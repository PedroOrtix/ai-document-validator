# docvalidator — AI document validator (production-shaped slice)

Structured field extraction + configurable business-rule verdicts for `SUPPLIER_INVOICE`
documents, exposed as a thin FastAPI service, with an offline-first extraction design
and a measurable evaluation harness.

> Status: work in progress (24h technical assessment). This README is completed in the final phase.

## Quickstart (60 seconds)

```bash
uv sync
uv run uvicorn docvalidator.api.main:app --reload --port 8000
# health check
curl -s localhost:8000/health
```

No API keys required — the default extraction backend is a deterministic offline extractor.

## Evaluation harness

```bash
uv run python -m eval.run          # precision/recall per field + verdict agreement
```

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/validate` | document + config → extraction + rule verdict |
| POST | `/v1/extract` | document → extraction only |
| GET  | `/health` | liveness |

## Architecture (short)

```
document (PDF/text) + config
        │
  POST /v1/validate (FastAPI)
        │
  Extractor (interface) ── OfflineExtractor (regex/heuristics, deterministic)  [default]
                       └── LLMExtractor (OpenRouter, OpenAI-compatible)        [optional]
        │
  DocumentExtraction (Pydantic: value + confidence + evidence per field)
        │
  RulesEngine (registry of pluggable rules) → Verdict {PASS | FAIL | REVIEW}
```

Key decisions (full rationale below in Trade-offs):

1. **Offline-first.** The deterministic extractor is the default backend; reviewers can run
   everything without paid credentials. The LLM path is an interchangeable adapter, not a dependency.
2. **REVIEW vs FAIL distinction.** Missing required data ⇒ `REVIEW` (cannot judge); a violated
   rule with data present ⇒ `FAIL` (judged and rejected). Compliance verdicts need this nuance.
3. **Confidence is evidence-strength, not model probability.** Documented per-field: labeled
   pattern > structural pattern > heuristic.

## Verdict contract

`PASS` — all rules evaluated and passed · `FAIL` — at least one rule failed with data present ·
`REVIEW` — required data missing, human should look at the document.

<!-- Sample request/response: filled in the final phase -->

## Trade-offs consciously made

_(final phase)_

## Cost, latency, and risk notes

_(final phase — when not to use an LLM, measured latency/cost, what to monitor in production)_

## What I would do next with another day

_(final phase)_
