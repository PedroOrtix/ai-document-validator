# Task brief: Phase 8 — LangChain structured output + markitdown PDF parser

## Project context

`src/docvalidator` is green at commit `09ea5e9`. Upgrade only the two adapter surfaces below.
Do not touch `baseline/golden-v1`, `fixtures/generator/`, or regenerate the invoice fixtures.

## Deliverables

1. PDF input (`src/docvalidator/extraction/input.py`)
   - Add `markitdown[pdf]`.
   - Use markitdown on PDF bytes and preserve the existing typed failures for unreadable PDFs and
     empty extracted text.
   - Keep all existing PDF integration behavior unchanged.
2. LLM extractor (`src/docvalidator/extraction/llm.py`)
   - Replace the hand-rolled httpx client with LangChain `ChatOpenAI` pointed at OpenRouter.
   - Bind a Pydantic six-field `InvoiceExtraction` model with `with_structured_output`; prefer
     JSON-schema mode, fall back to JSON mode, then raw JSON parsing.
   - Preserve backend, provider, model, token usage, duration, fixed confidence, and evidence
     semantics, plus all existing error classes and API mappings.
   - Keep the extractor injectable with a model or structured-model fake.
3. Recorded-LLM lane
   - Keep `eval.run` credential-free and green with existing recordings; regenerate only if the
     response format changes, and explicitly report regeneration if so.
4. Tests and docs
   - Cover structured parsing, invalid output, markitdown PDF paths, settings passthrough, and
     mocked API success/502/504 behavior.
   - Update README and AI_USAGE and commit this brief under `docs/prompts/`.

## Constraints

Python 3.12+, ruff clean, pytest green, eval gate green, no API keys, tight diff.
