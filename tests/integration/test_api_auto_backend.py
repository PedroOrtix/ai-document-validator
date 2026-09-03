"""API integration tests for the auto extraction backend."""

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from fpdf import FPDF
from pypdfium2 import PdfDocument

from docvalidator.api.main import app
from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
)
from docvalidator.extraction.input import DocumentInput

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
FULL_DOC_TEXT = (GOLDEN_DIR / "t0_en_0.txt").read_text(encoding="utf-8")

client = TestClient(app)

FIELD_NAMES = (
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
    "tax_id",
)


class _FakeAutoExtractor:
    """Route stand-in returning metadata that identifies the selected model."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.model = "placeholder"

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        assert isinstance(document, DocumentInput)
        return DocumentExtraction(
            fields={
                name: ExtractedField(value=None, confidence=0)
                for name in FIELD_NAMES
            },
            metadata=ExtractionMetadata(backend="auto", model=self.model),
        )


def _patch_auto_extractor(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    class _RoutedFakeAutoExtractor(_FakeAutoExtractor):
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.model = model

    monkeypatch.setattr(
        "docvalidator.extraction.routing.AutoExtractor", _RoutedFakeAutoExtractor
    )


def _text_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, text=FULL_DOC_TEXT)
    return bytes(pdf.output())


def _scanned_pdf() -> bytes:
    rendered_pages = PdfDocument(_text_pdf())
    scanned = FPDF()
    for page in rendered_pages:
        bitmap = io.BytesIO()
        page.render().to_pil().save(bitmap, format="PNG")
        scanned.add_page()
        scanned.image(bitmap)
    return bytes(scanned.output())


def test_default_auto_backend_uses_patched_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_auto_extractor(monkeypatch, "llm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    response = client.post(
        "/v1/extract",
        json={"text": FULL_DOC_TEXT, "extraction_backend": "auto"},
    )

    assert response.status_code == status.HTTP_200_OK
    metadata = response.json()["metadata"]
    assert metadata["backend"] == "auto"
    assert metadata["model"] == "llm"


def test_auto_backend_without_key_returns_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    response = client.post(
        "/v1/extract",
        json={"text": FULL_DOC_TEXT, "extraction_backend": "auto"},
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["error"]["code"] == "llm_configuration_error"
    assert "metadata" not in body


def test_explicit_auto_backend_is_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_auto_extractor(monkeypatch, "ocr")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    response = client.post(
        "/v1/extract",
        json={"text": FULL_DOC_TEXT, "extraction_backend": "auto"},
    )

    assert response.status_code == status.HTTP_200_OK
    metadata = response.json()["metadata"]
    assert metadata["backend"] == "auto"
    assert metadata["model"] == "ocr"


def test_auto_backend_routes_scanned_pdf_to_vlm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_auto_extractor(monkeypatch, "vlm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    response = client.post(
        "/v1/extract",
        json={
            "content_b64": base64.b64encode(_scanned_pdf()).decode("ascii"),
            "filename": "scan.pdf",
            "extraction_backend": "auto",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    metadata = response.json()["metadata"]
    assert metadata["backend"] == "auto"
    assert metadata["model"] == "vlm"


def test_multipart_txt_uses_offline_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    response = client.post(
        "/v1/extract",
        files={"file": ("invoice.txt", io.BytesIO(FULL_DOC_TEXT.encode()), "text/plain")},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["metadata"]["backend"] == "offline"
