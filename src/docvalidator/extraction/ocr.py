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


def _sort_boxes_reading_order(result: list[Any]) -> str:
    """Group RapidOCR boxes into horizontal lines and sort in reading order."""
    if not result:
        return ""
    items: list[dict[str, Any]] = []
    for item in result:
        if len(item) < 2 or not item[1]:
            continue
        box = item[0]
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except (ValueError, TypeError, IndexError):
            continue
        y_min, y_max = min(ys), max(ys)
        x_min, x_max = min(xs), max(xs)
        items.append(
            {
                "text": str(item[1]).strip(),
                "x_min": x_min,
                "x_max": x_max,
                "y_min": y_min,
                "y_max": y_max,
                "y_center": (y_min + y_max) / 2.0,
                "height": max(y_max - y_min, 1.0),
            }
        )
    if not items:
        return ""

    items.sort(key=lambda it: it["y_center"])
    lines: list[list[dict[str, Any]]] = []
    for it in items:
        matched_line: list[dict[str, Any]] | None = None
        for line in lines:
            line_y = sum(b["y_center"] for b in line) / len(line)
            line_h = sum(b["height"] for b in line) / len(line)
            if abs(it["y_center"] - line_y) < max(it["height"], line_h) * 0.5:
                matched_line = line
                break
        if matched_line is not None:
            matched_line.append(it)
        else:
            lines.append([it])

    lines.sort(key=lambda line: sum(b["y_center"] for b in line) / len(line))
    text_lines: list[str] = []
    for line in lines:
        line.sort(key=lambda b: b["x_min"])
        text_lines.append(" ".join(b["text"] for b in line))
    return "\n".join(text_lines)


def _rapidocr_fn(pages: list[RenderedPage]) -> str:
    """Run RapidOCR (PP-OCRv5 detection+recognition, ONNX Runtime) over pages."""
    import numpy as np

    if not pages:
        return ""
    engine = _load_runtime()
    page_texts: list[str] = []
    for page in pages:
        image = np.asarray(page)
        result, _ = engine(image)
        if result:
            page_text = _sort_boxes_reading_order(result)
            if page_text:
                page_texts.append(page_text)
    return "\n".join(page_texts)


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
            model_name = "regex-parser"
            provider_name = "local-deterministic"
        else:
            pages = _render_page_bitmaps(document, self.settings.validator_ocr_dpi)
            ocr_text = self.ocr_fn(pages)
            if not ocr_text.strip():
                raise ExtractionError("OCR produced no readable text")
            fields = self._parser.extract_fields(ocr_text)
            fields = {
                name: field.model_copy(update={"page_hint": 1})
                if field.value is not None
                else field
                for name, field in fields.items()
            }
            model_name = self.model_name
            provider_name = "rapidocr-local"

        duration_ms = (time.perf_counter() - started_at) * 1000
        return DocumentExtraction(
            fields=fields,
            metadata=ExtractionMetadata(
                backend=self.backend,
                model=model_name,
                provider=provider_name,
                duration_ms=round(duration_ms, 3),
            ),
        )
