"""Classify documents for their extraction route.

Text documents use the LLM extractor. PDFs with a meaningful selectable text
layer use markitdown first, while PDFs that cannot provide enough selectable
text use the vision-language-model (VLM) route. ``MIN_PDF_TEXT_CHARS`` rejects
residual or garbage text layers produced by a prior OCR process: at least 150
characters are required before treating PDF text as selectable.
"""

from enum import StrEnum

from docvalidator.extraction.input import DocumentInput, ExtractionError

MIN_PDF_TEXT_CHARS = 150


class DocumentRoute(StrEnum):
    """Extraction routes selected by :func:`classify_document`.

    ``OCR`` is never returned by ``classify_document``; it is the second-echelon
    fallback used later by ``AutoExtractor``.
    """

    LLM = "llm"
    MARKITDOWN = "markitdown"
    VISION = "vision"
    OCR = "ocr"


def classify_document(document: DocumentInput) -> DocumentRoute:
    """Return the extraction route for a text-only or PDF document."""

    if document.text is not None:
        return DocumentRoute.LLM

    try:
        text = document.to_text()
    except ExtractionError:
        return DocumentRoute.VISION
    if len(text) < MIN_PDF_TEXT_CHARS:
        return DocumentRoute.VISION
    return DocumentRoute.MARKITDOWN
