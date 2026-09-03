"""Unit tests for the vision extraction rendering and message path."""

from datetime import date
from typing import Any

import pytest
from fpdf import FPDF
from langchain_core.exceptions import OutputParserException

from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.llm import InvoiceExtraction, LLMParsingError, LLMTimeoutError
from docvalidator.extraction.rendering import render_pdf_pages_to_png
from docvalidator.extraction.vision import VisionExtractor
from docvalidator.settings import LLMSettings

_VALID_PAYLOAD: dict[str, Any] = {
    "supplier_name": "ACME Ltd",
    "invoice_number": "INV-1",
    "invoice_date": "2026-01-31",
    "total_amount": 123.45,
    "currency": "EUR",
    "tax_id": "DE123456789",
}


class _StructuredStub:
    def __init__(self, result: Any) -> None:
        self._result = result

    def invoke(self, messages: Any) -> Any:
        return {"parsed": self._result, "raw": None}


class _FakeModel:
    def __init__(self, result: Any) -> None:
        self._result = result

    def invoke(self, messages: Any) -> Any:
        raise AssertionError("raw model invoke should not be used by the fake chain")

    def with_structured_output(self, *args: object, **kwargs: object) -> _StructuredStub:
        return _StructuredStub(self._result)


def _single_page_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Scanned invoice fixture")
    return bytes(pdf.output())


def test_render_pdf_pages_to_png_returns_nonempty_image_bytes() -> None:
    pages = render_pdf_pages_to_png(_single_page_pdf())

    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"
    assert len(pages[0]) > 0


def test_vision_extractor_rejects_text_only_document() -> None:
    extractor = VisionExtractor(LLMSettings(openrouter_api_key="test-key"))

    with pytest.raises(ExtractionError, match="only accepts PDF"):
        extractor.extract(DocumentInput(text="ACME"))


def test_vision_extractor_uses_structured_response_and_vlm_metadata() -> None:
    parsed = InvoiceExtraction.model_validate(_VALID_PAYLOAD)
    extractor = VisionExtractor(
        LLMSettings(openrouter_api_key="test-key"),
        model=_FakeModel(parsed),
    )

    extraction = extractor.extract(DocumentInput(pdf_bytes=_single_page_pdf()))

    assert extraction.fields["invoice_date"].value == date(2026, 1, 31)
    assert extraction.metadata.backend == "vlm"
    assert extraction.metadata.provider == "openrouter"
    assert extraction.metadata.model == "z-ai/glm-5.3-flash"
    assert extraction.metadata.duration_ms is not None


def test_vision_extractor_propagates_timeout() -> None:
    class _TimeoutModel:
        def with_structured_output(self, *args: object, **kwargs: object) -> Any:
            raise LLMTimeoutError("slow")

    extractor = VisionExtractor(
        LLMSettings(openrouter_api_key="test-key"),
        model=_TimeoutModel(),
    )

    with pytest.raises(LLMTimeoutError, match="slow"):
        extractor.extract(DocumentInput(pdf_bytes=_single_page_pdf()))


def test_vision_extractor_propagates_structured_parse_failure() -> None:
    class _FailingChain:
        def invoke(self, messages: Any) -> Any:
            raise OutputParserException("Failed to parse")

    class _FailingModel:
        def with_structured_output(self, *args: object, **kwargs: object) -> _FailingChain:
            return _FailingChain()

    extractor = VisionExtractor(
        LLMSettings(openrouter_api_key="test-key"),
        model=_FailingModel(),
    )

    with pytest.raises(LLMParsingError, match="invalid field values"):
        extractor.extract(DocumentInput(pdf_bytes=_single_page_pdf()))
