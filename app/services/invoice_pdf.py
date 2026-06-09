from __future__ import annotations

import io
import logging
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether,
)

from app.models.invoice import Invoice
from app.models.invoice_settings import InvoiceSettings
from app.services.invoice_tax import amount_in_words

logger = logging.getLogger(__name__)


# ── Style + helpers ───────────────────────────────────────────────────


_BASE = getSampleStyleSheet()


def _style(name: str, **kwargs) -> ParagraphStyle:
    return ParagraphStyle(name=name, parent=_BASE["Normal"], **kwargs)


_S_TITLE = _style("title", fontName="Helvetica-Bold", fontSize=16, alignment=TA_RIGHT, textColor=colors.HexColor("#1a1a1a"))
_S_LABEL = _style("label", fontName="Helvetica-Bold", fontSize=8, textColor=colors.grey)
_S_VALUE = _style("value", fontName="Helvetica", fontSize=9, leading=12)
_S_VALUE_BOLD = _style("valuebold", fontName="Helvetica-Bold", fontSize=9, leading=12)
_S_HEADING = _style("heading", fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=colors.HexColor("#1a1a1a"))
_S_RIGHT = _style("right", fontName="Helvetica", fontSize=9, alignment=TA_RIGHT, leading=12)
_S_RIGHT_BOLD = _style("rightb", fontName="Helvetica-Bold", fontSize=10, alignment=TA_RIGHT, leading=14)
_S_FOOTER = _style("footer", fontName="Helvetica-Oblique", fontSize=7, textColor=colors.grey, alignment=TA_CENTER)


def _fmt_money(d: Decimal) -> str:
    """Indian number formatting with ₹ prefix: ₹ 1,23,456.78"""
    s = f"{d:,.2f}"  # US-style 1,234.56
    # Convert to Indian groupings: 1,23,456.78
    if "." in s:
        int_part, dec_part = s.split(".")
    else:
        int_part, dec_part = s, "00"
    int_part = int_part.replace(",", "")
    sign = ""
    if int_part.startswith("-"):
        sign = "-"
        int_part = int_part[1:]
    if len(int_part) > 3:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        # group the rest in 2s from the right
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        int_part = ",".join(groups) + "," + last3
    return f"{sign}₹ {int_part}.{dec_part}"


