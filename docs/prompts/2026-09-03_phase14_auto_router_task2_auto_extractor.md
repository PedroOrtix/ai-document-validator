# Unit brief — f5-t2-auto-extractor

## Goal

Add `AutoExtractor` to `src/docvalidator/extraction/routing.py`, on branch
`agent/f5-t2-auto-extractor` based on `0f2abf6` (Task 1 already committed there:
`DocumentRoute` enum + `classify_document()` in the same file). AutoExtractor
orchestrates the EXISTING extractors per document route:

- TXT → `LLMExtractor`
- PDF with selectable text (`DocumentRoute.MARKITDOWN`) → `LLMExtractor` (markitdown
  happens inside `DocumentInput.to_text()`), falling back to `OcrExtractor` on
  request/parsing/timeout failures
- Scanned PDF (`DocumentRoute.VISION`) → `VisionExtractor`, falling back to
  `OcrExtractor` on request/parsing/timeout failures

OUT of scope: `api/main.py`, `eval/`, `llm.py`, `vision.py`, `ocr.py`,
`offline.py`, `input.py` (another parallel unit is editing llm.py/vision.py — do
NOT touch them), README/AI_USAGE. Do not modify `classify_document` or
`DocumentRoute` semantics; only ADD to routing.py.

## Context

- Repo: `/home/pedro/Documents/prueba_tecnica_nalanda` (worktree by dispatcher),
  Python >=3.12, uv, ruff line-length 100. Base branch already contains
  `src/docvalidator/extraction/routing.py` with `DocumentRoute(StrEnum)`
  (`LLM="llm"`, `MARKITDOWN="markitdown"`, `VISION="vision"`, `OCR="ocr"`) and
  `classify_document()`.
- `src/docvalidator/extraction/base.py`: `class Extractor(ABC)` with
  `extract(self, document: DocumentInput) -> DocumentExtraction`.
- `src/docvalidator/extraction/llm.py`: `LLMExtractor(settings=None, model=None,
  structured_model=None)`; exceptions `LLMConfigurationError`, `LLMParsingError`,
  `LLMRequestError`, `LLMTimeoutError` — ALL subclass `ExtractionError` (defined in
  `extraction/input.py`). `LLMSettings` lives in `docvalidator.settings`
  (`validator_llm_model`, `validator_vlm_model`, `openrouter_api_key`, ...).
- `src/docvalidator/extraction/vision.py`: `VisionExtractor(settings=None, model=None,
  structured_model=None)` — same constructor signature as LLMExtractor.
- `src/docvalidator/extraction/ocr.py`: `OcrExtractor(settings=None, ocr_fn=None)`;
  may raise `ExtractionError("OCR produced no readable text")`. Uses
  `ValidatorOcrSettings` (default dpi 200).
- `src/docvalidator/domain/models.py`: `ExtractionMetadata(backend, duration_ms,
  model, provider, total_tokens, fallback_reason)` — all frozen models; use
  `extraction.model_copy(update={"metadata": extraction.metadata.model_copy(
  update={...})})` for metadata rewrites (see `ocr.py:112-123` for the pattern).
- The API-later unit will surface `LLMConfigurationError` as 503 — configuration
  errors must NEVER be swallowed into an OCR fallback (a degraded result would mask a
  misconfiguration). That is why fallback eligibility is limited to the three runtime
  failure classes below.
- Lazy-import pattern to follow: `api/main.py::_build_extractor` imports extractor
  classes INSIDE the factory functions. Do the same here (module-level import of
  llm/vision would make routing.py heavy and create import-order fragility).
- Tests are hermetic (no network/keys). Current suite on the base branch:
  351 passed, 3 skipped, 29 xfailed.

## Required changes

