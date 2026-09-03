"""Classify documents for their extraction route.

Text documents use the LLM extractor. PDFs with a meaningful selectable text
layer use markitdown first, while PDFs that cannot provide enough selectable
text use the vision-language-model (VLM) route. ``MIN_PDF_TEXT_CHARS`` rejects
residual or garbage text layers produced by a prior OCR process: at least 150
characters are required before treating PDF text as selectable.
"""

from enum import StrEnum

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.llm import (
    LLMParsingError,
    LLMRequestError,
    LLMTimeoutError,
)
from docvalidator.settings import LLMSettings

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


class AutoExtractor(Extractor):
    """Route documents to their intended extractor.

    Plain text uses the LLM extractor. PDFs with selectable text also use the
    LLM extractor, with OCR as the fallback for runtime failures. Scanned PDFs
    use the VLM extractor, also falling back to OCR for runtime failures.
    """

    def __init__(
        self,
        settings: LLMSettings | None = None,
        *,
        llm_extractor: Extractor | None = None,
        vlm_extractor: Extractor | None = None,
        ocr_extractor: Extractor | None = None,
    ) -> None:
        """Store extractor overrides so tests can replace service-backed paths."""
        self.settings = settings
        self._llm_override = llm_extractor
        self._vlm_override = vlm_extractor
        self._ocr_override = ocr_extractor

    def _llm(self) -> Extractor:
        if self._llm_override is None:
            from docvalidator.extraction.llm import LLMExtractor

            self._llm_override = LLMExtractor(self.settings)
        return self._llm_override

    def _vlm(self) -> Extractor:
        if self._vlm_override is None:
            from docvalidator.extraction.vision import VisionExtractor

            self._vlm_override = VisionExtractor(self.settings)
        return self._vlm_override

    def _ocr(self) -> Extractor:
        if self._ocr_override is None:
            from docvalidator.extraction.ocr import OcrExtractor

            self._ocr_override = OcrExtractor()
        return self._ocr_override

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        """Extract fields through the route selected for ``document``."""
        route = classify_document(document)

        if route is DocumentRoute.LLM:
            extraction = self._llm().extract(document)
            return self._wrap(extraction, sub_route=DocumentRoute.LLM)

        if route is DocumentRoute.MARKITDOWN:
            primary_route = DocumentRoute.LLM
            fallback_reason = "llm-unavailable"
            primary_extractor = self._llm()
        else:
            primary_route = DocumentRoute.VISION
            fallback_reason = "vlm-unavailable"
            primary_extractor = self._vlm()

        try:
            extraction = primary_extractor.extract(document)
        except (LLMRequestError, LLMParsingError, LLMTimeoutError):
            return self._wrap(
                self._ocr().extract(document),
                sub_route=DocumentRoute.OCR,
                fallback_reason=fallback_reason,
            )
        return self._wrap(extraction, sub_route=primary_route)

    def _wrap(
        self,
        extraction: DocumentExtraction,
        *,
        sub_route: DocumentRoute,
        fallback_reason: str | None = None,
    ) -> DocumentExtraction:
        sub_route_value = "vlm" if sub_route is DocumentRoute.VISION else sub_route.value
        return extraction.model_copy(
            update={
                "metadata": extraction.metadata.model_copy(
                    update={
                        "backend": "auto",
                        "model": sub_route_value,
                        "fallback_reason": fallback_reason,
                    }
                )
            }
        )
