"""Unit tests for deterministic regex-based extraction (the OCR floor's parser)."""

import json
from datetime import date
from pathlib import Path

import pytest
from fpdf import FPDF

from docvalidator.extraction import DocumentInput, ExtractionError
from docvalidator.extraction.ocr import OcrExtractor

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
TXT_MANIFEST = json.loads((GOLDEN_DIR / "manifest_txt.json").read_text(encoding="utf-8"))
TXT_CASE_IDS = [case["case_id"] for case in TXT_MANIFEST["cases"]]


@pytest.fixture
def extractor() -> OcrExtractor:
    """OcrExtractor without the OCR engine: text input bypasses RapidOCR."""
    return OcrExtractor(ocr_fn=lambda pages: "")


def load_fixture(name: str) -> tuple[str, dict[str, object]]:
    text = (GOLDEN_DIR / f"{name}.txt").read_text(encoding="utf-8")
    expected = json.loads((GOLDEN_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
    raw_fields = expected["expected_fields"]
    assert isinstance(raw_fields, dict)
    if raw_fields.get("invoice_date") is not None:
        raw_fields["invoice_date"] = date.fromisoformat(str(raw_fields["invoice_date"]))
    return text, expected


def test_exact_document_source_is_required() -> None:
    with pytest.raises(ValueError, match="provide text or pdf_bytes"):
        DocumentInput()
    with pytest.raises(ValueError, match="provide only one"):
        DocumentInput(text="invoice", pdf_bytes=b"pdf")


def test_markitdown_pdf_extracts_text() -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, "Invoice\nTotal 10.00")

    document = DocumentInput(pdf_bytes=bytes(pdf.output()))

    assert "Total 10.00" in document.to_text()


def test_blank_markitdown_pdf_has_typed_empty_text_failure() -> None:
    pdf = FPDF()
    pdf.add_page()

    with pytest.raises(ExtractionError, match="no extractable text layer"):
        DocumentInput(pdf_bytes=bytes(pdf.output())).to_text()


def test_unreadable_markitdown_pdf_has_typed_read_failure() -> None:
    with pytest.raises(ExtractionError, match="unable to read PDF"):
        DocumentInput(pdf_bytes=b"%PDF-1.4\n%%EOF").to_text()


def test_to_text_returns_plain_text() -> None:
    document = DocumentInput(text="Invoice No: INV-2026-0001")
    assert document.to_text() == "Invoice No: INV-2026-0001"


def test_pdf_without_text_layer_raises_typed_error() -> None:
    pdf_bytes = b"%PDF-1.4\n%%EOF"
    document = DocumentInput(pdf_bytes=pdf_bytes)
    with pytest.raises(ExtractionError, match="unable to read PDF"):
        document.to_text()


@pytest.mark.parametrize(
    ("text", "expected_number", "confidence_floor"),
    [
        ("Invoice No: INV-2026-0001", "INV-2026-0001", 0.95),
        ("Invoice #A-1234", "A-1234", 0.95),
        ("Factura Nº F2026-123", "F2026-123", 0.95),
        ("Reference INV-2026-7654", "INV-2026-7654", 0.8),
    ],
)
def test_invoice_number_patterns(
    extractor: OcrExtractor,
    text: str,
    expected_number: str,
    confidence_floor: float,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    field = extraction.fields["invoice_number"]
    assert field.value == expected_number
    assert field.confidence >= confidence_floor
    assert field.evidence is not None


@pytest.mark.parametrize(
    ("text", "expected_date"),
    [
        ("Invoice Date: 2026-01-31", date(2026, 1, 31)),
        ("Invoice Date: 31/01/2026", date(2026, 1, 31)),
        ("Date: 31 January 2026", date(2026, 1, 31)),
        ("Date: Jan 31, 2026", date(2026, 1, 31)),
    ],
)
def test_invoice_date_formats(
    extractor: OcrExtractor,
    text: str,
    expected_date: date,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["invoice_date"].value == expected_date


def test_invoice_date_missing_or_invalid_is_not_extracted(extractor: OcrExtractor) -> None:
    extraction = extractor.extract(DocumentInput(text="Date: 31/02/2026"))
    field = extraction.fields["invoice_date"]
    assert field.value is None
    assert field.confidence == 0


@pytest.mark.parametrize(
    ("text", "expected_amount"),
    [
        ("Total: 1,234.56", 1234.56),
        ("Total: 1.234,56", 1234.56),
        ("Amount Due: $99.00", 99.0),
        ("Grand Total: €2.000,00", 2000.0),
        ("Total: -350.00", -350.0),
        ("Total: 2.500,00", 2500.0),
    ],
)
def test_total_amount_formats(
    extractor: OcrExtractor,
    text: str,
    expected_amount: float,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["total_amount"].value == expected_amount
    assert isinstance(extraction.fields["total_amount"].value, float)


@pytest.mark.parametrize(
    ("text", "expected_currency", "minimum_confidence"),
    [
        ("Currency: EUR", "EUR", 0.95),
        ("Total: €100.00", "EUR", 0.8),
        ("Total: £100.00", "GBP", 0.8),
        ("Total: USD 100.00", "USD", 0.9),
    ],
)
def test_currency_detection(
    extractor: OcrExtractor,
    text: str,
    expected_currency: str,
    minimum_confidence: float,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    field = extraction.fields["currency"]
    assert field.value == expected_currency
    assert field.confidence >= minimum_confidence


@pytest.mark.parametrize(
    ("text", "expected_name"),
    [
        ("Acme Limited\nInvoice Date: 2026-08-20", "Acme Limited"),
        ("Supplier: Northwind GmbH\nInvoice", "Northwind GmbH"),
    ],
)
def test_supplier_name_detection(
    extractor: OcrExtractor,
    text: str,
    expected_name: str,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["supplier_name"].value == expected_name


def test_long_first_line_is_not_a_supplier_name(extractor: OcrExtractor) -> None:
    text = "A" * 101
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["supplier_name"].value is None


@pytest.mark.parametrize(
    ("text", "expected_tax_id"),
    [
        ("VAT: DE123456789", "DE123456789"),
        ("VAT: ESB12345678", "ESB12345678"),
        ("TAX: GB123456789", "GB123456789"),
    ],
)
def test_tax_id_patterns(
    extractor: OcrExtractor,
    text: str,
    expected_tax_id: str,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["tax_id"].value == expected_tax_id


def test_last_total_row_wins_over_subtotal(extractor: OcrExtractor) -> None:
    """Regression: among several Total rows, the grand total is the last one."""
    text = "Helios Ltd\nTotal (excl. VAT)  1,000.00\nTotal               1,210.00"
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["total_amount"].value == 1210.0


def test_unlabeled_german_total_is_found(extractor: OcrExtractor) -> None:
    extraction = extractor.extract(
        DocumentInput(text="Kraft GmbH\nDatum: 2026-08-15\nGesamtbetrag 2.500,00")
    )
    assert extraction.fields["total_amount"].value == 2500.0


def test_unparseable_labeled_date_beats_later_unlabeled_token(extractor: OcrExtractor) -> None:
    """Regression: a label match must not silently fall through to a later token."""
    extraction = extractor.extract(DocumentInput(text="Invoice Date: not-a-date\n31/01/2026"))
    field = extraction.fields["invoice_date"]
    assert field.value is None


def test_non_letter_first_line_is_not_a_supplier_name(extractor: OcrExtractor) -> None:
    extraction = extractor.extract(DocumentInput(text="@@@@ ####\nrandom note"))
    assert extraction.fields["supplier_name"].value is None


@pytest.mark.parametrize(
    ("fixture_name", "tier"),
    [
        *(
            pytest.param(
                case["case_id"],
                case["tier"],
                marks=(
                    pytest.mark.xfail(
                        reason=(
                            "known regex-parser gap at tier>=1 "
                            "(spelled dates, GB VAT ids, rare label variants); "
                            "tracked for the LLM backend"
                        ),
                        strict=False,
                    )
                    if case["tier"] >= 1
                    else []
                ),
                id=case["case_id"],
            )
            for case in TXT_MANIFEST["cases"]
        ),
    ],
)
def test_fixture_expected_fields(
    extractor: OcrExtractor,
    fixture_name: str,
    tier: int,
) -> None:
    text, expected = load_fixture(fixture_name)
    extraction = extractor.extract(DocumentInput(text=text))
    expected_fields = expected["expected_fields"]
    assert isinstance(expected_fields, dict)
    for field_name, expected_value in expected_fields.items():
        assert extraction.fields[field_name].value == expected_value


def test_missing_fields_are_zero_confidence_without_exception(
    extractor: OcrExtractor,
) -> None:
    extraction = extractor.extract(DocumentInput(text="no structured data"))
    for field in extraction.fields.values():
        if field.value is None:
            assert field.confidence == 0


@pytest.mark.parametrize(
    ("line", "expected_amount"),
    [
        ("Total: 1250.00", 1250.0),
        ("Total: 12500", 12500.0),
        ("Total: 12,500.00 USD", 12500.0),
        ("Total: 1.234,56 EUR", 1234.56),
        ("Amount Due: €9.99", 9.99),
        ("Grand Total: 9876543.21", 9876543.21),
    ],
)
def test_amounts_without_thousands_separator_are_not_truncated(
    extractor: OcrExtractor, line: str, expected_amount: float
) -> None:
    """Regression: 4+ digit integers with no grouping must parse in full."""
    extraction = extractor.extract(DocumentInput(text=f"ACME Ltd\n{line}"))
    assert extraction.fields["total_amount"].value == expected_amount


def test_european_space_separated_amounts_parse_correctly(extractor: OcrExtractor) -> None:
    """SI / European standard: space as thousand separator e.g. 680 867,00."""
    text = "Proveedor: Cerámica Alfonso S.L.\nTotal: 680 867,00 €"
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["total_amount"].value == 680867.0


def test_spanish_written_dates_parse_locale_independently(extractor: OcrExtractor) -> None:
    from datetime import date

    text = "Fecha de Factura: 14 ago 2026\nTotal: 100.00"
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["invoice_date"].value == date(2026, 8, 14)


def test_spanish_invoice_number_variants(extractor: OcrExtractor) -> None:
    cases = [
        ("Numero de Factura: RE-2025-6505", "RE-2025-6505"),
        ("Factura Nº: FAC-2024-7726", "FAC-2024-7726"),
        ("FacturaN9: FAC3703", "FAC3703"),
        ("NumerodeFactura: 2025/9526", "2025/9526"),
    ]
    for line, expected_num in cases:
        extraction = extractor.extract(DocumentInput(text=f"Empresa S.L.\n{line}"))
        assert extraction.fields["invoice_number"].value == expected_num


def test_vertical_multiline_pairing(extractor: OcrExtractor) -> None:
    from datetime import date

    text = "Proveedor:\nNorthwind Traders\nFecha de Factura:\n2026-08-14\nTotal:\n1250.00"
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["invoice_date"].value == date(2026, 8, 14)
    assert extraction.fields["total_amount"].value == 1250.0


def test_spatial_box_reading_order_sorting() -> None:
    from docvalidator.extraction.ocr import _sort_boxes_reading_order

    # Synthetic RapidOCR output where boxes are out of reading order:
    # Row 2 (y ~ 100): "Total:" (left), "1250.00" (right)
    # Row 1 (y ~ 50):  "Date:" (left),  "2026-08-01" (right)
    raw_result = [
        [[[200, 100], [300, 100], [300, 120], [200, 120]], "1250.00", 0.99],
        [[[50, 50], [150, 50], [150, 70], [50, 70]], "Date:", 0.98],
        [[[200, 50], [320, 50], [320, 70], [200, 70]], "2026-08-01", 0.99],
        [[[50, 100], [120, 100], [120, 120], [50, 120]], "Total:", 0.97],
    ]
    sorted_text = _sort_boxes_reading_order(raw_result)
    expected_lines = ["Date: 2026-08-01", "Total: 1250.00"]
    assert sorted_text.splitlines() == expected_lines
