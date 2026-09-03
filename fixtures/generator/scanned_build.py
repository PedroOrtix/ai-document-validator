"""Build or verify the deterministic scanned-invoice golden lane.

    uv run python -m fixtures.generator.scanned_build            # build all 12 cases
    uv run python -m fixtures.generator.scanned_build --verify   # verify hashes and truth
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fixtures.generator import pdf_build
from fixtures.generator.pdf_render import (
    A4_HEIGHT,
    A4_WIDTH,
    DEJAVU_SANS,
    MARGIN,
    RenderCase,
    RenderItem,
    currency_marker,
    format_render_amount,
)
from fixtures.generator.spec_v2 import PDF_PLAN, case_rng
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "fixtures" / "golden"
MANIFEST_NAME = "manifest_scanned.json"
PNG_DPI = 180
PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
SCALE = PAGE_WIDTH / A4_WIDTH
POINT_SCALE = PNG_DPI / 72.0

SELECTED_ROWS = tuple(
    dict(PDF_PLAN[language_offset + row_index])
    for language_offset in (0, 10)
    for row_index in (0, 1, 3, 6, 7, 8)
)


def _font(size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(DEJAVU_SANS), max(1, round(size * POINT_SCALE)))


def _text(
    draw: ImageDraw.ImageDraw,
    x_mm: float,
    y_mm: float,
    value: str,
    *,
    size: float = 10,
    anchor: str = "la",
    fill: tuple[int, int, int] | int = 0,
) -> None:
    draw.text((x_mm * SCALE, y_mm * SCALE), value, font=_font(size), anchor=anchor, fill=fill)


def _line(
    draw: ImageDraw.ImageDraw,
    x_mm: float,
    y_mm: float,
    text: str,
    size: float = 10,
) -> None:
    _text(draw, x_mm, y_mm - 1.5, text, size=size)


def _field(
    draw: ImageDraw.ImageDraw,
    x_mm: float,
    y_mm: float,
    label: str,
    value: str,
    size: float = 10,
) -> float:
    _line(draw, x_mm, y_mm, f"{label}: {value}", size)
    return y_mm + 5.5


def _summary_row(
    draw: ImageDraw.ImageDraw,
    label: str,
    value: str,
    y_mm: float,
) -> float:
    _text(draw, 160, y_mm - 1.5, label, anchor="ra")
    _text(draw, 195, y_mm - 1.5, value, anchor="ra")
    return y_mm + 5.5


def _table_row(draw: ImageDraw.ImageDraw, item: RenderItem, y_mm: float, case: RenderCase) -> None:
    values = (
        (15, item.description, "la"),
        (99, str(item.quantity), "la"),
        (124, format_render_amount(item.unit_price, case.amount_style), "la"),
        (160, format_render_amount(item.amount, case.amount_style), "la"),
    )
    for x_mm, value, anchor in values:
        _text(draw, x_mm + 1.5, y_mm + 1.7, value, size=8, anchor=anchor)
    draw.rectangle((MARGIN * SCALE, y_mm * SCALE, 195 * SCALE, (y_mm + 7) * SCALE), outline=90)


def _summary(case: RenderCase, draw: ImageDraw.ImageDraw, y_mm: float) -> float:
    marker = currency_marker(case.currency, _FontPlan())
    y_mm += 4
    subtotal = format_render_amount(case.subtotal, case.amount_style)
    y_mm = _summary_row(draw, "Subtotal", f"{marker}{subtotal}", y_mm)
    vat_label = "Tax" if case.language == "EN" else "IVA"
    y_mm = _summary_row(
        draw,
        f"{vat_label} ({case.vat_rate:.2%})",
        f"{marker}{format_render_amount(case.vat_amount, case.amount_style)}",
        y_mm,
    )
    y_mm += 2
    draw.rectangle((120 * SCALE, y_mm * SCALE, 195 * SCALE, (y_mm + 11) * SCALE), outline=40)
    _text(draw, 160, y_mm + 3, case.total_label, anchor="ra")
    _text(
        draw,
        194,
        y_mm + 3,
        f"{marker}{format_render_amount(case.total_amount, case.amount_style)}",
        anchor="ra",
    )
    return y_mm + 11


class _FontPlan:
    """Pillow renderer only uses a Unicode font, so currency symbols are always safe."""

    family = "DejaVu"
    unicode_symbols = True


def _header(case: RenderCase, draw: ImageDraw.ImageDraw, *, extended: bool) -> None:
    _line(draw, MARGIN, MARGIN, case.supplier, size=14)
    _line(draw, MARGIN, MARGIN + 8, case.street, size=9)
    _line(draw, MARGIN, MARGIN + 13, case.city_line, size=9)
    contact = (case.contact_phone, case.contact_email, f"IBAN: {case.iban}", f"BIC: {case.bic}")
    for offset, line in enumerate(contact):
        _line(draw, MARGIN, MARGIN + 26 + offset * 4.8, line, size=9)

    right_x = 115.0
    y = MARGIN + 1
    if case.invoice_number is not None:
        y = _field(draw, right_x, y, case.number_label, case.invoice_number, 9.5)
    if case.invoice_date is not None:
        y = _field(draw, right_x, y, case.date_label, case.invoice_date, 9.5)
    if extended:
        y = _field(draw, right_x, y, "Order Ref", case.order_ref, 9.5)
        y = _field(draw, right_x, y, "Delivery Note", case.delivery_note, 9.5)
    if case.currency is not None and case.currency_markers != "footnote":
        y = _field(draw, right_x, y, case.currency_label, case.currency, 9.5)
    if case.tax_id is not None:
        y = _field(draw, right_x, y, case.tax_label, case.tax_id, 9.5)
    if extended:
        _field(draw, MARGIN, MARGIN + 48, "Bill To", case.bill_to, 9.5)
        _field(draw, MARGIN, MARGIN + 55, "Ship To", case.ship_to, 9.5)


def _footer(case: RenderCase, draw: ImageDraw.ImageDraw, text: str) -> None:
    _text(draw, MARGIN, A4_HEIGHT - 16, text, size=7.5)
    _text(draw, 195, A4_HEIGHT - 11, "Page 1", size=7.5, anchor="ra")


def _degrade(image: Image.Image, rng: random.Random) -> Image.Image:
    image = image.rotate(rng.uniform(-1.0, 1.5), resample=Image.BICUBIC, fillcolor=(255, 255, 255))
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.97, 1.03))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.96, 1.04))
    sigma = rng.uniform(2.0, 4.0)
    noise_data = bytearray(image.width * image.height)
    for index in range(image.width * image.height):
        uniform = max(rng.random(), 1e-12)
        normal = math.sqrt(-2.0 * math.log(uniform)) * math.cos(2.0 * math.pi * rng.random())
        noise_data[index] = min(255, max(0, 128 + round(normal * sigma)))
    noise = Image.frombytes("L", image.size, bytes(noise_data))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    image = Image.blend(image, noise_rgb, 0.06)
    image = image.filter(ImageFilter.GaussianBlur(0.3))
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=rng.randrange(72, 79),
        optimize=True,
        dpi=(PNG_DPI, PNG_DPI),
    )
    return Image.open(buffer)


def render_case_png(case: RenderCase, rng: random.Random) -> bytes:
    """Render one deterministic scan-like PNG, mirroring the PDF tier layout."""
    image = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    if case.layout == "basic":
        y = MARGIN + 2
        y = _field(draw, MARGIN, y, "Supplier", case.supplier)
        y = _field(draw, MARGIN, y, "Address", f"{case.street}, {case.city_line}")
        y = _field(draw, MARGIN, y, "Contact", f"{case.contact_phone} / {case.contact_email}")
        y = _field(draw, MARGIN, y, "IBAN", case.iban)
        y = _field(draw, MARGIN, y, "BIC", case.bic)
        y += 2
        if case.invoice_number is not None:
            y = _field(draw, MARGIN, y, case.number_label, case.invoice_number)
        if case.invoice_date is not None:
            y = _field(draw, MARGIN, y, case.date_label, case.invoice_date)
        if case.currency is not None:
            y = _field(draw, MARGIN, y, case.currency_label, case.currency)
        if case.tax_id is not None:
            y = _field(draw, MARGIN, y, case.tax_label, case.tax_id)
        _summary(case, draw, y + 4)
    else:
        if case.layout == "complex":
            draw.rectangle(
                (112 * SCALE, 175 * SCALE, 190 * SCALE, 217 * SCALE), fill=(242, 244, 247)
            )
            draw.rectangle((22 * SCALE, 230 * SCALE, 80 * SCALE, 254 * SCALE), fill=(242, 244, 247))
        _header(case, draw, extended=case.layout == "complex")
        dashed_y = 70 if case.layout == "complex" else 72
        for x_mm in range(round(MARGIN), 196, 3):
            draw.line(
                (x_mm * SCALE, dashed_y * SCALE, min(x_mm + 1, 195) * SCALE, dashed_y * SCALE),
                fill=150,
            )
        y_mm = 82
        labels = ("Description", "Qty", "Unit price", "Amount") if case.language == "EN" else (
            "Descripción", "Cant.", "Precio unit.", "Importe"
        )
        for x_mm, label in zip((15, 99, 124, 160), labels, strict=True):
            _text(draw, x_mm + 1.5, y_mm + 1.7, label, size=8.5)
        draw.rectangle((MARGIN * SCALE, y_mm * SCALE, 195 * SCALE, (y_mm + 7) * SCALE), outline=70)
        for item in case.items:
            y_mm += 7
            _table_row(draw, item, y_mm, case)
        y_mm += 7
        y_mm = _summary(case, draw, y_mm)
        if case.layout == "complex":
            _text(draw, 148, 244, case.stamp, size=25, anchor="ma", fill=(185, 185, 185))
        _footer(case, draw, case.terms)

    degraded = _degrade(image, rng)
    return _png_bytes(degraded)


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, dpi=(PNG_DPI, PNG_DPI))
    return buffer.getvalue()


def _wrap_pdf(png_bytes: bytes) -> bytes:
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_creation_date(datetime(2026, 9, 3, tzinfo=UTC))
    pdf.set_auto_page_break(auto=False, margin=15)
    pdf.add_page()
    pdf.image(io.BytesIO(png_bytes), x=0, y=0, w=A4_WIDTH, h=A4_HEIGHT)
    pdf_bytes = bytes(pdf.output())
    if PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text().strip():
        raise RuntimeError("scanned PDF unexpectedly contains a text layer")
    return pdf_bytes


def _scanned_expected(expected: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(expected))
    result["slices"]["format"] = "scanned"
    result["slices"]["degradation"] = "scan_v1"
    return result


def build_all() -> list[dict[str, Any]]:
    """Render every selected PDF row, wrap it image-only, and write truth/manifest."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    manifest_cases: list[dict[str, Any]] = []
    for plan_row in SELECTED_ROWS:
        case, pdf_expected = pdf_build.build_case(plan_row)
        expected = _scanned_expected(pdf_expected)
        rng = case_rng(case.case_id)
        pdf_bytes = _wrap_pdf(render_case_png(case, rng))
        artifact_base = f"scan_{plan_row['case_id']}"
        pdf_path = GOLDEN_DIR / f"{artifact_base}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        (GOLDEN_DIR / f"{artifact_base}.expected.json").write_text(
            json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest_cases.append(
            {
                "case_id": artifact_base,
                "language": case.language,
                "tier": plan_row["tier"],
                "scenario": expected["slices"]["scenario"],
                "expected_verdict": expected["expected_verdict_status"],
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "pages": 1,
                "formats": ["scanned"],
            }
        )
    manifest_cases.sort(key=lambda entry: entry["case_id"])
    manifest = {"lane": "scanned", "cases": manifest_cases}
    (GOLDEN_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_cases


def verify_all() -> int:
    """Re-derive hashes, truth, page count, and absence of a text layer."""
    manifest_path = GOLDEN_DIR / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"{MANIFEST_NAME} is missing")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_ids = {f"scan_{row['case_id']}" for row in SELECTED_ROWS}
    if {entry["case_id"] for entry in manifest["cases"]} != expected_ids:
        print(f"{MANIFEST_NAME} case set differs from selected PDF_PLAN rows")
        return 1

    problems: list[str] = []
    plan_by_id = {row["case_id"]: row for row in SELECTED_ROWS}
    for entry in manifest["cases"]:
        underlying_id = entry["case_id"].removeprefix("scan_")
        _, pdf_expected = pdf_build.build_case(plan_by_id[underlying_id])
        expected = _scanned_expected(pdf_expected)
        pdf_path = GOLDEN_DIR / f"scan_{underlying_id}.pdf"
        expected_path = GOLDEN_DIR / f"scan_{underlying_id}.expected.json"
        if not pdf_path.is_file() or not expected_path.is_file():
            problems.append(f"{entry['case_id']}: artifact missing")
            continue
        pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if pdf_hash != entry["pdf_sha256"]:
            problems.append(f"{entry['case_id']}: pdf hash drift")
        if json.loads(expected_path.read_text(encoding="utf-8")) != expected:
            problems.append(f"{entry['case_id']}: expected truth drift")
        reader = PdfReader(io.BytesIO(pdf_path.read_bytes()))
        if len(reader.pages) != 1 or reader.pages[0].extract_text().strip():
            problems.append(f"{entry['case_id']}: invalid image-only PDF")
    for problem in problems:
        print(problem)
    print(f"verify: {len(problems)} problems over {len(manifest['cases'])} scanned cases")
    return len(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check drift instead of writing")
    args = parser.parse_args()
    if args.verify:
        raise SystemExit(1 if verify_all() else 0)
    cases = build_all()
    print(f"built {len(cases)} scanned cases")


if __name__ == "__main__":
    main()
