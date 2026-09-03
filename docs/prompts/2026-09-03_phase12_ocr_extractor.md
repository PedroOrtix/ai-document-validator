# Phase 12: OCR Extractor (F2)

Build `OcrExtractor` around **PaddleOCR-VL-1.6** (`PaddlePaddle/PaddleOCR-VL-1.6`). Render image-only
PDFs with `pypdfium2` at `VALIDATOR_OCR_DPI` (default 200), run the local 0.9B document-parse VLM,
extract readable plain text, then delegate to the existing `OfflineExtractor` regex heuristics.

Requirements:

- Keep a callable OCR seam for network-free tests and future engine swaps.
- Use typed failures for render and empty OCR output; preserve `backend="ocr"` metadata and
  `model/provider/token/duration` fields.
- Wire `"ocr"` into the API backend literal and factory without changing the default backend.
- Add network-free fake-seam tests plus slow real-engine tests skipped unless `RUN_REAL_OCR=1`.
- Docker must install the OCR stack and predownload weights at build time; runtime is offline.
- Document model rationale, RapidOCR/PP-OCRv4 rejection, CPU latency, and one-line AI attribution.
