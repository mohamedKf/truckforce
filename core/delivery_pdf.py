"""
delivery_pdf.py  —  Generate a delivery confirmation PDF using ReportLab.

Usage:
    from core.delivery_pdf import generate_delivery_pdf
    pdf_bytes = generate_delivery_pdf(confirmation)
"""

import io
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

# ── Attempt to register Hebrew font ──────────────────────────────────────────
import os, django
_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
    'C:/Windows/Fonts/Arial.ttf',
    'C:/Windows/Fonts/arial.ttf',
]
BODY_FONT = 'Helvetica'
for _fp in _FONT_PATHS:
    if os.path.exists(_fp):
        try:
            pdfmetrics.registerFont(TTFont('DejaVu', _fp))
            BODY_FONT = 'DejaVu'
        except Exception:
            pass
        break

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
        Paragraph(f"<b>{company_name}</b>", _style('co', fontSize=18, textColor=DARK)),
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
        'DELIVERY CONFIRMATION  /  אישור מסירה',
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
            Paragraph(f"<b>{label}</b>", _style('lbl', fontSize=9, textColor=MUTED)),
            Paragraph(str(value or '—'), _style('val', fontSize=10)),
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
        story.append(Paragraph('<b>Delivery Photos / תמונות מסירה</b>',
                               _style('sec', fontSize=11, textColor=DARK, spaceAfter=6)))
        photo_cells = []
        row = []
        for i, photo in enumerate(photos):
            try:
                img = RLImage(photo.image.path, width=4.5*cm, height=3.5*cm)
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
    story.append(Paragraph('<b>Recipient Signature / חתימת מקבל</b>',
                           _style('sec', fontSize=11, textColor=DARK, spaceAfter=8)))

    sig_left  = []
    sig_right = []

    # Signature image
    try:
        sig_img = RLImage(confirmation.signature_image.path, width=7*cm, height=3*cm)
        sig_img.hAlign = 'LEFT'
        sig_left.append(sig_img)
    except Exception:
        sig_left.append(Paragraph('[Signature]', _style('ph', textColor=MUTED)))

    sig_left.append(Spacer(1, 4))
    sig_left.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    sig_left.append(Paragraph(
        f"<b>{confirmation.signed_by_name}</b>",
        _style('signer', fontSize=11, textColor=DARK),
    ))
    sig_left.append(Paragraph(
        f"<font color='#888888'>Received by / התקבל על ידי</font>",
        _style('rcv', fontSize=9, textColor=MUTED),
    ))

    # Stamp area on right
    sig_right.append(Spacer(1, 0.5*cm))
    stamp_inner = Table(
        [[Paragraph('<font color="#CCCCCC">חותמת / Stamp</font>',
                    _style('stamp', fontSize=9, alignment=TA_CENTER))]],
        colWidths=[6*cm], rowHeights=[3.5*cm],
    )
    stamp_inner.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, BORDER),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    sig_right.append(stamp_inner)

    from reportlab.platypus import KeepInFrame
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
        f'<font color="#AAAAAA" size="8">Generated by TruckForce  •  {company_name}  •  {signed_dt}</font>',
        _style('footer', fontSize=8, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4),
    ))

    doc.build(story)
    return buf.getvalue()