"""Unit tests for deterministic extraction."""

import json
from datetime import date
from pathlib import Path

import pytest

from docvalidator.extraction import DocumentInput, ExtractionError, OfflineExtractor

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "invoices"


@pytest.fixture
def extractor() -> OfflineExtractor:
    return OfflineExtractor()


def load_fixture(name: str) -> tuple[str, dict[str, object]]:
    text = (FIXTURE_DIR / f"{name}.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURE_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
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
    extractor: OfflineExtractor,
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
    extractor: OfflineExtractor,
    text: str,
    expected_date: date,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["invoice_date"].value == expected_date


def test_invoice_date_missing_or_invalid_is_not_extracted(extractor: OfflineExtractor) -> None:
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
    ],
)
def test_total_amount_formats(
    extractor: OfflineExtractor,
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
    extractor: OfflineExtractor,
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
    extractor: OfflineExtractor,
    text: str,
    expected_name: str,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["supplier_name"].value == expected_name


def test_long_first_line_is_not_a_supplier_name(extractor: OfflineExtractor) -> None:
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
    extractor: OfflineExtractor,
    text: str,
    expected_tax_id: str,
) -> None:
    extraction = extractor.extract(DocumentInput(text=text))
    assert extraction.fields["tax_id"].value == expected_tax_id


@pytest.mark.parametrize(
    "fixture_name",
    [
        "happy_path_eur",
        "stale_invoice",
        "usd_not_allowed",
        "missing_supplier_name",
        "european_format_vat",
        "minimal_garbage",
    ],
)
def test_fixture_expected_fields(
    extractor: OfflineExtractor,
    fixture_name: str,
) -> None:
    text, expected = load_fixture(fixture_name)
    extraction = extractor.extract(DocumentInput(text=text))
    expected_fields = expected["expected_fields"]
    assert isinstance(expected_fields, dict)
    for field_name, expected_value in expected_fields.items():
        assert extraction.fields[field_name].value == expected_value


def test_missing_fields_are_zero_confidence_without_exception(
    extractor: OfflineExtractor,
) -> None:
    extraction = extractor.extract(DocumentInput(text="no structured data"))
    for field in extraction.fields.values():
        if field.value is None:
            assert field.confidence == 0
