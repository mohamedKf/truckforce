"""
delivery_pdf.py  —  Generate a delivery confirmation PDF using ReportLab.

Usage:
    from core.delivery_pdf import generate_delivery_pdf
    pdf_bytes = generate_delivery_pdf(confirmation)

Hebrew/Arabic support:
    Put DejaVuSans.ttf (and optionally DejaVuSans-Bold.ttf) inside
    core/fonts/ — the repo-bundled font is found first, so the PDF
    renders Hebrew on any server. RTL text is reordered with
    python-bidi (and Arabic letters joined with arabic-reshaper)
    when those packages are installed; without them the text still
    renders, just unshaped.
"""

import io
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

# ── Register a Hebrew-capable font ────────────────────────────────────────────
# The repo-bundled font comes FIRST so this works on Railway (the container
# ships no system fonts). System paths remain as fallbacks for local dev.
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_PATHS = [
    os.path.join(_HERE, 'fonts', 'DejaVuSans.ttf'),
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    'C:/Windows/Fonts/Arial.ttf',
    'C:/Windows/Fonts/arial.ttf',
]
_BOLD_PATHS = [
    os.path.join(_HERE, 'fonts', 'DejaVuSans-Bold.ttf'),
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
]

BODY_FONT = 'Helvetica'
for _fp in _FONT_PATHS:
    if os.path.exists(_fp):
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', _fp))
            BODY_FONT = 'DejaVu'
            break
        except Exception:
            continue

if BODY_FONT == 'DejaVu':
    _bold_name = 'DejaVu'
    for _bp in _BOLD_PATHS:
        if os.path.exists(_bp):
            try:
                pdfmetrics.registerFont(TTFont('DejaVu-Bold', _bp))
                _bold_name = 'DejaVu-Bold'
                break
            except Exception:
                continue
    # Map <b>/<i> markup onto the registered faces so Paragraph markup
    # keeps working with the TTF (without this, <b> tags can crash).
    addMapping('DejaVu', 0, 0, 'DejaVu')
    addMapping('DejaVu', 1, 0, _bold_name)
    addMapping('DejaVu', 0, 1, 'DejaVu')
    addMapping('DejaVu', 1, 1, _bold_name)

# ── RTL (Hebrew/Arabic) shaping ───────────────────────────────────────────────
# ReportLab lays glyphs left-to-right; bidi reordering makes Hebrew/Arabic
# read correctly. Both packages are optional — missing ones degrade
# gracefully instead of crashing PDF generation.
_RTL_RE    = re.compile(r'[\u0590-\u08FF]')
_ARABIC_RE = re.compile(r'[\u0600-\u06FF]')

try:
    from bidi.algorithm import get_display as _bidi_display
except Exception:                                     # pragma: no cover
    _bidi_display = None

try:
    import arabic_reshaper as _arabic_reshaper
except Exception:                                     # pragma: no cover
    _arabic_reshaper = None


def _rtl(text):
    """Return text reordered for correct RTL display inside the PDF."""
    s = str(text if text is not None else '')
    if not _RTL_RE.search(s):
        return s
    if _arabic_reshaper and _ARABIC_RE.search(s):
        try:
            s = _arabic_reshaper.reshape(s)
        except Exception:
            pass
    if _bidi_display:
        try:
            return _bidi_display(s)
        except Exception:
            return s
    return s


# ── Cloudinary-safe image source ──────────────────────────────────────────────
def _img_source(filefield):
    """Return something RLImage can draw from a FileField that may live on
    Cloudinary. Remote storages have no usable .path, so we download the
    bytes from the URL instead. Returns a path, a BytesIO, or None."""
    if not filefield:
        return None

    # The field may store a complete URL string (direct SDK uploads).
    name = str(getattr(filefield, 'name', '') or '')
    url = name if name.startswith('http') else None

    if url is None:
        # Local storage first (dev machines).
        try:
            p = filefield.path
            if p and os.path.exists(p):
                return p
        except Exception:
            pass
        try:
            url = filefield.url
        except Exception:
            return None

    if not url or not str(url).startswith('http'):
        return None
    try:
        import requests
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and resp.content:
            return io.BytesIO(resp.content)
    except Exception:
        pass
    return None


# Colours
PRIMARY   = colors.HexColor('#F5A623')   # amber
DARK      = colors.HexColor('#1A1A1A')
LIGHT_BG  = colors.HexColor('#F9F9F9')
BORDER    = colors.HexColor('#E0E0E0')
GREEN     = colors.HexColor('#4CAF50')
TEXT      = colors.HexColor('#333333')
MUTED     = colors.HexColor('#888888')


def _style(name, **kwargs):
    base = {
        'fontName': BODY_FONT,
        'fontSize': 10,
        'textColor': TEXT,
        'leading': 14,
    }
    base.update(kwargs)
    return ParagraphStyle(name, **base)


