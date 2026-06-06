"""
invoice_pdf.py — TruckForce-branded billing documents (Hebrew, RTL).

Generates the חשבון עסקה (proforma / billing request) PDF with the
hauling company's name + logo and a "Powered by TruckForce" footer.
This document is NOT a tax document, so TruckForce fully owns its
design; the legal חשבונית מס / קבלה come from Green Invoice and we
only mirror their PDFs.

Reuses the font registration, RTL shaping, and Cloudinary-safe image
loading already battle-tested in delivery_pdf.py.

Usage:
    from core.invoice_pdf import generate_invoice_pdf
    pdf_bytes = generate_invoice_pdf(invoice)
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, HRFlowable,
)

# Same font + RTL + image plumbing as the delivery confirmation —
# one source of truth, no duplication.
from .delivery_pdf import BODY_FONT, _rtl, _img_source, _style

PRIMARY  = colors.HexColor('#F5A623')
DARK     = colors.HexColor('#1A1A1A')
LIGHT_BG = colors.HexColor('#F9F9F9')
BORDER   = colors.HexColor('#E0E0E0')
TEXT     = colors.HexColor('#333333')
MUTED    = colors.HexColor('#888888')


def _money(amount) -> str:
    """Format Decimal money as ₪ with thousands separators."""
    try:
        return f"₪{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "₪0.00"


def generate_invoice_pdf(invoice) -> bytes:
    """Render a branded חשבון עסקה for the given Invoice instance.
    Expects .client / snapshot fields / .lines / totals on the invoice."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.6*cm, bottomMargin=1.6*cm,
        title='חשבון עסקה',
    )

    try:
        from core.models import CompanySettings
        co = CompanySettings.objects.first()
    except Exception:
        co = None

    company_name  = (co.company_name if co else '') or 'TruckForce'
    company_phone = (co.phone if co else '') or ''
    company_email = (getattr(co, 'email', '') if co else '') or ''
    company_addr  = (getattr(co, 'address', '') if co else '') or ''

    story = []

    # ── Header: logo + company identity ─────────────────────────────────
    logo_cell = ''
    try:
        if co is not None and co.company_logo:
            src = _img_source(co.company_logo)
            if src is not None:
                logo = RLImage(src, width=3.2*cm, height=3.2*cm,
                               kind='proportional', mask='auto')
                logo.hAlign = 'LEFT'
                logo_cell = logo
    except Exception:
        logo_cell = ''

    identity = [
        Paragraph(f"<b>{_rtl(company_name)}</b>",
                  _style('co', fontSize=17, textColor=DARK,
                         alignment=TA_RIGHT)),
    ]
    contact_bits = " • ".join(b for b in [company_phone, company_email] if b)
    if contact_bits:
        identity.append(Paragraph(contact_bits,
                        _style('coc', fontSize=9, textColor=MUTED,
                               alignment=TA_RIGHT)))
    if company_addr:
        identity.append(Paragraph(_rtl(company_addr),
                        _style('coa', fontSize=9, textColor=MUTED,
                               alignment=TA_RIGHT)))

    head = Table([[logo_cell, identity]], colWidths=['25%', '75%'])
    head.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(head)
    story.append(HRFlowable(width='100%', thickness=2.2, color=PRIMARY,
                            spaceBefore=8, spaceAfter=12))

    # ── Document title bar ───────────────────────────────────────────────
    number_txt = f"{invoice.number}" if invoice.number else "—"
    title_tbl = Table(
        [[Paragraph(f"<b>{_rtl('חשבון עסקה')} / {number_txt}</b>",
                    _style('ttl', fontSize=15, textColor=colors.white,
                           alignment=TA_CENTER))]],
        colWidths=['100%'], rowHeights=[0.95*cm],
    )
    title_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
    ]))
    story.append(title_tbl)

    issue = invoice.issue_date or date.today()
    story.append(Paragraph(
        f"<font color='#888888'>{_rtl('תאריך')}: {issue.strftime('%d/%m/%Y')}</font>",
        _style('dt', fontSize=9, alignment=TA_CENTER, spaceBefore=4,
               spaceAfter=12),
    ))

    # ── Bill-to block (snapshot fields first, live client as fallback) ──
    bill_name = invoice.client_name or (invoice.client.name if invoice.client_id else '')
    bill_tax  = invoice.client_tax_id or (invoice.client.tax_id if invoice.client_id else '')
    bill_addr = invoice.client_address or (invoice.client.address if invoice.client_id else '')

    bill_lines = [Paragraph(f"<b>{_rtl('לכבוד')}: {_rtl(bill_name)}</b>",
                            _style('bl', fontSize=11, alignment=TA_RIGHT))]
    if bill_tax:
        bill_lines.append(Paragraph(f"{_rtl('ח.פ / עוסק')}: {bill_tax}",
                          _style('bl2', fontSize=9, textColor=MUTED,
                                 alignment=TA_RIGHT)))
    if bill_addr:
        bill_lines.append(Paragraph(_rtl(bill_addr),
                          _style('bl3', fontSize=9, textColor=MUTED,
                                 alignment=TA_RIGHT)))
    bill_tbl = Table([[bill_lines]], colWidths=['100%'])
    bill_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(bill_tbl)
    story.append(Spacer(1, 14))

    # ── Lines table (RTL column order, like every Israeli invoice) ──────
    hdr_style = _style('h', fontSize=9, textColor=DARK, alignment=TA_CENTER)
    cell_r    = _style('cr', fontSize=10, alignment=TA_RIGHT)
    cell_c    = _style('cc', fontSize=10, alignment=TA_CENTER)

    rows = [[
        Paragraph(f"<b>{_rtl('סה' + chr(34) + 'כ')}</b>", hdr_style),
        Paragraph(f"<b>{_rtl('מחיר ליחידה')}</b>", hdr_style),
        Paragraph(f"<b>{_rtl('כמות')}</b>", hdr_style),
        Paragraph(f"<b>{_rtl('תיאור')}</b>", hdr_style),
    ]]
    for line in invoice.lines.all():
        qty = line.quantity
        qty_txt = f"{qty:.2f}".rstrip('0').rstrip('.') if qty is not None else '1'
        rows.append([
            Paragraph(_money(line.line_total), cell_c),
            Paragraph(_money(line.unit_price), cell_c),
            Paragraph(qty_txt, cell_c),
            Paragraph(_rtl(line.description), cell_r),
        ])

    lines_tbl = Table(rows, colWidths=[3.2*cm, 3.2*cm, 2.0*cm, None],
                      repeatRows=1)
    lines_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EFEFEF')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(lines_tbl)
    story.append(Spacer(1, 10))

    # ── Totals box (bottom-left like the classic layout) ─────────────────
    vat_label = _rtl('מע"מ') + f" {invoice.vat_rate:.0f}%"
    if invoice.vat_exempt:
        vat_label = _rtl('מע"מ — פטור')
    tot_rows = [
        [Paragraph(_money(invoice.subtotal), cell_c),
         Paragraph(_rtl('סה"כ לפני מע"מ'), cell_r)],
        [Paragraph(_money(invoice.vat_amount), cell_c),
         Paragraph(vat_label, cell_r)],
        [Paragraph(f"<b>{_money(invoice.total)}</b>",
                   _style('tt', fontSize=12, alignment=TA_CENTER,
                          textColor=DARK)),
         Paragraph(f"<b>{_rtl('סה' + chr(34) + 'כ לתשלום')}</b>",
                   _style('tl', fontSize=11, alignment=TA_RIGHT,
                          textColor=DARK))],
    ]
    tot_tbl = Table(tot_rows, colWidths=[3.6*cm, 4.6*cm], hAlign='LEFT')
    tot_tbl.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, BORDER),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#FFF3DC')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(tot_tbl)

    # Payment terms / notes
    terms = (invoice.client.payment_terms
             if invoice.client_id and invoice.client.payment_terms else '')
    if terms:
        story.append(Spacer(1, 8))
        story.append(Paragraph(
            f"{_rtl('תנאי תשלום')}: {_rtl(terms)}",
            _style('terms', fontSize=9, textColor=MUTED, alignment=TA_RIGHT)))
    if invoice.notes:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"{_rtl('הערות')}: {_rtl(invoice.notes)}",
            _style('nts', fontSize=9, textColor=MUTED, alignment=TA_RIGHT)))

    # Legal clarification — proforma is not a tax document.
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        _rtl('מסמך זה אינו חשבונית מס. חשבונית מס תופק עם התשלום.'),
        _style('legal', fontSize=8, textColor=MUTED, alignment=TA_CENTER)))

    # ── Footer ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER))
    story.append(Paragraph(
        f'<font color="#AAAAAA" size="8">Powered by TruckForce  •  '
        f'{_rtl(company_name)}  •  {issue.strftime("%d/%m/%Y")}</font>',
        _style('foot', fontSize=8, alignment=TA_CENTER, spaceBefore=4)))

    doc.build(story)
    return buf.getvalue()