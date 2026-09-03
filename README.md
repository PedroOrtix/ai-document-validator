# docvalidator — AI document validator (production-shaped slice)

Structured field extraction + configurable business-rule verdicts for `SUPPLIER_INVOICE`
documents, exposed as a thin FastAPI service, with an LLM-primary / offline-fallback extraction
design and a measurable evaluation harness.

> Status: work in progress (24h technical assessment). This README is completed in the final phase.

## Quickstart (60 seconds)

```bash
uv sync
uv run uvicorn docvalidator.api.main:app --reload --port 8000
# health check
curl -s localhost:8000/health
```

The service runs with **zero configuration**: without an `OPENROUTER_API_KEY` the default
extraction backend is the deterministic offline extractor; with a key present, the LangChain
LLM extractor becomes the primary engine and the offline extractor becomes its runtime
fallback. An examiner-only API key (budget-capped at **$1 USD**, expiring **one week** after
the submission date) is delivered out-of-band with the submission — paste it into `.env`
(see `.env.example`) to run the LLM path; the repo itself never contains any key.

### Backend selection contract

| Condition | Default backend |
|---|---|
| `OPENROUTER_API_KEY` present | `llm` (LangChain → OpenRouter, `z-ai/glm-5.3-flash` @ reasoning effort `low`) |
| No key (or key expired) | `offline` (regex/heuristics, deterministic, ~ms) |

- Every request can still override this with `extraction_backend: "offline" | "llm" | "vlm"`.
- **Runtime fallback:** if the LLM lane fails mid-request (timeout, provider error, unparseable
  response), the same document is retried once with the offline extractor. The result carries
  `metadata.backend = "offline-fallback"` and `metadata.fallback_reason`
  (`llm_timeout` | `llm_request_error` | `llm_parsing_error`), plus a structured warning in the
  logs. A misconfigured key is NOT masked this way — it returns `503` as before.

## Golden dataset v2

The fixed evaluation set is generated, not hand-maintained. It contains 60
documents: 40 text cases and 20 single-page PDF cases, split evenly between
English and Spanish. Tier 0 is canonical, tier 1 adds label and format variants,
and tier 2 stresses rare labels, mixed formats, and unlabeled/mixed currency.
Every case carries generated field truth and verdict truth.

| Lane | Tier | Count | Scenarios |
|---|---:|---:|---|
| TXT | 0 | 12 | clean, stale |
| TXT | 1 | 16 | clean, stale variants |
| TXT | 2 | 12 | missing total, mixed/unlabeled currency |
| PDF | 0 | 6 | clean, stale |
| PDF | 1 | 8 | clean, missing date |
| PDF | 2 | 6 | clean, mixed/unlabeled currency |

Regenerate or verify it with:

```bash
uv run python -m fixtures.generator.build
uv run python -m fixtures.generator.build --verify
```

The build writes `manifest_txt.json` and `manifest_pdf.json` fragments plus the
merged `manifest.json`; `--verify` re-derives both lanes and checks hashes and
orphan files. There is no tier 3 lane: rule scenarios are distributed in tiers
0–2 and remain represented by their expected verdicts.

### Gates policy

`eval.run` prints a `GATES` section by default:

- `tier:0` is hard for each lane: field accuracy and verdict agreement >= `0.95`.
- `tier:1` is hard for each lane: field accuracy >= `0.60` and verdict agreement >= `0.25`.
- `tier:2` and scenario slices are informative.
- Lane and global lane aggregates are informative; `--no-gates` preserves the
  legacy optional `--min-field-accuracy` and `--min-verdict-agreement` behavior.

### Extractor offline baseline

Measured 2026-09-03 with `uv run python -m eval.run --as-of 2026-09-03`:

| Slice | Field accuracy | Verdict agreement |
|---|---:|---:|
| TXT tier 0 | 100.00% | 100.00% |
| TXT tier 1 | 68.75% | 31.25% |
| TXT tier 2 | 44.87% | 38.46% |
| PDF tier 1 | 79.63% | 44.44% |
| PDF lane overall | 81.16% | 60.87% |
| Scanned lane (informative) | 5.56% | 16.67% |

