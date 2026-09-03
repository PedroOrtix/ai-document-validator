"""Integration tests for the PDF path (text-layer extraction) and multipart upload."""

import io

from fastapi.testclient import TestClient
from fpdf import FPDF

from docvalidator.api.main import app

client = TestClient(app)

INVOICE_TEXT = (
    "Northwind Supplies GmbH\n"
    "Invoice No: INV-2026-0001\n"
    "Invoice Date: 2026-08-20\n"
    "Currency: EUR\n"
    "Supplier: Northwind Supplies GmbH\n"
    "VAT: DE123456789\n"
    "Total Amount: 1236.00"
)


def _pdf_bytes(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


def test_validate_pdf_multipart_returns_pass_verdict() -> None:
    config = (
        '{"max_age_days": 90, "allowed_currencies": ["EUR", "GBP"], '
        '"required_fields": ["supplier_name", "invoice_number", "invoice_date", "total_amount"]}'
    )
    response = client.post(
        "/v1/validate",
        files={"file": ("invoice.pdf", io.BytesIO(_pdf_bytes(INVOICE_TEXT)), "application/pdf")},
        data={"config": config},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASS"
    fields = body["extraction"]["fields"]
    assert fields["supplier_name"]["value"] == "Northwind Supplies GmbH"
    assert fields["invoice_number"]["value"] == "INV-2026-0001"
    assert fields["invoice_date"]["value"] == "2026-08-20"
    assert fields["total_amount"]["value"] == 1236.0
    assert fields["currency"]["value"] == "EUR"
    assert fields["tax_id"]["value"] == "DE123456789"
    assert body["extraction"]["metadata"]["backend"] == "offline"


def test_validate_garbage_pdf_is_a_structured_422() -> None:
    response = client.post(
        "/v1/validate",
        files={"file": ("scan.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF"), "application/pdf")},
        data={"config": "{}"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_document"
    assert "unable to read PDF" in body["error"]["message"]


def test_validate_blank_pdf_without_text_layer_is_a_structured_422() -> None:
    blank_pdf = FPDF()
    blank_pdf.add_page()
    response = client.post(
        "/v1/validate",
        files={"file": ("scan.pdf", io.BytesIO(bytes(blank_pdf.output())), "application/pdf")},
        data={"config": "{}"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_document"
    assert "no extractable text layer" in body["error"]["message"]


def test_extract_pdf_multipart_without_config_uses_defaults() -> None:
    """/v1/extract is documented as document-only; config must be optional in multipart."""
    response = client.post(
        "/v1/extract",
        files={"file": ("invoice.pdf", io.BytesIO(_pdf_bytes(INVOICE_TEXT)), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document_type"] == "SUPPLIER_INVOICE"
    fields = body["fields"]
    assert fields["supplier_name"]["value"] == "Northwind Supplies GmbH"
    assert fields["invoice_number"]["value"] == "INV-2026-0001"
    assert body["metadata"]["backend"] == "offline"


def test_validate_pdf_multipart_without_config_uses_defaults() -> None:
    """Defaults (max_age_days=90, no currency restriction) yield PASS for a fresh invoice."""
    response = client.post(
        "/v1/validate",
        files={"file": ("invoice.pdf", io.BytesIO(_pdf_bytes(INVOICE_TEXT)), "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PASS"


def test_extract_multipart_without_file_is_a_structured_422() -> None:
    response = client.post("/v1/extract", files={"config": (None, "{}")})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "file is required" in body["error"]["message"]


def test_extract_multipart_with_uploaded_config_part_is_a_structured_422() -> None:
    """A second file part named `config` is a client error, not a server crash."""
    response = client.post(
        "/v1/extract",
        files=[
            ("file", ("invoice.pdf", io.BytesIO(_pdf_bytes(INVOICE_TEXT)), "application/pdf")),
            ("config", ("config.json", b"{}", "application/json")),
        ],
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_request"
    assert "config must be a JSON string" in body["error"]["message"]