"""Unit tests for document extraction routing."""

from io import BytesIO

import pytest
from fpdf import FPDF
from pypdfium2 import PdfDocument

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
)
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.llm import LLMConfigurationError, LLMRequestError
from docvalidator.extraction.routing import (
    MIN_PDF_TEXT_CHARS,
    AutoExtractor,
    classify_document,
)


def _text_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, text=text)
    return bytes(pdf.output())


def _scanned_pdf(pdf_bytes: bytes) -> bytes:
    pages = PdfDocument(pdf_bytes)
    scanned = FPDF()
    for page in pages:
        bitmap = BytesIO()
        page.render().to_pil().save(bitmap, format="PNG")
        scanned.add_page()
        scanned.image(bitmap)
    return bytes(scanned.output())


def test_classify_text_document_as_llm() -> None:
    document = DocumentInput(text="Invoice text")

    assert classify_document(document) == "llm"


def test_classify_selectable_text_pdf_as_markitdown() -> None:
    text = "Selectable invoice content " * 10
    document = DocumentInput(pdf_bytes=_text_pdf(text))

    assert classify_document(document) == "markitdown"


def test_classify_scanned_pdf_without_text_layer_as_vision() -> None:
    scanned_pdf = _scanned_pdf(_text_pdf("Scanned invoice"))
    document = DocumentInput(pdf_bytes=scanned_pdf)

    assert classify_document(document) == "vision"


def test_classify_residual_short_text_pdf_as_vision() -> None:
    document = DocumentInput(pdf_bytes=_text_pdf("Invoice 123"))

    assert classify_document(document) == "vision"


def test_classify_pdf_just_above_threshold_as_markitdown() -> None:
    text = "Invoice " + "a" * MIN_PDF_TEXT_CHARS
    document = DocumentInput(pdf_bytes=_text_pdf(text))

    assert classify_document(document) == "markitdown"


def test_classify_unparseable_pdf_as_vision() -> None:
    document = DocumentInput(pdf_bytes=b"not a pdf")

    assert classify_document(document) == "vision"


class _RecordingExtractor:
    """Extractor test double that records calls and returns canned output."""

    def __init__(self, backend: str, model: str) -> None:
        self.backend = backend
        self.model = model
        self.calls: list[DocumentInput] = []

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        self.calls.append(document)
        return DocumentExtraction(
            fields={
                name: ExtractedField(value=None, confidence=0)
                for name in (
                    "supplier_name",
                    "invoice_number",
                    "invoice_date",
                    "total_amount",
                    "currency",
                    "tax_id",
                )
            },
            metadata=ExtractionMetadata(
                backend=self.backend,
                model=self.model,
                provider="fake",
                total_tokens=10,
                duration_ms=12.5,
            ),
        )


class _FailingExtractor:
    """Extractor test double that always raises the configured exception."""

    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls: list[DocumentInput] = []

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        self.calls.append(document)
        raise self.exception


class TestAutoExtractor:
    """Tests for extraction route orchestration and metadata wrapping."""

    def test_text_document_routes_to_llm_only(self) -> None:
        llm = _RecordingExtractor("llm", "llm-model")
        vlm = _RecordingExtractor("vlm", "vlm-model")
        ocr = _RecordingExtractor("ocr", "ocr-model")
        extractor = AutoExtractor(llm_extractor=llm, vlm_extractor=vlm, ocr_extractor=ocr)

        extraction = extractor.extract(DocumentInput(text="Invoice text"))

        assert len(llm.calls) == 1
        assert not vlm.calls
        assert not ocr.calls
        assert extraction.metadata.backend == "auto"
        assert extraction.metadata.model == "llm"
        assert extraction.metadata.provider == "fake"
        assert extraction.metadata.total_tokens == 10
        assert extraction.metadata.duration_ms == 12.5
        assert extraction.metadata.fallback_reason is None

    def test_selectable_text_pdf_routes_to_llm_once(self) -> None:
        llm = _RecordingExtractor("llm", "llm-model")
        extractor = AutoExtractor(llm_extractor=llm)
        document = DocumentInput(pdf_bytes=_text_pdf("Selectable invoice content " * 10))

        extraction = extractor.extract(document)

        assert len(llm.calls) == 1
        assert extraction.metadata.model == "llm"

    def test_scanned_pdf_falls_back_from_vlm_to_ocr(self) -> None:
        vlm = _FailingExtractor(LLMRequestError("VLM unavailable"))
        ocr = _RecordingExtractor("ocr", "ocr-model")
        extractor = AutoExtractor(vlm_extractor=vlm, ocr_extractor=ocr)
        document = DocumentInput(pdf_bytes=_scanned_pdf(_text_pdf("Scanned invoice")))

        extraction = extractor.extract(document)

        assert len(vlm.calls) == 1
        assert len(ocr.calls) == 1
        assert extraction.metadata.backend == "auto"
        assert extraction.metadata.model == "ocr"
        assert extraction.metadata.fallback_reason == "vlm-unavailable"

    def test_scanned_pdf_re_raises_when_ocr_fails(self) -> None:
        vlm = _FailingExtractor(LLMRequestError("VLM unavailable"))
        ocr = _FailingExtractor(ExtractionError("OCR produced no readable text"))
        extractor = AutoExtractor(vlm_extractor=vlm, ocr_extractor=ocr)
        document = DocumentInput(pdf_bytes=_scanned_pdf(_text_pdf("Scanned invoice")))

        with pytest.raises(ExtractionError, match="OCR produced no readable text"):
            extractor.extract(document)

        assert len(vlm.calls) == 1
        assert len(ocr.calls) == 1

    def test_selectable_text_pdf_falls_back_from_llm_to_ocr(self) -> None:
        llm = _FailingExtractor(LLMRequestError("LLM unavailable"))
        ocr = _RecordingExtractor("ocr", "ocr-model")
        extractor = AutoExtractor(llm_extractor=llm, ocr_extractor=ocr)
        document = DocumentInput(pdf_bytes=_text_pdf("Selectable invoice content " * 10))

        extraction = extractor.extract(document)

        assert len(llm.calls) == 1
        assert len(ocr.calls) == 1
        assert extraction.metadata.model == "ocr"
        assert extraction.metadata.fallback_reason == "llm-unavailable"

    def test_selectable_text_pdf_does_not_fallback_on_configuration_error(self) -> None:
        llm = _FailingExtractor(LLMConfigurationError("missing API key"))
        ocr = _RecordingExtractor("ocr", "ocr-model")
        extractor = AutoExtractor(llm_extractor=llm, ocr_extractor=ocr)
        document = DocumentInput(pdf_bytes=_text_pdf("Selectable invoice content " * 10))

        with pytest.raises(LLMConfigurationError, match="missing API key"):
            extractor.extract(document)

        assert len(llm.calls) == 1
        assert not ocr.calls

    def test_scanned_pdf_does_not_use_ocr_when_vlm_succeeds(self) -> None:
        vlm = _RecordingExtractor("vlm", "vlm-model")
        ocr = _RecordingExtractor("ocr", "ocr-model")
        extractor = AutoExtractor(vlm_extractor=vlm, ocr_extractor=ocr)
        document = DocumentInput(pdf_bytes=_scanned_pdf(_text_pdf("Scanned invoice")))

        extraction = extractor.extract(document)

        assert len(vlm.calls) == 1
        assert not ocr.calls
        assert extraction.metadata.model == "vlm"
        assert extraction.metadata.fallback_reason is None