The scanned lane is the documented offline gap: image-only PDFs have no text layer,
so the deterministic extractor correctly returns nothing (the expected failure mode,
not a regression) — closing it is the LLM/vision backend's job, and the fixtures exist
to measure that.

## Evaluation harness

```bash
uv run python -m eval.run --as-of 2026-09-03   # both lanes over the v2 golden set, GATES section
# hard gates by tier: tier0 >= 0.95/0.95, tier1 >= 0.60/0.25; tier2 and scenario slices informative
uv run python -m eval.run --no-gates           # report only
```

The offline extractor runs over the v2.2 golden set (43 txt + 23 digital-born
single-page pdf + 12 image-only scanned pdf fixtures across
tiers 0-2: label/format variants, unlabeled currency, distractor totals, textured
PDF layouts): the deterministic offline extractor (LLM and recorded-LLM
backends plug into the same interface), so the comparison is reproducible
without credentials. Runs are anchored to
`--as-of` (default 2026-09-03) so age-rule expectations never rot with
wall-clock time. Known extractor misses at tier 1-2 (see the measured table
above): spelled-out dates, GB-format VAT ids, and rare label variants — the
dataset isolates them; closing the gap is the LLM backend's job.

The v2.2 dataset also contains 12 deterministic image-only scanned PDFs
(4 per tier, 2 per language) with the same truth as their PDF twins. The offline
lane deliberately cannot read them; `--include-scanned` (default on) reports
them as a separate `scanned` lane for future VLM/OCR measurements and does not
contribute to the txt/pdf gates. Use `--no-include-scanned` to omit the section.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/validate` | document + config → extraction + rule verdict |
| POST | `/v1/extract` | document → extraction only |
| GET  | `/health` | liveness |

### API contract

`POST /v1/validate` accepts **either** representation:

- **Multipart form** (`multipart/form-data`): `file` (a `.pdf` — read through its text layer —
  or a `.txt`) + `config` (a JSON string matching the config example above).
- **JSON body** (`application/json`), exactly one content source:
  - `text` — plain-text document content, **or**
  - `content_b64` — base64-encoded document bytes (PDF by default; decoded as text when
    `filename` ends in `.txt`) — plus optional `filename`;
  - optional `config` (defaults apply when omitted);
  - optional `extraction_backend`: `offline` | `llm` | `vlm` (default: `llm` with a key, else `offline`).

Responses: `200` with the verdict (see the sample below), `422` with a structured
`{"error": {"code", "message", "details"}, "request_id"}` body for invalid input or
unreadable documents, `5xx` only for upstream LLM failures. Every response echoes an
`X-Request-ID` header (client-supplied or generated) that also appears in the structured logs.

`POST /v1/extract` accepts the same representations and returns only the extraction object.

## Architecture

```mermaid
flowchart TD
    A["document (PDF bytes / text) + rule config"] --> B["POST /v1/validate · POST /v1/extract<br/>(FastAPI, structured JSON logs)"]
    B --> C{extraction backend}
  C -->|llm · primary with key| E["LLMExtractor<br/>LangChain ChatOpenAI + structured output<br/>via OpenRouter, reasoning=low"]
  C -->|vlm · explicit scanned lane| E2["VisionExtractor<br/>PDF page image → OpenRouter<br/>z-ai/glm-5.3-flash, reasoning=low"]
    C -->|offline · no-key default| D["OfflineExtractor<br/>regex + heuristics, deterministic"]
    E -->|on LLM failure| D2["offline-fallback<br/>retry once + fallback_reason"]
    C -->|llm-recorded| F["RecordedLLMExtractor<br/>recorded responses for tests"]
    D --> G["DocumentExtraction<br/>value + confidence + evidence per field"]
    E --> G
    F --> G
    G --> H["RulesEngine<br/>pluggable rule registry"]
    H --> I["Verdict: PASS / FAIL / REVIEW<br/>rule results + model metadata"]
