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
    "You are a data-extraction assistant for a logistics company in Israel. "
    "This document is one or more delivery route sheets — usually one route "
    "sheet per driver. Read it and return ONLY a JSON object with EXACTLY "
    "this shape:\n"
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
    "Rules:\n"
    "- Return ONE entry in \"drivers\" per route/driver. The driver name, ID and "
    "route number are usually in the header of each sheet.\n"
    "- Each table row is one delivery stop. Put the recipient/business name in "
    "site_name, the contact person in contact_name, and the phone in contact_phone.\n"
    "- Text is usually Hebrew; keep names and addresses in their original language.\n"
    "- Capture location clues EXACTLY as written: street in address, city/town "
    "in city. If only a name and city are given, leave address empty. NEVER "
    "guess or fabricate a street address — the system resolves it afterwards.\n"
    "- Use empty strings and 0 where a value is missing. NEVER invent data.\n"
    "- package_count must be an integer (0 if unknown).\n"
    "- Keep stops in the order they appear.\n"
    "- Return ONLY the JSON object — no markdown fences, no commentary."
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


def _pdf_to_pngs(pdf_bytes, max_pages=12, dpi=170):
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
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
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