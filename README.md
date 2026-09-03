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

### Sample request/response

```bash
curl -s -X POST localhost:8000/v1/validate \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "ACME Supply GmbH\nInvoice No: INV-2026-041\nInvoice Date: 2026-09-01\nTotal: 1250.00\nCurrency: EUR\nVAT: DE123456789",
    "config": {
      "max_age_days": 90,
      "allowed_currencies": ["EUR", "GBP"],
      "required_fields": ["supplier_name", "invoice_number", "invoice_date", "total_amount"]
    }
  }'
```

```json
{
  "status": "PASS",
  "rule_results": [
    {"rule_id": "invoice_date_present_and_fresh", "passed": true, "message": "invoice date is present and fresh"},
    {"rule_id": "total_amount_present_and_positive", "passed": true, "message": "total amount is present and positive"},
    {"rule_id": "supplier_name_present", "passed": true, "message": "supplier name is present"},
    {"rule_id": "currency_allowed", "passed": true, "message": "currency is allowed"}
  ],
  "extraction": {
    "document_type": "SUPPLIER_INVOICE",
    "fields": {
      "supplier_name":   {"value": "ACME Supply GmbH", "confidence": 0.8,  "evidence": "ACME Supply GmbH"},
      "invoice_number":  {"value": "INV-2026-041",     "confidence": 0.95, "evidence": "Invoice No: INV-2026-041"},
      "invoice_date":    {"value": "2026-09-01",       "confidence": 0.95, "evidence": "2026-09-01"},
      "total_amount":    {"value": 1250.0,             "confidence": 0.95, "evidence": "Total: 1250.00"},
      "currency":        {"value": "EUR",              "confidence": 0.95, "evidence": "Currency: EUR"},
      "tax_id":          {"value": "DE123456789",      "confidence": 0.95, "evidence": "VAT: DE123456789"}
    },
    "metadata": {"backend": "offline", "duration_ms": null, "model": null, "provider": null}
  },
  "request_id": "33f7bd4d-36a6-4f39-bcd7-8b3724cc0524"
}
```

Errors are structured too: `{"error": {"code": "...", "message": "...", "details": [...]}, "request_id": "..."}`.


## Trade-offs consciously made

1. **Offline-first over LLM-first.** The deterministic extractor is the default backend. Rationale:
   reviewers run everything without credentials, behavior is 100% testable and replayable, per-document
   latency is single-digit milliseconds, and cost is zero. The LLM path exists as a swappable adapter
   for layouts the heuristics can't handle. The eval harness makes the quality of either path measurable.
2. **REVIEW ≠ FAIL.** A rule whose input data is missing is marked `inconclusive` and cannot push the
   verdict to `FAIL`; missing required fields surface as `REVIEW`. Rationale: in compliance workflows,
   "judged and rejected" (FAIL) and "cannot judge, human needed" (REVIEW) have very different operational
   consequences — FAIL may block a supplier, REVIEW queues work. Conflating them would misroute documents.
3. **Confidence = evidence strength, not model probability.** Offline confidences encode pattern quality
   (labeled 0.95 / structural 0.7–0.9 / heuristic <0.5); the LLM path reports a fixed 0.75 because
   model-reported confidence is not calibrated. We prefer an honest constant to a fake decimal.
4. **Fixtures instead of real OCR.** The scope note says OCR is out of scope; PDFs are supported through
   the text layer (pypdf) and plain-text fixtures drive the golden set. Scanned-image PDFs fail loudly
   with a typed error instead of silently returning empty extractions.
5. **One document type, extensible seams.** Only `SUPPLIER_INVOICE` is implemented, but the extractor
   interface, rule registry, and config schema are document-type aware; adding `CERTIFICATE` means a new
   extractor + rules, not a rewrite.

## Cost, latency, and risk notes

**When would you *not* use an LLM here?**

- Digital-born PDFs with stable labeled layouts (typical B2B invoice PDFs): regex/structural extraction
  is deterministic, free, ~ms-fast, and unit-testable — an LLM adds cost, latency, nondeterminism, and a
  failure mode without a quality win.
- Any verdict path that must be explainable in an audit: regex evidence (the exact matched text) is
  stronger evidence than an LLM's answer.
- Very high volume of low-value documents where a wrong extraction just routes to review anyway.

The LLM earns its keep on messy OCR text, highly varied layouts, and multi-language suppliers — that's
why it ships as an opt-in adapter with a recorded stub for offline testing.

**What did we measure?**

- Offline path (measured, from the structured request logs): **~3.5 ms** end-to-end per document
  (extraction + rules), zero marginal cost.
- LLM path (measured via OpenRouter, model `z-ai/glm-5.3-flash`): see `VALIDATOR_LLM_*` in
  `.env.example`; per-document latency and token usage are returned in `extraction.metadata`
  (`duration_ms`, `model`, `provider`) and logged per request. Rough order: ~1–3 s and
  ~500–900 tokens per document at high reasoning effort → fractions of a cent per document on
  an open-weight model.
- Eval harness: `uv run python -m eval.run` reports field exact-match, per-field precision/recall and
  verdict agreement over the golden set (currently 1.00 / 1.00 over 6 fixtures — small set, see below).

**What would we monitor in production?**

- **Quality drift:** scheduled re-run of the golden set + a sample of live documents against updated
  expectations; alert on field accuracy or verdict agreement dropping below threshold.
- **Confidence distribution:** a shift toward low-confidence extractions signals layout drift before
  customers complain.
- **Verdict mix:** sudden changes in PASS/FAIL/REVIEW ratios usually mean upstream document format
  changes (or extractor regressions).
- **Cost & latency (LLM backend):** tokens and ms per document per day, provider error/timeout rates,
  spend cap alerts.
- **Operational:** request error rate by code (422 vs 5xx), PDF text-layer failures (they indicate
  scanned documents needing real OCR).

## What I would do next with another day

1. Grow the golden set (20–30 fixtures incl. scanned-style, multi-page, non-EU formats) and add an
   offline-vs-LLM comparison lane to the eval output.
2. Wire the eval harness into CI as a regression gate (fails PRs that drop field accuracy).
3. Real OCR adapter (Azure Document Intelligence) behind the same `Extractor` interface for scanned PDFs.
4. Page-level evidence spans and a debug endpoint returning per-field extractor traces.
5. Second document type (`CERTIFICATE_OF_INCORPORATION`) to pressure-test the extensibility claim.