1. `src/docvalidator/extraction/routing.py` — add (keep existing code untouched):
   - `class AutoExtractor(Extractor)` with docstring: routes each document to its
     intended path; txt→LLM, pdf-text→LLM (→OCR), scanned→VLM (→OCR).
   - `__init__(self, settings: LLMSettings | None = None, *, llm_extractor:
     Extractor | None = None, vlm_extractor: Extractor | None = None,
     ocr_extractor: Extractor | None = None) -> None` — stores overrides for test
     injection (constructor seams, mirroring `OcrExtractor(ocr_fn=...)`).
   - Private cached accessors `_llm()`, `_vlm()`, `_ocr()` returning the injected
     instance or lazily building the default (`LLMExtractor(self.settings)` /
     `VisionExtractor(self.settings)` / `OcrExtractor()`), imported inside the
     accessor (lazy-import pattern).
   - `extract()`:
     ```
     route = classify_document(document)
     LLM          → self._wrap(self._llm().extract(document), sub_route=DocumentRoute.LLM)
     MARKITDOWN   → try llm path; on (LLMRequestError | LLMParsingError |
                    LLMTimeoutError) → OCR fallback, fallback_reason="llm-unavailable"
     VISION       → try vlm path; on (LLMRequestError | LLMParsingError |
                    LLMTimeoutError) → OCR fallback, fallback_reason="vlm-unavailable"
     ```
     `LLMConfigurationError` (and any other exception) propagates — never masked.
     When no `ocr_extractor` override was injected AND the fallback would use the
     default, still attempt the default OCR; only re-raise when OCR itself raises.
   - `_wrap(self, extraction: DocumentExtraction, *, sub_route: DocumentRoute,
     fallback_reason: str | None = None) -> DocumentExtraction`: returns a copy with
     `metadata.backend="auto"`, `metadata.model=sub_route.value`,
     `metadata.fallback_reason=fallback_reason` (None stays None); PRESERVES
     `provider`, `total_tokens`, `duration_ms` from the delegated extraction.
2. `tests/unit/test_routing.py` — add `TestAutoExtractor` (keep existing classify
   tests): fakes defined in the test module:
   - `_RecordingExtractor`: counts `extract` calls, returns a canned
     `DocumentExtraction` (six fields, backend "llm"/"vlm"/"ocr" as needed,
     `provider="fake"`, `total_tokens=10`).
   - `_FailingExtractor`: raises a constructor-configured exception on `extract`.
   Tests (at minimum):
   - txt document → only llm called; metadata.backend=="auto", model=="llm",
     provider/total_tokens preserved from the fake.
   - selectable-text PDF → llm called once; model=="llm".
   - scanned PDF with failing VLM → vlm called once, ocr called once;
     model=="ocr", fallback_reason=="vlm-unavailable".
   - scanned PDF with failing VLM and failing OCR → `ExtractionError` propagates
     (no third retry, no silent nulls).
   - selectable-text PDF with failing LLM (LLMRequestError) → ocr fallback with
     fallback_reason=="llm-unavailable".
   - selectable-text PDF with `LLMConfigurationError` → propagates (NO fallback).
   - scanned PDF with working VLM → ocr NOT called.
   Use real PDFs built with fpdf2 (reuse the `_text_pdf`/`_scanned_pdf` helpers
   already in test_routing.py) — do not bypass `classify_document`.
3. Commit this brief unchanged as
   `docs/prompts/2026-09-03_phase14_auto_router_task2_auto_extractor.md`.

## Constraints

- Only ADD to routing.py (existing `DocumentRoute`/`classify_document`/`MIN_PDF_TEXT_CHARS`
  unchanged) and APPEND tests. No edits to llm.py/vision.py (parallel unit owns them),
  api, eval, docs other than the brief file.
- Type hints on public interfaces; ruff clean; no network.
- Sub-route values in metadata must be the enum VALUES ("llm"/"vlm"/"ocr") — the eval
  slices on them later.
- Commit message: `feat(routing): AutoExtractor with structural VLM→OCR / LLM→OCR fallback`.

## Verification commands

```
uv run --extra ocr pytest tests/unit/test_routing.py -v
uv run --extra ocr pytest
uv run ruff check .
```

Full suite green (baseline on this base: 351 passed / 3 skipped / 29 xfailed; grows by
the new tests, never shrinks), ruff clean.
