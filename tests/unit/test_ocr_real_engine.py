"""Optional real-engine coverage for RapidOCR (PP-OCRv5 ONNX)."""

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

GOLDEN = Path(__file__).parents[2] / "fixtures" / "golden"
CASE_IDS = ["scan_pdf_en_t0_0", "scan_pdf_es_t0_0"]

try:
    import rapidocr_onnxruntime  # noqa: F401

    rapidocr = rapidocr_onnxruntime
except ImportError:
    rapidocr = None


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_OCR") != "1" or rapidocr is None,
    reason="set RUN_REAL_OCR=1 and install the [ocr] extra to run the real engine",
)
@pytest.mark.parametrize("case_id", CASE_IDS)
def test_real_rapidocr_extracts_at_least_four_fields(case_id: str) -> None:
    from docvalidator.extraction.input import DocumentInput
    from docvalidator.extraction.ocr import OcrExtractor

    pdf = (GOLDEN / f"{case_id}.pdf").read_bytes()
    expected = json.loads((GOLDEN / f"{case_id}.expected.json").read_text())
    extraction = OcrExtractor().extract(
        DocumentInput(pdf_bytes=pdf, filename=f"{case_id}.pdf")
    )
    hits = sum(
        1
        for name, value in expected["expected_fields"].items()
        if value is not None and extraction.fields[name].value is not None
    )
    assert hits >= 4, f"expected >=4 extracted fields, got {hits}"
