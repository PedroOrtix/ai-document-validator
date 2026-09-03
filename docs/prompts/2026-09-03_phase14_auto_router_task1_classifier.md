# Unit brief — f5-t1-route-classifier

# Goal

Create the document-type classifier that decides which extraction path serves each
document, on branch `agent/f5-t1-route-classifier` from main @ `9cf4834`. This is
Task 1 of the F5 auto-routing series: a pure classifier (`txt → LLM`, `pdf with
selectable text → markitdown+LLM`, `scanned pdf → VLM`) with zero orchestration —
`AutoExtractor` is a SEPARATE later unit and must NOT be built here.

OUT of scope: `AutoExtractor`, API changes, eval changes, any edit to `llm.py`,
`vision.py`, `ocr.py`, `offline.py`, `input.py`, fixtures, README/AI_USAGE.

# Context

- Repo: `/home/pedro/Documents/prueba_tecnica_nalanda` (worktree will be created by
  the dispatcher). Python >=3.12, uv, ruff line-length 100, pytest addopts -q.
- `DocumentInput` lives in `src/docvalidator/extraction/input.py`: frozen Pydantic
  model with `text: str | None` and `pdf_bytes: bytes | None` (exactly one), plus
  `to_text()` which extracts PDF text via markitdown and raises
  `ExtractionError("PDF has no extractable text layer")` when the PDF has no text
  layer, or `ExtractionError("unable to read PDF")` when unparseable. `ExtractionError`
  subclasses `ValueError`. THE CLASSIFIER MUST REUSE `to_text()` AS THE PROBE — do not
  add a second markitdown invocation config or a pypdf-based text-length heuristic.
- Existing extractor classes (for naming reference only, do not touch):
  `LLMExtractor` (llm.py, backend="llm"), `VisionExtractor` (vision.py, backend="vlm"),
  `OcrExtractor` (ocr.py, backend="ocr"), `OfflineExtractor` (offline.py, backend="offline").
- Tests are hermetic: `tests/conftest.py` deletes `OPENROUTER_API_KEY` autouse. New
  tests must not need network, API keys, or the real RapidOCR engine.
- fpdf2 is available in the dev dependency group (see `tests/unit/test_vision_extraction.py`
  `_single_page_pdf()` for the pattern). pypdfium2 is a main dependency
  (`render_pdf_pages_to_png` in `src/docvalidator/extraction/rendering.py`).

# Required changes

1. Create `src/docvalidator/extraction/routing.py`:
   - `class DocumentRoute(StrEnum)` with exactly: `LLM = "llm"`, `MARKITDOWN = "markitdown"`,
     `VISION = "vision"`, `OCR = "ocr"`. Docstring note: `OCR` is never returned by
     `classify_document` (it is the second-echelon fallback used later by AutoExtractor).
   - Module constant `MIN_PDF_TEXT_CHARS = 150` (int).
   - `def classify_document(document: DocumentInput) -> DocumentRoute:` with behavior:
     a) `document.text is not None` → `DocumentRoute.LLM`
     b) else call `document.to_text()`; if it raises `ExtractionError` → `DocumentRoute.VISION`
     c) if `len(text) < MIN_PDF_TEXT_CHARS` → `DocumentRoute.VISION` (scanned PDF with
        residual/garbage text layer from a prior OCR process)
     d) else → `DocumentRoute.MARKITDOWN`
   - Full type hints, module docstring explaining the three routes and the threshold.
2. Create `tests/unit/test_routing.py` covering at minimum:
   - txt input routes to LLM.
   - PDF with selectable text (> threshold; build with fpdf2, pad text well over 150
     chars) routes to MARKITDOWN.
   - Scanned PDF (text PDF rendered to PNG via pypdfium2, re-embedded as an image-only
     PDF via fpdf2 `pdf.image()`) routes to VISION — markitdown returns no text layer.
   - PDF with a residual short text layer (< 150 chars) routes to VISION.
   - Threshold boundary: text length just above 150 chars routes to MARKITDOWN.
   - No network, no key needed; fast (<2s total).

# Constraints

- Preserve exact anchors: `ExtractionError` import from
  `docvalidator.extraction.input`; enum member values are API-visible later, keep them
  lowercase as specified.
- Tight diff: only the two new files. No edits to existing modules, no renames.
- Type hints on public interfaces, ruff clean (`uv run ruff check .`), pytest green.
- Commit the brief unchanged as `docs/prompts/2026-09-03_phase14_auto_router_task1_classifier.md`.
- Commit message: `feat(routing): document-type classifier (txt/pdf-text/scanned)`.

# Verification commands

```
uv run --extra ocr pytest tests/unit/test_routing.py -v
uv run --extra ocr pytest
uv run ruff check .
```

All three must pass: new tests green, full suite green (baseline 345 passed,
3 skipped, 29 xfailed — that count may grow by the new tests, never shrink), ruff clean.