```

Key decisions (full rationale below in Trade-offs):

1. **LLM-primary with an honest offline floor.** With a key present the LangChain extractor is the
   default engine; the deterministic offline extractor stays as the credential-free default AND the
   automatic runtime fallback, so the assessment's offline-first requirement is preserved: every
   lane (API, tests, eval, CI) still runs with zero credentials.
2. **REVIEW vs FAIL distinction.** Missing required data ⇒ `REVIEW` (cannot judge); a violated
   rule with data present ⇒ `FAIL` (judged and rejected). Compliance verdicts need this nuance.
3. **Confidence is evidence-strength, not model probability.** Documented per-field: labeled
   pattern > structural pattern > heuristic.

### LLM extraction system prompt

The offline heuristics were specified in [`docs/prompts/`](docs/prompts/) (the executor briefs).
The LLM backend's system prompt — the single source of truth lives in
`src/docvalidator/extraction/llm.py` — is:

```text
You extract supplier invoice fields into the requested structured schema. Return the six
fields "supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", and
"tax_id". Use null for absent fields, ISO dates (YYYY-MM-DD), float amounts, and ISO-4217
currency codes.
```

`LLMExtractor` builds a LangChain `ChatOpenAI` client against the configured OpenRouter base
URL and binds a Pydantic `InvoiceExtraction` schema with `with_structured_output`. The adapter
tries JSON-schema mode first, falls back to JSON mode if the provider rejects structured
output, and finally uses defensive JSON parsing of the raw completion. Invalid or incomplete
responses still raise `LLMParsingError`; timeouts, provider errors, and configuration failures
retain the existing API mappings.

### VisionExtractor (F1)

`VisionExtractor` is an explicit `extraction_backend: "vlm"` lane for image-only scanned
invoices. It rasterizes every PDF page to PNG at about 150 DPI using the pure-wheel
`pypdfium2` renderer, then sends the **first page image** (plus the same six-field structured
schema) through LangChain to OpenRouter. Multi-page scanning beyond page 1 is deferred; no
automatic text-to-VLM switching is added in this phase.

| Setting | Default |
|---|---|
| `VALIDATOR_VLM_MODEL` | `z-ai/glm-5.3-flash` |
| `VALIDATOR_VLM_REASONING_EFFORT` | `low` |
| `VALIDATOR_VLM_TIMEOUT_SECONDS` | `60` |

The same API key is reused. OpenRouter lists this model's input modalities as
`["text", "image", "video"]` and its prompt price at `$0.000000075/token`; expect image calls to
be slower than the text LLM lane and budget for image-token overhead. The 12 scanned
`fixtures/golden/scan_*.pdf` cases are the intended measurement surface (eval integration is
phase F3).

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
    {"rule_id": "invoice_date_present_and_fresh", "passed": true, "message": "invoice date is present and fresh", "inconclusive": false},
    {"rule_id": "total_amount_present_and_positive", "passed": true, "message": "total amount is present and positive", "inconclusive": false},
    {"rule_id": "supplier_name_present", "passed": true, "message": "supplier name is present", "inconclusive": false},
    {"rule_id": "currency_allowed", "passed": true, "message": "currency is allowed", "inconclusive": false}
  ],
  "extraction": {
    "document_type": "SUPPLIER_INVOICE",
    "fields": {
      "supplier_name":  {"value": "ACME Supply GmbH", "confidence": 0.8,  "evidence": "ACME Supply GmbH",   "page_hint": null},
      "invoice_number": {"value": "INV-2026-041",     "confidence": 0.95, "evidence": "Invoice No: INV-2026-041", "page_hint": null},
      "invoice_date":   {"value": "2026-09-01",       "confidence": 0.95, "evidence": "2026-09-01",       "page_hint": null},
      "total_amount":   {"value": 1250.0,             "confidence": 0.95, "evidence": "Total: 1250.00",   "page_hint": null},
      "currency":       {"value": "EUR",              "confidence": 0.95, "evidence": "Currency: EUR",    "page_hint": null},
      "tax_id":         {"value": "DE123456789",      "confidence": 0.95, "evidence": "VAT: DE123456789", "page_hint": null}
    },
    "metadata": {"backend": "offline", "duration_ms": 1.711, "model": null, "provider": null, "total_tokens": null}
  },
  "request_id": "a5cb8bb2-8f10-4b02-aa77-be3b8e07d49e"
}
```

