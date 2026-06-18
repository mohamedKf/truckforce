"""
ai_delivery_sheet.py — Read a delivery sheet (photo OR PDF) and extract the
driver + stops as structured data, using OpenAI's vision model.

Used by ParseDeliverySheetView. PDFs are rasterized to images server-side
(PyMuPDF) since the vision API takes images. Everything is best-effort: on any
failure (no key, bad file, model error) it returns a dict with an 'error' key
and empty fields rather than raising, so the desktop import popup degrades
gracefully.

Server requirements (add to the backend's requirements.txt):
    openai
    pymupdf
"""

import base64
import json

from django.conf import settings


# What we ask the model to return. Kept strict so the desktop can map it
# straight onto editable stop rows.
_PROMPT = (
    "You are a careful data-extraction assistant for a logistics company in "
    "Israel. The image is a delivery route sheet (often photographed at an "
    "angle or rotated, and partly handwritten). Read it as-is and return ONLY "
    "a JSON object with EXACTLY this shape:\n"
    "{\n"
    '  "date": "YYYY-MM-DD or empty string",\n'
    '  "drivers": [\n'
    "    {\n"
    '      "driver": {"name": "", "id_number": "", "phone": "", "route": ""},\n'
    '      "stops": [\n'
    '        {"site_name": "", "address": "", "city": "", "contact_name": "", '
    '"contact_phone": "", "package_count": 0, "notes": ""}\n'
    "      ]\n"
    "    }\n"
    "  ]\n"
    "}\n"
    "CRITICAL RULES:\n"
    "1. ONE STOP PER CUSTOMER, NOT PER ROW. The sheet has a customer/sequence "
    "number column (e.g. 1, 2, 3). Each distinct numbered customer is exactly "
    "ONE stop. A single customer often spans several table rows (one row per "
    "parcel, barcode or document line) — MERGE those rows into the SAME stop "
    "and add up their quantities into package_count. Do NOT emit a separate "
    "stop for each row. If the sheet shows 3 customers, return 3 stops.\n"
    "2. READ EACH STOP'S OWN DATA. site_name, contact_phone, city and address "
    "must come from THAT customer's own rows. NEVER copy a name, phone, city or "
    "address from one stop onto another. If two stops really share a value, "
    "only repeat it when you can clearly read it on both.\n"
    "3. LEAVE BLANK WHEN UNSURE. If a field is empty, unreadable, or you are "
    "guessing, use \"\" (or 0 for package_count). An empty field is correct; an "
    "invented or copied-from-elsewhere value is a serious error.\n"
    "4. LOCATION: put the town/city (e.g. מגדל שמס, חיפה, קריות) in city and any "
    "street in address. Copy them EXACTLY as written. If only a town is given, "
    "fill city and leave address empty. NEVER fabricate a street — the system "
    "resolves the real address afterwards from what you give.\n"
    "5. The recipient/business name goes in site_name; a contact person (if "
    "separate) in contact_name; the recipient's phone in contact_phone.\n"
    "6. Keep Hebrew text in Hebrew. Keep stops in the order they appear.\n"
    "7. Return ONE entry in \"drivers\" per route/driver (the driver name, ID and "
    "route number are in the sheet header). Most sheets have a single driver.\n"
    "Return ONLY the JSON object — no markdown, no commentary."
)


def _empty(error=None):
    return {"error": error, "date": "", "drivers": []}


def _client():
    """Build an OpenAI client, or None if the key/library isn't available."""
    key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception as e:
        print(f"[AI-SHEET] openai library unavailable: {e}", flush=True)
        return None


def _pdf_to_pngs(pdf_bytes, max_pages=12, dpi=200):
    """Rasterize each PDF page to PNG bytes via PyMuPDF."""
    import fitz  # PyMuPDF
    pages = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(dpi=dpi)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


def _is_pdf(file_bytes, content_type, filename):
    if file_bytes[:4] == b"%PDF":
        return True
    if "pdf" in (content_type or "").lower():
        return True
    return (filename or "").lower().endswith(".pdf")


