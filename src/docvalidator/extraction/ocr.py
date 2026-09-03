"""Local document OCR using RapidOCR (PP-OCRv5, ONNX) and the regex parser.

This extractor is the credential-free floor of the system: it needs no API
key and no network (ONNX weights ship in the container image), so reviewers
can run every lane without paid credentials.
"""

import time
from collections.abc import Callable
from typing import Any, Protocol

from docvalidator.domain.models import DocumentExtraction, ExtractionMetadata
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.parsing import RegexFieldParser
from docvalidator.settings import ValidatorOcrSettings


class RenderedPage(Protocol):
    """Bitmap rendered from one PDF page."""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def mode(self) -> str: ...


_RUNTIME_CACHE: dict[str, Any] = {}


def _page_scale(dpi: int) -> float:
    return dpi / 72.0


def _render_page_bitmaps(document: DocumentInput, dpi: int) -> list[RenderedPage]:
    """Rasterize a PDF in isolation; F3/F4 will consolidate rendering helpers."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(document.pdf_bytes)
    except Exception as exc:
        raise ExtractionError("unable to render PDF") from exc
    try:
        images = [page.render(scale=_page_scale(dpi)).to_pil() for page in pdf]
    except Exception as exc:
        raise ExtractionError("unable to render PDF") from exc
    finally:
        pdf.close()
    pages: list[RenderedPage] = []
    try:
        for image in images:
            pages.append(image.convert("RGB"))
    except Exception as exc:
        raise ExtractionError("unable to render PDF") from exc
    return pages


def _load_runtime() -> Any:
    if "rapidocr" in _RUNTIME_CACHE:
        return _RUNTIME_CACHE["rapidocr"]
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    _RUNTIME_CACHE["rapidocr"] = engine
    return engine


def _rapidocr_fn(pages: list[RenderedPage]) -> str:
    """Run RapidOCR (PP-OCRv5 detection+recognition, ONNX Runtime) over pages."""
    import numpy as np

    if not pages:
        return ""
    engine = _load_runtime()
    lines: list[str] = []
    for page in pages:
        image = np.asarray(page)
        result, _ = engine(image)
        if result:
            for item in result:
                # RapidOCR rows are [box, text, confidence]
                if len(item) >= 2 and item[1]:
                    lines.append(str(item[1]))
    return "\n".join(lines)


class OcrExtractor(Extractor):
    """Extract invoice fields from document text or OCR'ed page bitmaps.

    Plain text bypasses the OCR engine and goes straight to the deterministic
    regex parser (the text is already machine-readable). PDFs are rasterized,
    run through local RapidOCR, and the OCR text is parsed the same way.
    """

    backend = "ocr"
    model_name = "pp-ocrv5-onnx"

    def __init__(
        self,
        settings: ValidatorOcrSettings | None = None,
        ocr_fn: Callable[[list[RenderedPage]], str] | None = None,
    ) -> None:
        self.settings = settings or ValidatorOcrSettings()
        self.ocr_fn = ocr_fn or _rapidocr_fn
        self._parser = RegexFieldParser()

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        started_at = time.perf_counter()
        if document.text is not None:
            fields = self._parser.extract_fields(document.to_text())
        else:
            pages = _render_page_bitmaps(document, self.settings.validator_ocr_dpi)
            ocr_text = self.ocr_fn(pages)
            if not ocr_text.strip():
                raise ExtractionError("OCR produced no readable text")
            fields = self._parser.extract_fields(ocr_text)

        duration_ms = (time.perf_counter() - started_at) * 1000
        return DocumentExtraction(
            fields=fields,
            metadata=ExtractionMetadata(
                backend=self.backend,
                model=self.model_name,
                provider="rapidocr-local",
                duration_ms=round(duration_ms, 3),
            ),
        )