Errors are structured too: `{"error": {"code": "...", "message": "...", "details": [...]}, "request_id": "..."}`.


## Trade-offs consciously made

1. **LLM-primary, offline-fallback (flipped from the original offline-first).** Rationale: an
   examiner-only key (delivered out-of-band, $1 budget, one-week expiry) removes the credential
   barrier for the reviewer, so the higher-quality engine leads. The offline extractor remains the
   no-key default and the runtime fallback (never masked: a degraded result is always flagged with
   `backend="offline-fallback"` + `fallback_reason`), tests and eval stay 100% credential-free, and
   latency is honest: ~7 s/document on the LLM path vs ~3.5 ms offline — the client pays the latency
   only when a key is configured.
2. **REVIEW ≠ FAIL.** A rule whose input data is missing is marked `inconclusive` and cannot push the
   verdict to `FAIL`; missing required fields surface as `REVIEW`. Rationale: in compliance workflows,
   "judged and rejected" (FAIL) and "cannot judge, human needed" (REVIEW) have very different operational
   consequences — FAIL may block a supplier, REVIEW queues work. Conflating them would misroute documents.
3. **Confidence = evidence strength, not model probability.** Offline confidences encode pattern quality
   (labeled 0.95 / structural 0.7–0.9 / heuristic <0.5); the LLM path reports a fixed 0.75 because
   model-reported confidence is not calibrated. We prefer an honest constant to a fake decimal.
4. **Fixtures instead of real OCR.** The scope note says OCR is out of scope; PDFs are supported through
   the text layer (markitdown) and plain-text fixtures drive the golden set. Scanned-image PDFs fail loudly
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
- LLM path (measured live, model `z-ai/glm-5.3-flash` via OpenRouter, single invoice): **~7.5 s,
  ~300 total tokens** per document → well under a cent per document on an open-weight model.
  Per-document latency and token usage are returned in `extraction.metadata` (`duration_ms`,
  `model`, `provider`, `total_tokens`) and logged per request.
- Eval harness: offline extraction over the v2.2 golden set (78 fixtures: 43 txt + 23 digital
  PDF + 12 scanned) — **1.00/1.00 field accuracy / verdict agreement on tier 0** in both txt
  and pdf lanes, with per-tier hard gates that fail CI on regression; scanned results are
  reported as a separate informative lane.

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

Done since the first submission: golden set grown 6 → 78 fixtures (43 txt + 23 digital PDF +
12 scanned image-only PDFs; subtotal traps, credit notes, US/JP formats, empty/garbage
documents, degenerate failure modes), scanned fixture lane, and the eval wired into CI as
a tiered regression gate.

PDF parsing now uses Microsoft `markitdown` instead of raw `pypdf`. Markitdown gives a
higher-level, converter-based PDF-to-Markdown path and keeps PDF handling out of hand-written
page-extraction code; the trade-off is a larger dependency footprint and less direct control over
page-level parsing than pypdf. For this project, the typed empty-text and unreadable-PDF failures
are unchanged, and the simpler adapter wins.

1. **VisionExtractor hardening**: reuse the same `Extractor` interface while extending the
   first-page implementation to multi-page policies, confidence calibration, and automatic
   scanned-document cascade decisions.
2. Locale-metadata-aware date disambiguation (the known `us_date_ambiguous` miss) instead of
   always reading `03/07/2026` day-first.
3. Real OCR adapter (Azure Document Intelligence) behind the same `Extractor` interface for scanned PDFs.
4. Page-level evidence spans and a debug endpoint returning per-field extractor traces.
5. Second document type (`CERTIFICATE_OF_INCORPORATION`) to pressure-test the extensibility claim.
6. Optional live LLM lane in the eval harness (real API, gated behind a key) next to the recorded one.