def parse_delivery_sheet(file_bytes, content_type="", filename=""):
    """Parse a delivery sheet into {driver, date, stops}. Never raises."""
    client = _client()
    if client is None:
        return _empty("no_openai_key")

    try:
        if _is_pdf(file_bytes, content_type, filename):
            images = _pdf_to_pngs(file_bytes)
        else:
            images = [file_bytes]  # already an image
        if not images:
            return _empty("no_pages")

        content = [{"type": "text", "text": _PROMPT}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })

        model = (getattr(settings, "OPENAI_VISION_MODEL", "") or "gpt-4o").strip()
        # GPT-5.x / o-series are reasoning models: they reject a custom
        # `temperature` and want `max_completion_tokens` instead of `max_tokens`.
        # Classic models (gpt-4o, gpt-4o-mini) use the old params. Pick per model
        # so the same code works whichever you set in OPENAI_VISION_MODEL.
        is_reasoning = model.lower().startswith(("gpt-5", "o1", "o3", "o4"))
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        if is_reasoning:
            kwargs["max_completion_tokens"] = 6000
        else:
            kwargs["temperature"] = 0
            kwargs["max_tokens"] = 6000
        resp = client.chat.completions.create(**kwargs)
        try:
            u = resp.usage
            print(f"[AI-SHEET] model={model} pages={len(images)} "
                  f"in={getattr(u,'prompt_tokens','?')} "
                  f"out={getattr(u,'completion_tokens','?')} "
                  f"total={getattr(u,'total_tokens','?')}", flush=True)
        except Exception:
            pass
        raw = (resp.choices[0].message.content or "{}").strip()
        data = json.loads(raw)

        # Normalize to {date, drivers:[{driver, stops:[...]}]} so callers can
        # rely on the shape regardless of small model deviations.
        def _norm_stop(s):
            try:
                pkg = int(s.get("package_count") or 0)
            except (TypeError, ValueError):
                pkg = 0
            return {
                "site_name": str(s.get("site_name", "") or ""),
                "address": str(s.get("address", "") or ""),
                "city": str(s.get("city", "") or ""),
                "contact_name": str(s.get("contact_name", "") or ""),
                "contact_phone": str(s.get("contact_phone", "") or ""),
                "package_count": pkg,
                "notes": str(s.get("notes", "") or ""),
            }

        out_drivers = []
        for entry in (data.get("drivers") or []):
            if not isinstance(entry, dict):
                continue
            d = entry.get("driver") or {}
            stops = [_norm_stop(s) for s in (entry.get("stops") or [])
                     if isinstance(s, dict)]
            out_drivers.append({
                "driver": {
                    "name": str(d.get("name", "") or ""),
                    "id_number": str(d.get("id_number", "") or ""),
                    "phone": str(d.get("phone", "") or ""),
                    "route": str(d.get("route", "") or ""),
                },
                "stops": stops,
            })

        return {
            "error": None,
            "date": str(data.get("date", "") or ""),
            "drivers": out_drivers,
        }
    except Exception as e:
        print(f"[AI-SHEET] parse failed: {e}", flush=True)
        return _empty(str(e))


def match_driver(driver_info):
    """Best-effort match of extracted driver info to a real Driver record.
    Tries Israeli ID first (unique), then phone, then name. Returns a small
    dict or None."""
    from .models import Driver
    info = driver_info or {}
    idn = (info.get("id_number") or "").strip()
    phone = (info.get("phone") or "").strip()
    name = (info.get("name") or "").strip()

    found = None
    if idn:
        found = Driver.objects.filter(id_number=idn, is_active=True).first()
    if not found and phone:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) >= 7:
            # Match on the last 7 digits to ignore +972 / leading-0 differences.
            found = Driver.objects.filter(
                phone__icontains=digits[-7:], is_active=True).first()
    if not found and name:
        found = Driver.objects.filter(
            full_name__icontains=name, is_active=True).first()

    if not found:
        return None
    return {
        "id": found.id,
        "full_name": found.full_name,
        "id_number": found.id_number,
        "phone": found.phone,
    }