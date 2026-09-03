"""Integration tests: explicit VLM backend construction and API error mapping."""

import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from docvalidator.api.main import app
from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
)
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.llm import InvoiceExtraction
from docvalidator.extraction.vision import VisionExtractor  # noqa: F401

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
FULL_DOC_TEXT = (GOLDEN_DIR / "t0_en_0.txt").read_text(encoding="utf-8")

client = TestClient(app)

_VALID_PAYLOAD: dict[str, Any] = {
    "supplier_name": "ACME Ltd",
    "invoice_number": "INV-1",
    "invoice_date": "2026-01-31",
    "total_amount": 123.45,
    "currency": "EUR",
    "tax_id": "DE123456789",
}


class _RecordingExtractor:
    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        self.model = kwargs.get("model")

    def extract(self, document: DocumentInput) -> Any:
        assert document.pdf_bytes == b"fake-pdf"
        assert self.model is None
        parsed = InvoiceExtraction.model_validate(_VALID_PAYLOAD)
        return DocumentExtraction(
            fields={
                name: ExtractedField(value=value, confidence=0.75)
                for name, value in parsed.model_dump().items()
            },
            metadata=ExtractionMetadata(
                backend="vlm",
                provider="openrouter",
                model="z-ai/glm-5.3-flash",
                total_tokens=42,
            ),
        )


def test_extract_endpoint_vlm_backend_returns_vlm_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "docvalidator.extraction.vision.VisionExtractor",
        _RecordingExtractor,
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    response = client.post(
        "/v1/extract",
        json={
            "content_b64": base64.b64encode(b"fake-pdf").decode("ascii"),
            "filename": "scan.pdf",
            "extraction_backend": "vlm",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["fields"]["supplier_name"]["value"] == "ACME Ltd"
    assert body["metadata"]["backend"] == "vlm"


def test_extract_endpoint_vlm_without_key_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    response = client.post(
        "/v1/extract",
        json={"text": FULL_DOC_TEXT, "extraction_backend": "vlm"},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"]["code"] == "llm_configuration_error"
