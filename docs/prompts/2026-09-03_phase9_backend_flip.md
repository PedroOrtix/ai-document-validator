# Phase 9 brief — LLM-primary backend selection with offline runtime fallback

Dispatched to Codex (unit `llm-first-fallback`, base `d8aaa6e`). Full brief preserved verbatim
below for audit.

> **Runtime note:** the original dispatch's wrapper was killed by a terminal timeout (SIGTERM to
> the process group) after Codex had already implemented the six code/test surfaces; the remaining
> close-out (ruff fixes, README/.env.example/AI_USAGE, this file, commit) was finished by the owner.
> Code + tests: Codex. Close-out: owner. All gates re-verified by the owner after close-out.

---

# Goal

Flip the extraction backend priority of `docvalidator`: the **LangChain LLM lane becomes the primary engine**, and the deterministic regex `OfflineExtractor` becomes the **runtime fallback** and the credential-free default. Base your work on commit `d8aaa6e` (branch `agent/llm-langchain-markitdown`), which already contains the LangChain structured-output extractor and the markitdown PDF parser. Do NOT touch `baseline/golden-v1`, `fixtures/generator/`, `fixtures/golden/`, or `fixtures/invoices/` — another lane owns the dataset.

# Context

- Repo root for your worktree: branched from `d8aaa6e` (LangChain + markitdown upgrade already merged in that branch).
- Assessment brief: offline-first is a HARD requirement — reviewers must be able to run everything without credentials. The LLM is now the *preferred* engine, but the regex path must remain the credential-free default AND the automatic runtime fallback.
- An examiner-only OpenRouter API key will be delivered out-of-band (submission email), budget-capped at $1 USD, **expiring in one week**. The README must document this: how to set it, what it is for, its budget/expiry, and that the service falls back to the offline extractor without it. NEVER put a key in the repo, `.env.example` stays a placeholder-only file.
- Model: `z-ai/glm-5.3-flash` via OpenRouter. We want inference at **reasoning effort "low"** (the same model family's vision-capable low-effort variant will later be used for a VisionExtractor; this lane sends TEXT from markitdown, not images — that future approach is out of scope, only document it in the README roadmap).
- Current default backend selection: `src/docvalidator/api/main.py` `_default_backend()` reads `EXTRACTION_BACKEND` env (default "offline"). Backends: `offline`, `llm`.
- The LLM lane: `src/docvalidator/extraction/llm.py` — LangChain `ChatOpenAI` + `.with_structured_output(InvoiceExtraction)` with json_schema → json_mode → raw cascade; typed exceptions `LLMConfigurationError/LLMRequestError/LLMParsingError/LLMTimeoutError` mapped to 503/502/502/504 in the API.
- The offline lane: `src/docvalidator/extraction/offline.py` — regex/heuristics, deterministic, ~ms.
- Eval: `uv run python -m eval.run --as-of 2026-09-03 --min-field-accuracy 0.95 --min-verdict-agreement 1.0` must stay green and **100% network-free** (recorded-LLM lane replays fixtures).

# Required changes

1. **Default backend selection** (`api/main.py` + `settings.py`):
   - New logic: when a request does not specify `extraction_backend`, use `llm` if `OPENROUTER_API_KEY` is present, else `offline` (log a structured warning once at startup: "no API key configured; using offline extractor").
   - Keep the explicit per-request override (`extraction_backend: "offline" | "llm"`) working exactly as now.
2. **Runtime fallback on LLM failure** (the key change):
   - When the LLM lane fails mid-request (LLMRequestError, LLMTimeoutError, LLMParsingError — but NOT LLMConfigurationError at request time, which means no key), retry the same document once with `OfflineExtractor` and return its result with `ExtractionMetadata.backend = "offline-fallback"` plus a `fallback_reason` field added to the metadata model (e.g. "llm_timeout", "llm_request_error", "llm_parsing_error").
   - Log a structured warning (request_id, reason) when this happens.
   - `LLMConfigurationError` (no key) must NOT fall back — the request should never have been routed to `llm` in that case; return 503 as now.
   - Apply this fallback to both `/v1/validate` and `/v1/extract`.
3. **Reasoning effort setting** (`settings.py` + `llm.py`):
   - New setting `VALIDATOR_LLM_REASONING_EFFORT: str = "low"`.
   - Pass it to ChatOpenAI via OpenRouter-compatible `extra_body={"reasoning": {"effort": <value>}}` (only when the setting is non-empty). Keep it injectable/mockable as the rest of the constructor.
4. **Docs**:
   - README: new default-backend behavior table (key present → LLM primary; no key → offline); the runtime fallback contract; the examiner key instructions (out-of-band key, $1 budget, expires in one week, `cp .env.example .env`, paste key, restart); latency honesty (LLM ~7s/doc vs offline ~ms); roadmap note for the future `VisionExtractor` (same `Extractor` interface, sends page images to the vision-capable GLM variant instead of markitdown text).
   - `.env.example`: add `VALIDATOR_LLM_REASONING_EFFORT=low` placeholder line (no real key, ever).
   - AI_USAGE.md: extend the existing section with this flip (delegated to Codex, verified by owner).
   - Add the brief to `docs/prompts/` following the existing naming convention.
5. **Tests** (protect the important behaviour, no ceremony):
   - Unit: backend selection with/without key (monkeypatch env); fallback triggers on a mock LLM raising LLMTimeoutError/LLMRequestError → result has backend="offline-fallback" + fallback_reason + verdict computed; LLMConfigurationError → 503, no fallback; reasoning-effort setting reaches the model constructor kwargs.
   - Integration (API, mocked model, no network): /v1/validate with LLM mock failing → 200 with offline-fallback metadata; /v1/validate without key → 200 offline; /v1/validate with explicit backend override still respected.
6. **Eval stays untouched and network-free**: do not change `eval/` or fixtures. Verify the gate still passes.

# Constraints

- Python 3.12+, type hints on public interfaces, ruff clean, pytest green, eval gate green.
- No API keys in the repo; `.env.example` placeholder only.
- Keep the diff tight: `api/main.py`, `settings.py`, `extraction/llm.py` (constructor only), `domain/models.py` (metadata field), tests, docs.
- Commit message: `feat: llm-first backend selection with offline runtime fallback`.

# Verification commands

```
uv run pytest -q
uv run ruff check .
uv run python -m eval.run --as-of 2026-09-03 --min-field-accuracy 0.95 --min-verdict-agreement 1.0
```
