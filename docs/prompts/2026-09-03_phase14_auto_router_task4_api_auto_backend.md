# Unit brief — f5-t4-api-auto-backend

# Goal

Expose the `auto` extraction backend in the API and make it the default when
`OPENROUTER_API_KEY` is present, on branch `agent/f5-t4-api-auto` based on the
integration branch HEAD (given at dispatch time; contains T1 classifier, T2
AutoExtractor in `extraction/routing.py`, T3 simplified LLMExtractor without the
format cascade). Also REMOVE the API-level offline fallback (`_extract_with_fallback`):
an LLM failure must surface as its typed HTTP error, never silently degrade to the
regex extractor.

OUT of scope: `extraction/*` (routing/llm/vision/ocr are merged and frozen), `eval/`,
`settings.py`, fixtures.

# Context

- Repo: `/home/pedro/Documents/prueba_tecnica_nalanda` (worktree by dispatcher),
  Python >=3.12, uv, ruff line-length 100.
- `src/docvalidator/api/main.py` anchors:
  - `JsonValidateRequest.extraction_backend: Literal["offline", "llm", "vlm", "ocr"] | None`
    (line ~47).
  - `_default_backend()` → `"llm" if os.environ.get("OPENROUTER_API_KEY") else "offline"`.
  - `_select_backend(requested)` validates the 4-value set and raises
    `APIError("unsupported_backend", ...)` otherwise (handler maps it to 501).
  - `_build_extractor(backend)` lazy-imports LLMExtractor/VisionExtractor per branch.
  - `_FALLBACK_REASONS` dict + `_extract_with_fallback(document, backend, request_id)`
    retry ONCE with `OfflineExtractor` when backend=="llm" and a runtime LLM error
    occurs, tagging `backend="offline-fallback"` + `fallback_reason`. BOTH endpoints
    (`/v1/extract`, `/v1/validate`) call it.
  - `extraction_error_handler`: `LLMConfigurationError`→503 (+hint), `LLMParsingError`/
    `LLMRequestError`→502, `LLMTimeoutError`→504, other `ExtractionError`→
    `_validation_error` (422). THIS MAPPING IS FROZEN.
- `src/docvalidator/extraction/routing.py` (from T1+T2, merged):
  `AutoExtractor(settings: LLMSettings | None = None, *, llm_extractor=None,
  vlm_extractor=None, ocr_extractor=None)`; on runtime LLM/VLM failure falls back to
  `OcrExtractor` internally; `LLMConfigurationError` propagates. `DocumentRoute` enum
  exists. Metadata from AutoExtractor: `backend="auto"`, `model` in
  {"llm","vlm","ocr"}, `fallback_reason` set when the OCR fallback fired.
- `tests/conftest.py` deletes `OPENROUTER_API_KEY` autouse — hermetic suite. Tests
  needing a key use `monkeypatch.setenv`.
- IMPORTANT test pattern (do not regress): to fake the LLM behind the API, patch the
  symbol in its SOURCE module — `monkeypatch.setattr("docvalidator.extraction.llm.LLMExtractor",
  FakeExtractor)` — because `_build_extractor` lazy-imports. For `auto`, patch
  `docvalidator.extraction.routing.AutoExtractor` the same way when needed.
- `tests/integration/test_api_llm_backend.py` currently asserts the offline fallback
  behavior (backend=="offline-fallback") in some tests — those assert behavior being
  REMOVED and must be updated. `_ExplodingExtractor` double pattern stays useful.

# Required changes

1. `src/docvalidator/api/main.py`:
   - Literal gains `"auto"`.
   - `_select_backend` accepts `{"offline","llm","vlm","ocr","auto"}`.
   - `_default_backend()` → `"auto" if os.environ.get("OPENROUTER_API_KEY") else "offline"`.
   - `_build_extractor`: `backend == "auto"` → `AutoExtractor(LLMSettings(
     openrouter_api_key=_llm_api_key()))` (lazy import inside the branch, same style
     as the other backends).
   - Replace `_extract_with_fallback` with a plain `_extract(document, backend)`
     (no retry, no `_FALLBACK_REASONS`); both endpoints call it. Delete the dead dict.
2. `tests/integration/test_api_llm_backend.py`:
   - `test_default_backend_selects_llm_only_with_api_key` → with key asserts `"auto"`.
   - Remove/rewrite the offline-fallback assertions into typed-error propagation
     tests (LLMRequestError → 502, LLMTimeoutError → 504, LLMConfigurationError → 503
     via the explicit backend) with NO `offline-fallback` backend in any response.
3. NEW `tests/integration/test_api_auto_backend.py`:
   - auto + plain text + patched AutoExtractor returning canned extraction with
     metadata backend "auto"/model "llm" → 200, response metadata matches.
   - auto without key (default) → 503 `llm_configuration_error` (no fallback).
   - explicit `extraction_backend: "auto"` respected.
   - auto + scanned PDF (build with fpdf2: render text-PDF to PNG via pypdfium2,
     re-embed via fpdf2 `pdf.image()`) + patched AutoExtractor with model "vlm" →
     200 model "vlm". (Do NOT run real OCR/VLM; the patched AutoExtractor stands in
     for routing — its internal fallback is T2's unit-tested responsibility.)
   - multipart upload of a .txt still works with default backend without key →
     backend "offline" (unchanged behavior).
4. `README.md`:
   - Backend selection contract table: default = `auto` with key / `offline` without;
     explicit override list gains `auto`.
   - Mermaid pipeline diagram: replace the explicit-lane framing with the auto router
     (txt→LLM; pdf-text→markitdown+LLM→OCR; scanned→VLM→OCR) while keeping the
     RulesEngine tail. Keep the change surgical (diagram + contract table +
     `extraction_backend` bullet + env table untouched).
5. Commit the brief unchanged as
   `docs/prompts/2026-09-03_phase14_auto_router_task4_api_auto_backend.md`.

# Constraints

- Exception→status mapping frozen (503/502/504/422/501). No changes to
  `extraction/` modules, `eval/`, `settings.py`.
- Hermetic tests (no network); suite currently 351+ passed on the integration base —
  grows, never breaks.
- Type hints; ruff clean.
- Commit message: `feat(api): auto extraction backend as default with key, drop silent offline fallback`.

# Verification commands

```
uv run --extra ocr pytest tests/integration -v
uv run --extra ocr pytest
uv run ruff check .
```
