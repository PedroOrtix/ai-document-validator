"""PDF page rendering for vision-based extraction."""

from io import BytesIO

import pypdfium2
from pypdfium2 import PdfBitmap

from docvalidator.extraction.input import ExtractionError

_RENDER_SCALE = 2.0


def render_pdf_pages_to_png(pdf_bytes: bytes) -> list[bytes]:
    """Rasterize every PDF page to PNG bytes at approximately 150 DPI."""
    try:
        document = pypdfium2.PdfDocument(pdf_bytes)
        try:
            return [_bitmap_to_png(page.render(scale=_RENDER_SCALE)) for page in document]
        finally:
            document.close()
    except Exception as exc:
        raise ExtractionError("unable to render PDF") from exc


def _bitmap_to_png(bitmap: PdfBitmap) -> bytes:
    output = BytesIO()
    bitmap.to_pil().save(output, format="PNG")
    return output.getvalue()
