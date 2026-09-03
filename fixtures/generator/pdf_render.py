"""Deterministic fpdf2 rendering helpers for the golden-v2 PDF lane."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fixtures.generator.spec_v2 import SYMBOLS
from fpdf import FPDF

DEJAVU_SANS = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
A4_WIDTH = 210.0
A4_HEIGHT = 297.0
MARGIN = 15.0


@dataclass(frozen=True)
class RenderItem:
    """One table line whose amount is already expressed in major units."""

    description: str
    quantity: int
    unit_price: float
    amount: float


@dataclass(frozen=True)
class RenderCase:
    """Layout-independent data needed to render one invoice PDF."""

    case_id: str
    language: str
    layout: str
    supplier: str
    street: str
    city_line: str
    contact_phone: str
    contact_email: str
    iban: str
    bic: str
    number_label: str
    invoice_number: str | None
    date_label: str
    invoice_date: str | None
    currency_label: str
    currency: str | None
    currency_markers: str | None
    tax_label: str
    tax_id: str | None
    items: tuple[RenderItem, ...]
    subtotal: float
    vat_rate: float
    vat_amount: float
    total_label: str
    total_amount: float
    amount_style: str
    terms: str
    stamp: str
    order_ref: str
    delivery_note: str
    bill_to: str
    ship_to: str


@dataclass(frozen=True)
class FontPlan:
    """The resolved Unicode-capable font and whether currency glyphs are safe."""

    family: str
    unicode_symbols: bool


def resolve_font(pdf: FPDF) -> FontPlan:
    """Prefer DejaVuSans for €/£ and fall back to the built-in Helvetica font."""
    if DEJAVU_SANS.is_file():
        try:
            pdf.add_font(family="DejaVu", fname=str(DEJAVU_SANS))
            pdf.set_font("DejaVu", size=10)
        except Exception:
            pdf.set_font("Helvetica", size=10)
            return FontPlan("Helvetica", False)
        return FontPlan("DejaVu", True)
    pdf.set_font("Helvetica", size=10)
    return FontPlan("Helvetica", False)


def format_render_amount(value: float, style: str) -> str:
    """Import the frozen amount formatter lazily to avoid renderer-side truth logic."""
    from fixtures.generator.spec_v2 import format_amount

    return format_amount(value, style)


def currency_marker(currency: str | None, font: FontPlan) -> str:
    """Return a compact prefix for amounts, using ISO codes in the fallback path."""
    if currency is None:
        return ""
    symbol = SYMBOLS.get(currency, currency)
    if font.unicode_symbols and currency in {"EUR", "GBP"}:
        return f"{symbol} "
    return f"{currency} "


def _line(pdf: FPDF, x: float, y: float, text: str, size: float = 10) -> None:
    pdf.set_xy(x, y)
    pdf.set_font_size(size)
    pdf.cell(0, 5.5, text)


def _field(pdf: FPDF, x: float, y: float, label: str, value: str, size: float = 10) -> float:
    _line(pdf, x, y, f"{label}: {value}", size)
    return y + 5.5


def _dashed_line(pdf: FPDF, y: float) -> None:
    pdf.set_draw_color(150, 150, 150)
    pdf.set_line_width(0.25)
    pdf.set_dash_pattern(dash=1.4, gap=1.0)
    pdf.line(MARGIN, y, A4_WIDTH - MARGIN, y)
    pdf.set_dash_pattern()


def _watermark(pdf: FPDF) -> None:
    pdf.set_fill_color(242, 244, 247)
    pdf.rect(112, 175, 78, 42, style="F")
    pdf.rect(22, 230, 58, 24, style="F")


def _stamp(pdf: FPDF, stamp: str) -> None:
    pdf.set_text_color(185, 185, 185)
    pdf.set_font_size(25)
    with pdf.rotation(angle=38, x=148, y=248):
        pdf.set_xy(112, 244)
        pdf.cell(72, 10, stamp, align="C")
    pdf.set_text_color(0, 0, 0)


def _summary_row(pdf: FPDF, label: str, value: str, y: float) -> float:
    pdf.set_xy(120, y)
    pdf.cell(40, 5.5, label, align="R")
    pdf.set_xy(161, y)
    pdf.cell(34, 5.5, value, align="R")
    return y + 5.5


def _footer(pdf: FPDF, text: str, page_number: int = 1) -> None:
    pdf.set_y(-16)
    pdf.set_font_size(7.5)
    pdf.cell(0, 4, text)
    pdf.set_xy(-22, -11)
    pdf.cell(20, 4, f"Page {page_number}", align="R")


def _basic_layout(case: RenderCase, pdf: FPDF, font: FontPlan) -> None:
    y = MARGIN + 2
    y = _field(pdf, MARGIN, y, "Supplier", case.supplier)
    y = _field(pdf, MARGIN, y, "Address", f"{case.street}, {case.city_line}")
    y = _field(pdf, MARGIN, y, "Contact", f"{case.contact_phone} / {case.contact_email}")
    y = _field(pdf, MARGIN, y, "IBAN", case.iban)
    y = _field(pdf, MARGIN, y, "BIC", case.bic)
    y += 2
    if case.invoice_number is not None:
        y = _field(pdf, MARGIN, y, case.number_label, case.invoice_number)
    if case.invoice_date is not None:
        y = _field(pdf, MARGIN, y, case.date_label, case.invoice_date)
    if case.currency is not None:
        y = _field(pdf, MARGIN, y, case.currency_label, case.currency)
    if case.tax_id is not None:
        y = _field(pdf, MARGIN, y, case.tax_label, case.tax_id)
    y += 4
    _line(pdf, MARGIN, y, case.items[0].description)
    total_text = format_render_amount(case.total_amount, case.amount_style)
    _field(
        pdf, MARGIN, y + 8, case.total_label, f"{currency_marker(case.currency, font)}{total_text}"
    )
    _footer(pdf, case.terms)


def _header_block(
    case: RenderCase,
    pdf: FPDF,
    *,
    extended: bool,
) -> None:
    pdf.set_xy(MARGIN, MARGIN)
    pdf.set_font_size(14)
    pdf.cell(90, 7, case.supplier)
    pdf.set_xy(MARGIN, MARGIN + 8)
    pdf.set_font_size(9)
    pdf.multi_cell(85, 4.8, f"{case.street}\n{case.city_line}")
    contact = f"{case.contact_phone}\n{case.contact_email}\nIBAN: {case.iban}\nBIC: {case.bic}"
    pdf.set_xy(MARGIN, MARGIN + 26)
    pdf.multi_cell(85, 4.8, contact)

    right_x = 115.0
    y = MARGIN + 1
    if case.invoice_number is not None:
        y = _field(pdf, right_x, y, case.number_label, case.invoice_number, 9.5)
    if case.invoice_date is not None:
        y = _field(pdf, right_x, y, case.date_label, case.invoice_date, 9.5)
    if extended:
        y = _field(pdf, right_x, y, "Order Ref", case.order_ref, 9.5)
        y = _field(pdf, right_x, y, "Delivery Note", case.delivery_note, 9.5)
    if case.currency is not None and case.currency_markers != "footnote":
        y = _field(pdf, right_x, y, case.currency_label, case.currency, 9.5)
    if case.tax_id is not None:
        y = _field(pdf, right_x, y, case.tax_label, case.tax_id, 9.5)

    if extended:
        _field(pdf, MARGIN, MARGIN + 48, "Bill To", case.bill_to, 9.5)
        _field(pdf, MARGIN, MARGIN + 55, "Ship To", case.ship_to, 9.5)


def _table(
    case: RenderCase,
    pdf: FPDF,
    *,
    wrapped: bool,
) -> float:
    header_labels = (
        ("Description", "Qty", "Unit price", "Amount")
        if case.language == "EN"
        else ("Descripción", "Cant.", "Precio unit.", "Importe")
    )
    columns = (
        (MARGIN, 84.0, header_labels[0]),
        (99.0, 25.0, header_labels[1]),
        (124.0, 36.0, header_labels[2]),
        (160.0, 35.0, header_labels[3]),
    )
    row_height = 7.0 if not wrapped else 8.5
    header_y = 82.0
    pdf.set_line_width(0.2)
    pdf.set_draw_color(70, 70, 70)
    for x, width, label in columns:
        pdf.rect(x, header_y, width, row_height)
        pdf.set_xy(x + 1.5, header_y + 1.7)
        pdf.set_font_size(8.5)
        pdf.cell(width - 3, 4, label)

    y = header_y + row_height
    for item in case.items:
        cells = (
            item.description,
            str(item.quantity),
            format_render_amount(item.unit_price, case.amount_style),
            format_render_amount(item.amount, case.amount_style),
        )
        description_height = row_height
        if wrapped:
            pdf.set_xy(columns[0][0] + 1.5, y + 1.5)
            pdf.set_font_size(8)
            pdf.multi_cell(columns[0][1] - 3, 3.5, cells[0])
            description_height = max(row_height, pdf.get_y() - y + 1)
        else:
            pdf.set_xy(columns[0][0] + 1.5, y + 1.7)
            pdf.set_font_size(8)
            pdf.cell(columns[0][1] - 3, 4, cells[0])
        pdf.rect(MARGIN, y, A4_WIDTH - 2 * MARGIN, description_height)
        for x, _width, _ in columns[1:]:
            pdf.line(x, y, x, y + description_height)
        for index, (x, width, _) in enumerate(columns[1:], start=1):
            pdf.set_xy(x + 1.5, y + 1.7)
            pdf.set_font_size(8)
            pdf.cell(width - 3, 4, cells[index], align="R" if index > 0 else "L")
        y += description_height
    return y


def _summary(
    case: RenderCase,
    pdf: FPDF,
    font: FontPlan,
    y: float,
) -> float:
    subtotal = format_render_amount(case.subtotal, case.amount_style)
    vat = format_render_amount(case.vat_amount, case.amount_style)
    total = format_render_amount(case.total_amount, case.amount_style)
    marker = currency_marker(case.currency, font)
    rate = f"{case.vat_rate:.2%}"
    vat_label = "Tax" if case.language == "EN" else "IVA"
    y += 4
    y = _summary_row(pdf, "Subtotal", f"{marker}{subtotal}", y)
    y = _summary_row(pdf, f"{vat_label} ({rate})", f"{marker}{vat}", y)
    y += 2
    pdf.set_line_width(0.4)
    pdf.rect(120, y, 75, 11)
    pdf.set_font_size(10)
    pdf.set_xy(121, y + 3)
    pdf.cell(39, 5, case.total_label, align="R")
    pdf.set_xy(161, y + 3)
    pdf.cell(33, 5, f"{marker}{total}", align="R")
    return y + 11


def _complex_layout(case: RenderCase, pdf: FPDF, font: FontPlan) -> None:
    _watermark(pdf)
    _header_block(case, pdf, extended=True)
    _dashed_line(pdf, 70)
    y = _table(case, pdf, wrapped=True)
    y = _summary(case, pdf, font, y)
    pdf.set_text_color(110, 110, 110)
    pdf.set_xy(MARGIN, y + 6)
    pdf.set_font_size(5.5)
    legal = (
        "This document is a fictional invoice created for extraction testing. "
        "It does not represent a real commercial transaction, contractual offer, "
        "or tax record, and all identifiers are deliberately synthetic."
    )
    pdf.multi_cell(0, 2.7, legal)
    pdf.set_text_color(0, 0, 0)
    _stamp(pdf, case.stamp)
    footnote = "All amounts are exclusive of bank charges."
    if case.currency is not None and case.currency_markers == "footnote":
        footnote = f"{footnote} Currency: {case.currency}."
    _footer(pdf, footnote)


def _styled_layout(case: RenderCase, pdf: FPDF, font: FontPlan) -> None:
    _header_block(case, pdf, extended=False)
    _dashed_line(pdf, 72)
    y = _table(case, pdf, wrapped=False)
    _summary(case, pdf, font, y)
    _footer(pdf, case.terms)


def render_case_pdf(case: RenderCase) -> bytes:
    """Render one deterministic, single-page A4 invoice to PDF bytes."""
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_creation_date(datetime(2026, 9, 3, tzinfo=UTC))
    pdf.set_auto_page_break(auto=False, margin=15)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()
    font = resolve_font(pdf)
    if case.layout == "basic":
        _basic_layout(case, pdf, font)
    elif case.layout == "styled":
        _styled_layout(case, pdf, font)
    elif case.layout == "complex":
        _complex_layout(case, pdf, font)
    else:  # pragma: no cover - guarded by the frozen plan
        raise ValueError(f"unknown PDF layout: {case.layout}")
    return bytes(pdf.output())


def pdf_smoke_report(pdf_bytes: bytes, case: RenderCase) -> dict[str, Any]:
    """Return page count and canonical-field presence for a rendered PDF."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = len(reader.pages)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise RuntimeError(f"{case.case_id}: invalid PDF: {exc}") from exc

    present: dict[str, bool] = {}
    if case.invoice_number is not None:
        present["invoice_number"] = f"{case.number_label}: {case.invoice_number}" in text
    if case.invoice_date is not None:
        present["invoice_date"] = f"{case.date_label}: {case.invoice_date}" in text
    if case.currency is not None:
        marker_present = f"{case.currency_label}: {case.currency}" in text
        if case.currency_markers == "footnote":
            marker_present = f"Currency: {case.currency}" in text
        present["currency"] = marker_present
    if case.tax_id is not None:
        present["tax_id"] = f"{case.tax_label}: {case.tax_id}" in text
    if case.total_amount is not None:
        total = format_render_amount(case.total_amount, case.amount_style)
        present["total_amount"] = f"{case.total_label}" in text and total in text
    present["supplier_name"] = case.supplier in text
    return {"pages": pages, "text": text, "fields": present}


def assert_smoke(report: dict[str, Any], case: RenderCase) -> None:
    """Fail loudly when the PDF is not a single extractable page."""
    if report["pages"] != 1:
        raise RuntimeError(f"{case.case_id}: expected 1 page, got {report['pages']}")
    missing = sorted(field for field, present in report["fields"].items() if not present)
    if missing:
        raise RuntimeError(f"{case.case_id}: missing text-layer fields: {', '.join(missing)}")


def sum_items(items: tuple[RenderItem, ...]) -> Decimal:
    """Sum item amounts exactly in cents before converting to float."""
    return sum((Decimal(str(item.amount)) for item in items), Decimal("0"))
