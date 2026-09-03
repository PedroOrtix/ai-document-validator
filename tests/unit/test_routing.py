"""Unit tests for document extraction routing."""

from io import BytesIO

from fpdf import FPDF
from pypdfium2 import PdfDocument

from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.routing import MIN_PDF_TEXT_CHARS, classify_document


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
