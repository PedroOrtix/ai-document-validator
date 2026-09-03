"""Tests for the local RapidOCR extractor."""

from pathlib import Path
from unittest.mock import patch

import pypdfium2
import pytest

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.ocr import OcrExtractor

INVOICE_TEXT = """Helios Limited
Invoice No: INV-2026-0451
Invoice Date: 2026-03-14
Total: EUR 1,210.00
VAT: DE123456789
"""
SCANNED_PDF = (
    Path(__file__).parents[2] / "fixtures" / "golden" / "scan_pdf_es_t0_0.pdf"
)


def test_ocr_pdf_with_fake_engine_returns_regex_extraction() -> None:
    rendered_pages: list[object] = []

    def ocr_fn(pages: list[bytes]) -> str:
        rendered_pages.extend(pages)
        return INVOICE_TEXT

    extractor = OcrExtractor(ocr_fn=ocr_fn)
    extraction = extractor.extract(DocumentInput(pdf_bytes=SCANNED_PDF.read_bytes()))

    assert isinstance(extraction, DocumentExtraction)
    assert extraction.metadata.backend == "ocr"
    assert extraction.metadata.model == "pp-ocrv5-onnx"
    assert extraction.metadata.provider == "rapidocr-local"
    assert extraction.metadata.total_tokens is None
    assert extraction.metadata.duration_ms is not None
    assert extraction.fields["invoice_number"].value == "INV-2026-0451"
    assert rendered_pages
    assert extraction.fields["supplier_name"].value == "Helios Limited"
    assert extraction.fields["total_amount"].value == 1210.0


def test_empty_ocr_output_raises_typed_error() -> None:
    extractor = OcrExtractor(ocr_fn=lambda _pages: "\n \n")
    with pytest.raises(ValueError, match="OCR produced no readable text"):
        extractor.extract(DocumentInput(pdf_bytes=SCANNED_PDF.read_bytes()))


def test_render_failure_raises_typed_error() -> None:
    with patch.object(pypdfium2.PdfDocument, "__init__", side_effect=RuntimeError("corrupt")):
        extractor = OcrExtractor(ocr_fn=lambda _pages: INVOICE_TEXT)
        with pytest.raises(ValueError, match="unable to render PDF"):
            extractor.extract(DocumentInput(pdf_bytes=b"not-a-pdf"))


def test_text_document_skips_ocr() -> None:
    called = False

    def ocr_fn(pages: list[bytes]) -> str:
        nonlocal called
        called = True
        return ""

    extraction = OcrExtractor(ocr_fn=ocr_fn).extract(DocumentInput(text=INVOICE_TEXT))
    assert called is False
    assert extraction.metadata.backend == "ocr"
    assert extraction.fields["invoice_number"].value == "INV-2026-0451"