def generate_delivery_pdf(confirmation) -> bytes:
    """
    confirmation — a DeliveryConfirmation model instance with
    .stop, .signed_by_name, .signed_by_phone, .signed_by_email,
    .signature_image, .created_at
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title='Delivery Confirmation',
    )

    stop     = confirmation.stop
    schedule = stop.schedule
    driver   = schedule.driver
    truck    = schedule.truck

    # Try to load company settings
    try:
        from core.models import CompanySettings
        co = CompanySettings.objects.first()
    except Exception:
        co = None

    company_name  = co.company_name  if co else 'TruckForce'
    company_phone = co.phone         if co else ''
    company_email = getattr(co, 'email', '')

    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(f"<b>{_rtl(company_name)}</b>",
                  _style('co', fontSize=18, textColor=DARK)),
        Paragraph(
            f"{company_phone}<br/>{company_email}",
            _style('co_info', fontSize=9, textColor=MUTED, alignment=TA_RIGHT),
        ),
    ]]
    header_tbl = Table(header_data, colWidths=['60%', '40%'])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(header_tbl)
    story.append(HRFlowable(width='100%', thickness=2, color=PRIMARY, spaceAfter=10))

    # ── Title ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        _rtl('DELIVERY CONFIRMATION  /  אישור מסירה'),
        _style('title', fontSize=16, fontName=BODY_FONT,
               textColor=DARK, alignment=TA_CENTER, spaceAfter=2),
    ))

    conf_no = f"DC-{stop.id:05d}"
    signed_dt = confirmation.created_at.strftime('%d/%m/%Y  %H:%M') if confirmation.created_at else ''
    story.append(Paragraph(
        f"<font color='#888888'>Confirmation #: {conf_no}  |  Date: {signed_dt}</font>",
        _style('sub', fontSize=9, alignment=TA_CENTER, spaceAfter=16),
    ))

    # ── Info grid ────────────────────────────────────────────────────────────
    def info_row(label, value):
        return [
            Paragraph(f"<b>{_rtl(label)}</b>", _style('lbl', fontSize=9, textColor=MUTED)),
            Paragraph(_rtl(value) if value else '—', _style('val', fontSize=10)),
        ]

    info_data = [
        info_row('Site / אתר', stop.site_name),
        info_row('Address / כתובת', stop.address),
        info_row('Schedule date / תאריך', schedule.date.strftime('%d/%m/%Y')),
        info_row('Driver / נהג', driver.full_name),
        info_row('Vehicle / רכב', f"{truck.plate_number} — {truck.make} {truck.model}" if truck else '—'),
    ]
    if stop.actual_arrival:
        info_data.append(info_row('Arrived at / הגעה', stop.actual_arrival.strftime('%H:%M')))
    if stop.notes:
        info_data.append(info_row('Notes / הערות', stop.notes))

    info_tbl = Table(info_data, colWidths=[4*cm, None])
    info_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [LIGHT_BG, colors.white]),
        ('BOX', (0,0), (-1,-1), 0.5, BORDER),
        ('INNERGRID', (0,0), (-1,-1), 0.25, BORDER),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 16))

    # ── Delivery photos ──────────────────────────────────────────────────────
    photos = list(stop.photos.all()[:6])  # max 6 thumbnails
    if photos:
        story.append(Paragraph(f"<b>{_rtl('Delivery Photos / תמונות מסירה')}</b>",
                               _style('sec', fontSize=11, textColor=DARK, spaceAfter=6)))
        photo_cells = []
        row = []
        for i, photo in enumerate(photos):
            try:
                src = _img_source(photo.image)
                if src is None:
                    raise ValueError('no image source')
                img = RLImage(src, width=4.5*cm, height=3.5*cm)
                img.hAlign = 'CENTER'
                row.append(img)
            except Exception:
                row.append(Paragraph('—', _style('ph')))
            if len(row) == 3:
                photo_cells.append(row)
                row = []
        if row:
            while len(row) < 3:
                row.append('')
            photo_cells.append(row)

        photo_tbl = Table(photo_cells, colWidths=[5.5*cm, 5.5*cm, 5.5*cm])
        photo_tbl.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(photo_tbl)
        story.append(Spacer(1, 16))

    # ── Signature section ────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER, spaceAfter=10))
    story.append(Paragraph(f"<b>{_rtl('Recipient Signature / חתימת מקבל')}</b>",
                           _style('sec', fontSize=11, textColor=DARK, spaceAfter=8)))

    sig_left  = []
    sig_right = []

    # Signature image — drawn from URL bytes (Cloudinary) or local path;
    # mask='auto' keeps the transparent-background PNG clean on white.
    try:
        src = _img_source(confirmation.signature_image)
        if src is None:
            raise ValueError('no signature source')
        sig_img = RLImage(src, width=7*cm, height=3*cm, mask='auto')
        sig_img.hAlign = 'LEFT'
        sig_left.append(sig_img)
    except Exception:
        sig_left.append(Paragraph('[Signature]', _style('ph', textColor=MUTED)))

    sig_left.append(Spacer(1, 4))
    sig_left.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    sig_left.append(Paragraph(
        f"<b>{_rtl(confirmation.signed_by_name)}</b>",
        _style('signer', fontSize=11, textColor=DARK),
    ))
    sig_left.append(Paragraph(
        f"<font color='#888888'>{_rtl('Received by / התקבל על ידי')}</font>",
        _style('rcv', fontSize=9, textColor=MUTED),
    ))

    # Stamp area on right
    sig_right.append(Spacer(1, 0.5*cm))
    stamp_inner = Table(
        [[Paragraph(f"<font color='#CCCCCC'>{_rtl('חותמת / Stamp')}</font>",
                    _style('stamp', fontSize=9, alignment=TA_CENTER))]],
        colWidths=[6*cm], rowHeights=[3.5*cm],
    )
    stamp_inner.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, BORDER),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    sig_right.append(stamp_inner)

    sig_tbl = Table(
        [[sig_left, sig_right]],
        colWidths=['55%', '45%'],
    )
    sig_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(sig_tbl)
    story.append(Spacer(1, 20))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER, spaceBefore=4))
    story.append(Paragraph(
        f'<font color="#AAAAAA" size="8">Generated by TruckForce  •  {_rtl(company_name)}  •  {signed_dt}</font>',
        _style('footer', fontSize=8, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4),
    ))

    doc.build(story)
    return buf.getvalue()