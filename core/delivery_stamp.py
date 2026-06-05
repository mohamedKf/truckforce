"""
delivery_stamp.py — Stamp a hand-drawn signature onto an existing
delivery-note PDF at a driver-chosen position.

The driver positions a signature box on the rendered PDF in the app and
sends back the page index plus a *normalized* rectangle: the top-left
corner (nx, ny) and the size (nw, nh), each a fraction (0..1) of the page
width/height, top-left origin. We map that straight onto the PDF page's
point dimensions and drop the signature PNG in. Working in fractions keeps
this resolution-independent — there is no DPI or render-scale guesswork,
because PyMuPDF's coordinate space is also top-left origin.

Usage:
    from core.delivery_stamp import stamp_signature_on_note
    signed_bytes = stamp_signature_on_note(
        note_bytes, signature_png_bytes,
        page=0, nx=0.55, ny=0.82, nw=0.35, nh=0.10,
    )
"""

import io
import fitz  # PyMuPDF


def stamp_signature_on_note(
    note_bytes: bytes,
    signature_png_bytes: bytes,
    page: int = 0,
    nx: float = 0.55,
    ny: float = 0.82,
    nw: float = 0.35,
    nh: float = 0.10,
) -> bytes:
    """Return new PDF bytes with the signature stamped on the chosen page.

    Coordinates are clamped into the page with a sane minimum size, so a
    malformed payload can never push the image off-page or invert the rect.
    On any failure we return the original bytes unchanged — a stamping
    glitch must never lose the delivery note.
    """
    try:
        doc = fitz.open(stream=note_bytes, filetype='pdf')
    except Exception:
        return note_bytes

    try:
        if doc.page_count == 0:
            return note_bytes

        idx = max(0, min(int(page), doc.page_count - 1))
        pg = doc[idx]

        # Page size in points (PyMuPDF uses a top-left origin, y downwards).
        W = pg.rect.width
        H = pg.rect.height

        # Clamp the normalized rect into the page and enforce a minimum size.
        nx = min(max(nx, 0.0), 0.98)
        ny = min(max(ny, 0.0), 0.98)
        nw = min(max(nw, 0.03), 1.0 - nx)
        nh = min(max(nh, 0.02), 1.0 - ny)

        rect = fitz.Rect(nx * W, ny * H, (nx + nw) * W, (ny + nh) * H)

        pg.insert_image(
            rect,
            stream=signature_png_bytes,
            overlay=True,
            keep_proportion=True,  # never distort the signature
        )

        out = io.BytesIO()
        doc.save(out, deflate=True)
        return out.getvalue()
    except Exception:
        return note_bytes
    finally:
        try:
            doc.close()
        except Exception:
            pass


def append_pdf(base_bytes: bytes, extra_bytes: bytes) -> bytes:
    """Merge two PDFs: returns base + extra appended as additional page(s).
    Used to attach the generated confirmation page after the signed
    delivery note, so the client receives ONE document with everything.
    On any failure, returns the base unchanged — merging must never cost
    us the signed note."""
    base = None
    extra = None
    try:
        import fitz  # PyMuPDF
        base = fitz.open(stream=base_bytes, filetype='pdf')
        extra = fitz.open(stream=extra_bytes, filetype='pdf')
        base.insert_pdf(extra)
        out = io.BytesIO()
        base.save(out, deflate=True)
        return out.getvalue()
    except Exception:
        return base_bytes
    finally:
        for _d in (base, extra):
            try:
                if _d is not None:
                    _d.close()
            except Exception:
                pass