"""API integration tests for the OCR backend request path."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import status
from fastapi.testclient import TestClient

from docvalidator.api.main import app
from docvalidator.domain.models import DocumentExtraction, ExtractionMetadata
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.parsing import RegexFieldParser

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
INVOICE_TEXT = (GOLDEN_DIR / "t0_en_0.txt").read_text(encoding="utf-8")

client = TestClient(app)


class _FakeOcrExtractor:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        assert document.text == INVOICE_TEXT
        fields = RegexFieldParser().extract_fields(document.to_text())
        return DocumentExtraction(
            fields=fields,
            metadata=ExtractionMetadata(backend="ocr"),
        )


def test_extract_endpoint_accepts_ocr_backend_with_fake_seam() -> None:
    with patch("docvalidator.api.main.OcrExtractor", _FakeOcrExtractor):
        response = client.post(
            "/v1/extract",
            json={"text": INVOICE_TEXT, "extraction_backend": "ocr"},
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["metadata"]["backend"] == "ocr"
    assert body["fields"]["supplier_name"]["value"] is not None