def _para(text: str, style: ParagraphStyle = _S_VALUE) -> Paragraph:
    # Escape any < > & that could confuse ReportLab's mini-XML parser
    safe = (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(safe, style)


def _validate_compliance(invoice: Invoice, settings: InvoiceSettings) -> list[str]:
    """Pre-flight check. Returns a list of human-readable error strings
    for missing GST-mandatory fields. Service raises BadRequestError
    enumerating them rather than silently rendering a non-compliant PDF.
    """
    errors: list[str] = []
    for field in ("legal_name", "gstin", "address_line1", "city", "pincode", "state_name", "state_code"):
        if not getattr(settings, field, None):
            errors.append(f"Supplier {field} is required")
    if not invoice.customer_name:
        errors.append("Customer name is required")
    if not invoice.invoice_number:
        errors.append("Invoice number is required")
    if not invoice.invoice_date:
        errors.append("Invoice date is required")
    if not invoice.line_items:
        errors.append("At least one line item is required")
    for i, li in enumerate(invoice.line_items, 1):
        if not li.get("hsn_sac"):
            errors.append(f"Line {i}: HSN/SAC code is required")
        if not li.get("description"):
            errors.append(f"Line {i}: description is required")
    return errors


# ── Render ────────────────────────────────────────────────────────────


def _header_table(settings: InvoiceSettings, invoice: Invoice, logo_bytes: Optional[bytes]) -> Table:
    # Left cell: logo (if available)
    if logo_bytes:
        try:
            img = Image(io.BytesIO(logo_bytes), width=40 * mm, height=20 * mm, kind="proportional")
            left = img
        except Exception:
            logger.warning("PDF render: failed to load logo image, skipping")
            left = _para(settings.legal_name, _S_HEADING)
    else:
        left = _para(settings.legal_name, _S_HEADING)

    # Right cell: TAX INVOICE + number + date
    right_rows = [
        [_para("TAX INVOICE", _S_TITLE)],
        [_para(f"<b>Invoice #</b> {invoice.invoice_number}", _S_RIGHT)],
        [_para(f"<b>Date</b> {invoice.invoice_date.isoformat()}", _S_RIGHT)],
    ]
    if invoice.due_date:
        right_rows.append([_para(f"<b>Due</b> {invoice.due_date.isoformat()}", _S_RIGHT)])
    right_table = Table(right_rows, colWidths=[80 * mm])
    right_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    t = Table([[left, right_table]], colWidths=[100 * mm, 80 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _from_billto_block(settings: InvoiceSettings, invoice: Invoice) -> Table:
    def _addr(*lines: Optional[str]) -> str:
        return "<br/>".join(l for l in lines if l)

    from_html = (
        f"<b>{settings.legal_name}</b><br/>"
        f"{_addr(settings.address_line1, settings.address_line2)}<br/>"
        f"{settings.city} - {settings.pincode}<br/>"
        f"{settings.state_name} (State Code: {settings.state_code})<br/>"
        f"<b>GSTIN:</b> {settings.gstin}"
        f"<br/><b>PAN:</b> {settings.pan}"
    )
    if settings.email:
        from_html += f"<br/>Email: {settings.email}"
    if settings.phone:
        from_html += f"<br/>Phone: {settings.phone}"

    to_lines = [f"<b>{invoice.customer_name}</b>"]
    if invoice.customer_address:
        to_lines.append(invoice.customer_address.replace("\n", "<br/>"))
    if invoice.customer_state_name:
        sc = invoice.customer_state_code or ""
        to_lines.append(f"{invoice.customer_state_name}{' (State Code: ' + sc + ')' if sc else ''}")
    if invoice.customer_gstin:
        to_lines.append(f"<b>GSTIN:</b> {invoice.customer_gstin}")
    else:
        to_lines.append("<i>Unregistered customer (B2C)</i>")
    if invoice.customer_email:
        to_lines.append(f"Email: {invoice.customer_email}")
    if invoice.customer_phone:
        to_lines.append(f"Phone: {invoice.customer_phone}")
    to_html = "<br/>".join(to_lines)

    inner = Table(
        [
            [_para("FROM", _S_LABEL), _para("BILL TO", _S_LABEL)],
            [_para(from_html, _S_VALUE), _para(to_html, _S_VALUE)],
        ],
        colWidths=[90 * mm, 90 * mm],
    )
    inner.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f5")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d4d4d8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e4e4e7")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return inner


def _line_items_table(invoice: Invoice) -> Table:
    headers = ["Sr.", "Description", "HSN/SAC", "Qty", "Rate (₹)", "Amount (₹)"]
    rows = [headers]
    for i, li in enumerate(invoice.line_items, 1):
        amount = Decimal(str(li.get("amount") or "0"))
        rate = Decimal(str(li.get("rate") or "0"))
        rows.append([
            str(i),
            _para(str(li.get("description") or ""), _S_VALUE),
            str(li.get("hsn_sac") or ""),
            str(li.get("qty") or ""),
            f"{rate:,.2f}",
            f"{amount:,.2f}",
        ])
    t = Table(rows, colWidths=[12 * mm, 80 * mm, 22 * mm, 16 * mm, 25 * mm, 25 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#27272a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e4e4e7")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#a1a1aa")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _tax_block(invoice: Invoice) -> Table:
    rate = Decimal(str(invoice.tax_rate))
    rows = [
        ["Subtotal", _fmt_money(Decimal(str(invoice.subtotal)))],
    ]
    if invoice.tax_split == "cgst_sgst":
        half = rate / Decimal("2")
        rows.append([f"CGST @ {half}%", _fmt_money(Decimal(str(invoice.cgst_amount)))])
        rows.append([f"SGST @ {half}%", _fmt_money(Decimal(str(invoice.sgst_amount)))])
    else:
        rows.append([f"IGST @ {rate}%", _fmt_money(Decimal(str(invoice.igst_amount)))])
    rows.append(["Total Tax", _fmt_money(Decimal(str(invoice.total_tax)))])
    rows.append(["Grand Total", _fmt_money(Decimal(str(invoice.grand_total)))])

    t = Table(rows, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    style = [
        ("FONTNAME", (0, 0), (-1, -2), "Helvetica"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, -2), (-1, -2), 0.5, colors.black),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, colors.black),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f4f4f5")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def _bank_block(settings: InvoiceSettings) -> Optional[Table]:
    if not (settings.bank_account_number or settings.bank_name):
        return None
    parts = []
    if settings.bank_account_name:
        parts.append(f"<b>Account Name:</b> {settings.bank_account_name}")
    if settings.bank_account_number:
        parts.append(f"<b>A/c No:</b> {settings.bank_account_number}")
    if settings.bank_ifsc:
        parts.append(f"<b>IFSC:</b> {settings.bank_ifsc}")
    if settings.bank_name:
        parts.append(f"<b>Bank:</b> {settings.bank_name}")
    if settings.bank_branch:
        parts.append(f"<b>Branch:</b> {settings.bank_branch}")

    body = "<br/>".join(parts)
    t = Table(
        [[_para("BANK DETAILS", _S_LABEL)], [_para(body, _S_VALUE)]],
        colWidths=[180 * mm],
    )
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d4d4d8")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f5")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _signature_block(settings: InvoiceSettings, sig_bytes: Optional[bytes]) -> Table:
    if sig_bytes:
        try:
            sig_img = Image(io.BytesIO(sig_bytes), width=40 * mm, height=20 * mm, kind="proportional")
            sig = sig_img
        except Exception:
            logger.warning("PDF render: failed to load signature image, skipping")
            sig = Spacer(40 * mm, 20 * mm)
    else:
        sig = Spacer(40 * mm, 20 * mm)
    t = Table(
        [
            [sig],
            [_para(f"For <b>{settings.legal_name}</b><br/>Authorized Signatory", _S_RIGHT)],
        ],
        colWidths=[60 * mm],
        hAlign="RIGHT",
    )
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def render_invoice_pdf(
    *,
    invoice: Invoice,
    settings: InvoiceSettings,
    logo_bytes: Optional[bytes] = None,
    signature_bytes: Optional[bytes] = None,
) -> bytes:
    """Render a single-invoice PDF and return bytes.

    Caller responsible for fetching logo + signature bytes from storage
    (or passing None to skip them). Raises ValueError listing all
    missing GST-mandatory fields if pre-flight fails — service
    converts to BadRequestError before the API responds.
    """
    errors = _validate_compliance(invoice, settings)
    if errors:
        raise ValueError("Invoice not GST-compliant: " + "; ".join(errors))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Invoice {invoice.invoice_number}",
    )

    story = []
    story.append(_header_table(settings, invoice, logo_bytes))
    story.append(Spacer(1, 6 * mm))
    story.append(_from_billto_block(settings, invoice))
    story.append(Spacer(1, 6 * mm))
    story.append(_line_items_table(invoice))
    story.append(Spacer(1, 6 * mm))
    story.append(_tax_block(invoice))
    story.append(Spacer(1, 4 * mm))
    # Amount in words — mandatory per GST
    story.append(_para(
        f"<b>Amount in words:</b> {amount_in_words(Decimal(str(invoice.grand_total)))}",
        _S_VALUE,
    ))
    story.append(Spacer(1, 6 * mm))

    bank = _bank_block(settings)
    if bank:
        story.append(bank)
        story.append(Spacer(1, 4 * mm))

    if invoice.notes:
        story.append(_para(f"<b>Notes:</b> {invoice.notes}", _S_VALUE))
        story.append(Spacer(1, 2 * mm))
    if invoice.terms:
        story.append(_para(f"<b>Terms:</b> {invoice.terms}", _S_VALUE))
        story.append(Spacer(1, 6 * mm))

    story.append(KeepTogether(_signature_block(settings, signature_bytes)))
    story.append(Spacer(1, 6 * mm))
    story.append(_para(
        "This is a computer-generated invoice and does not require a physical signature.",
        _S_FOOTER,
    ))

    doc.build(story)
    return buf.getvalue()
