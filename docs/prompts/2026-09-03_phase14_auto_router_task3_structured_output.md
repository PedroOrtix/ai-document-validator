# Unit brief — f5-t3-structured-output-no-cascade

# Goal

Remove the structured-output FORMAT cascade (`json_schema → json_mode → raw`) from
`LLMExtractor` and its inheritance in `VisionExtractor`, on branch
`agent/f5-t3-structured-output-no-cascade` from main @ `9cf4834`. Owner decision: the
LangChain `with_structured_output` object MUST work — one call, one format. A
non-conforming response is a typed error (surfaced as 502/503/504 by the API), never a
silent retry with another format. Structural fallbacks (VLM→OCR in a later unit) stay
out of scope here.

OUT of scope: `api/main.py` (its `_extract_with_fallback` offline fallback is a LATER
unit), `ocr.py`, `offline.py`, `input.py`, `routing.py`, `settings.py`, eval, README.

# Context

- Repo: `/home/pedro/Documents/prueba_tecnica_nalanda` (worktree created by dispatcher).
  Python >=3.12, uv, ruff line-length 100.
- `src/docvalidator/extraction/llm.py` today: `LLMExtractor._invoke_structured_output`
  implements the cascade (`_method` state machine, `_invoke_structured`,
  `_extract_raw`, `_UnsupportedResponseFormat`, `_strip_markdown_fences`,
  `_FENCE_PATTERN`), `parse_llm_response` does defensive raw JSON parsing, and
  `_StructuredResponse = dict[str, Any] | InvoiceExtraction | AIMessage |
  DocumentExtraction` is the union those paths produce.
- `src/docvalidator/extraction/vision.py:14-19` imports `_StructuredResponse` and
  `_build_chat_model` from llm.py and calls the inherited `_invoke_structured_output` +
  `_parse_structured_output`; its comment at lines 74-78 documents the (now-to-be-removed)
  cascade rationale.
- Exception taxonomy is API-CONTRACT (see `api/main.py` `extraction_error_handler`):
  `LLMConfigurationError`→503, `LLMParsingError`/`LLMRequestError`→502,
  `LLMTimeoutError`→504. Class names, inheritance (all subclass `ExtractionError`),
  and the mapping MUST NOT change.
- Test fakes (contract to keep): `tests/unit/test_vision_extraction.py` `_FakeModel`
  exposes `with_structured_output(*args, **kwargs)` returning `_StructuredStub` whose
  `invoke()` returns `{"parsed": InvoiceExtraction, "raw": None}`. After this refactor
  the ONLY accepted chain shape is `{"parsed": InvoiceExtraction | None, "raw":
  AIMessage | None}` (that is what `include_raw=True` yields).
- `parse_structured_extraction(fields, response, model)` is a PUBLIC mapper used by
  tests and keeps its signature. `_total_tokens` already reads usage from an AIMessage
  (via `usage_metadata` or `response_metadata`) — the raw AIMessage from the chain is
  how `total_tokens` keeps feeding the cost-per-doc metric in `eval/lanes.py`.
- `_build_chat_model` stays exactly as is (`vision.py` imports it).
- Tests are hermetic (no network/keys). Baseline suite: 345 passed, 3 skipped,
  29 xfailed.

# Required changes

1. `src/docvalidator/extraction/llm.py` — simplify to a single structured-output call:
   - `_invoke` builds messages, then `chain = model.with_structured_output(
     InvoiceExtraction, include_raw=True)` and invokes ONCE; classify exceptions via
     the existing `_classify_error`.
   - `_parse_structured_output`: accept ONLY `{"parsed": InvoiceExtraction | None,
     "raw": AIMessage | None}`. `parsed is None` or missing → `LLMParsingError`.
     Delegate to `parse_structured_extraction(parsed, raw, model)`. Keep the
     `DocumentExtraction` passthrough REMOVED (no raw-cascade step exists anymore).
   - Delete: `_method`, `_invoke_structured_output`, `_invoke_structured`,
     `_extract_raw`, `_UnsupportedResponseFormat`, `_FENCE_PATTERN`,
     `_strip_markdown_fences`, `parse_llm_response`, `_message_text` (if unused after
     the above), `_uses_response_format` (note: it is currently dead code after the
     `return` in `_build_chat_model` — removing it fixes that too), and the
     `json_invalid` special-case branch in `_classify_error`.
   - `_classify_error` keeps the same typed outcomes MINUS cascade signaling:
     auth/401/403 → `LLMConfigurationError`; other `APIStatusError` → `LLMRequestError`;
     timeouts → `LLMTimeoutError`; `OutputParserException`/`ValidationError` →
     `LLMParsingError` (no more `_UnsupportedResponseFormat` return); connection →
     `LLMRequestError`; fallback → `LLMRequestError("LLM extraction failed")`.
   - `_StructuredResponse`/`_StructuredModel` type aliases: narrow to the single chain
     shape (or remove if unused).
2. `src/docvalidator/extraction/vision.py`:
   - Update imports (drop `_StructuredResponse`; keep `VISION_INSTRUCTION`,
     `LLMExtractor`, `_build_chat_model`).
   - `_invoke_page` uses the simplified inherited path; update the stale comment block
     (lines ~74-78) to state the NEW contract: single structured call, typed failures,
     no format cascade (owner decision).
3. `tests/unit/test_llm_parsing.py`:
   - Remove `TestParseLlmResponse` and the `parse_llm_response` import.
   - ADD regression test: a fake model whose `with_structured_output` chain raises
     `OutputParserException` → `LLMExtractor.extract` raises `LLMParsingError` (proves
     no silent raw retry).
   - ADD test: chain returns `{"parsed": None, "raw": <AIMessage with usage_metadata
     total_tokens=42>}` → `LLMParsingError` (unparseable structured response is an
     error, not a null-field document).
4. `tests/unit/test_vision_extraction.py`:
   - Keep `_FakeModel`/`_StructuredStub` (the `{"parsed","raw"}` contract).
   - ADD a failure-path test: VLM chain raising `OutputParserException` →
     `LLMParsingError` propagates from `VisionExtractor.extract`.
5. Commit this brief unchanged as
   `docs/prompts/2026-09-03_phase14_auto_router_task3_structured_output.md`.

# Constraints

- Exception class names, messages, and the 503/502/504 mapping preserved — the API
  handler in `api/main.py` is untouched and must keep passing.
- `InvoiceExtraction` schema (six nullable fields) unchanged.
- No network tests; fakes only. Type hints on public interfaces; ruff clean.
- Tight diff: the four files above + the brief. No renames elsewhere.
- Commit message: `refactor(llm): single structured-output call, drop format cascade`.

# Verification commands

```
uv run --extra ocr pytest tests/unit/test_llm_parsing.py tests/unit/test_vision_extraction.py -v
uv run --extra ocr pytest
uv run ruff check .
```

Full suite green (baseline 345 passed / 3 skipped / 29 xfailed; count may shift by
removed `parse_llm_response` tests and added regression tests — net may shrink or grow,
but NO previously-passing test may fail), ruff clean.
