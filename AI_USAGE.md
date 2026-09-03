# AI Usage

This assessment was built with heavy AI assistance under an explicit orchestration workflow.
The author (Pedro) owned every architectural decision, reviewed every diff, and accepted or
rejected each AI contribution deliberately. Nothing was pasted blindly.

## Tools and roles

| Tool | Role |
|---|---|
| **Hermes Agent** (meta-orchestrator) | Requirement analysis, task decomposition, architecture decisions, per-task briefs, diff review, quality gates, final edits |
| **Codex CLI** (executor, model `z-ai/glm-5.3-flash` via OpenRouter, sandboxed worktrees) | Bulk implementation: domain models, extractor, rules engine, API, eval harness, tests |
| **Aegis skill pack** (inside Codex) | TDD discipline, systematic debugging, verification before "done" claims |

Workflow: each phase had a written brief (`docs/prompts/`), Codex implemented it in an
isolated worktree, and every resulting diff was re-read and re-verified (ruff + pytest +
smoke runs) by the orchestrator before being merged. Small defects found in review were
fixed directly rather than re-dispatched.

## Concrete examples of rejected AI suggestions

1. **Rejected: LLM-first extraction architecture.** Early drafts reached for an LLM call as
   the primary extraction path. Rejected because (a) the assessment explicitly rewards an
   offline path reviewers can run without credentials, (b) deterministic extraction is
   testable and free, and (c) "heuristic vs LLM vs hybrid — choose the simplest approach
   that meets the goal" is itself a scored criterion. The LLM became an optional adapter.
2. **Rejected: `date.today()` called deep inside the freshness rule.** The first rule
   implementation read the system clock directly, which makes the age boundary untestable.
   Rejected in review; replaced with a `today: date` injection parameter (default preserves
   behavior, tests inject fixed dates).
3. **Rejected (executor output, caught in review): silent FAIL on missing rule data.** The
   first rules engine returned FAIL whenever a rule's input field was missing. Rejected:
   it conflates "judged and rejected" with "cannot judge". Fixed with an `inconclusive`
   flag on `RuleResult`; missing data now drives `REVIEW` through the required-field check.
   Two aggregation tests lock the semantics, and one golden-fixture expectation was
   corrected accordingly — the eval harness is what surfaced the disagreement.
4. **Rejected (caught by a live API call): untyped LLM values.** The recorded-stub tests
   used pre-typed values, so they never caught that a real LLM returns `invoice_date` as
   an ISO *string* — which the freshness rule would classify as invalid. Found by running
   the extractor against the real OpenRouter API during review; fixed with typed coercion
   in the parser (`date.fromisoformat` / `float`, garbage raises a typed parsing error).
5. **Rejected: asking the LLM to self-report its confidence.** When designing the confidence
   system, the obvious AI-suggested pattern is to have the model emit a confidence per field.
   Rejected: a model grading itself is not calibrated — the score would be decoration, not
   measurement. Confidence is instead **evidence strength**: deterministic pattern tiers for
   the regex parser, and for the LLM path a presence/evidence-based score (0.75 parsed /
   0.6 reported-absent) documented as not self-assessed. Whether that score means anything is
   then *measured*, not asserted: the eval decision table reports mean confidence on
   exact-match cells vs mismatched cells (`conf-ok` / `conf-bad`), and converging columns are
   the documented signal that confidence must not gate automation.
6. **Rejected: keeping a separate regex-only `offline` backend once OCR became local.** The
   initial design had five backends (`offline` regex floor + `llm`/`vlm`/`ocr`/`auto`). Once
   the OCR floor ran fully locally (no key, weights in the image), a separate regex-only
   backend duplicated a worse subset of the same interface. Rejected: one credential-free
   floor (`ocr`) that handles text via the same regex parser *and* reads scanned PDFs beats
   two floors — fewer modes, same guarantee, one fewer failure mode to document.

Also caught in orchestrator review and fixed before merge: an amount regex that truncated
4+ digit integers without thousands separators (`1250.00 → 125.0`), a duplicated request
body parse in `/v1/extract`, and a duplicated Pydantic field declaration.

## How correctness was verified

- **Golden v2 dataset build (2026-09-03).** Three Codex units formed the dataset
  workstream: one TXT generator (40 EN/ES cases), one PDF generator (20 EN/ES
  single-page cases), and this integration unit that consolidated both lane
  manifests, purged the v1 golden files, updated the contract/extraction/API
  tests, and added tier gates.
- **Hermes QA findings and fixes.** Missing PDF table description cells were
  found in review and fixed in the PDF generator; English table headers leaking
  into Spanish invoices were localized; a visual-review false alarm claiming a
  line-math error was rejected after checking the PDF text layer, which showed
  the generated values were correct.
- **Gate calibration by measurement.** Tier 0 gates were set at 0.95/0.95 after
  the offline extractor measured 100%/100% in both lanes. TXT tier 1 was
  calibrated at 0.60 field accuracy / 0.25 verdict agreement from its measured
  68.75%/31.25%. Tier 2 and scenario slices remained informative because they
  are diagnostic rather than release gates.
- Unit tests per extraction field (labeled, fallback, format variants, missing) and per rule
  (pass / fail / skip, boundary at exactly `max_age_days`).
- Aggregation tests: missing required field ⇒ `REVIEW`; violated rule ⇒ `FAIL`; both ⇒ `FAIL`.
- Golden set v2.2 (78 fixtures across txt / digital-pdf / scanned lanes) with expected field
  values and expected verdicts; eval harness reports field-level exact-match rate and verdict
  agreement per tier, with tiered gates that fail CI on regression.
- Every merged phase re-run locally: `uv run ruff check .` and `uv run pytest` before commit.

## Main extraction prompts / instructions

The exact briefs given to the coding executor are committed verbatim in
[`docs/prompts/`](docs/prompts/). The offline extractor's field heuristics were specified in
`docs/prompts/2026-09-03_phase123_core.md`; the LLM extraction system prompt lives in
`src/docvalidator/extraction/llm.py` (single source of truth, reproduced in the README).

## LangChain and markitdown upgrade

The follow-on LLM/PDF upgrade was delegated to Codex with a narrow two-surface brief: replace the
hand-rolled OpenRouter HTTP client with LangChain structured output, and replace the pypdf parser
with Microsoft markitdown. Codex implemented `src/docvalidator/extraction/llm.py`, the markitdown
path in `src/docvalidator/extraction/input.py`, dependency updates, focused unit tests, mocked API
integration tests, documentation, and the committed brief.

I verified that the offline and recorded-LLM lanes remain credential-free, that the exception
taxonomy and API status mappings did not change, that PDF integration behavior stayed intact, and
that the structured-output mapping preserves field types, evidence, fixed confidence, provider,
model, token usage, and duration metadata. I also confirmed the tests, ruff, and eval gate in the
final verification pass.

Final state (2026-09-03, latest verification): 347 tests green (`uv run pytest`), ruff clean,
eval harness over the v2.2 golden set (78 fixtures: 43 txt + 23 digital PDF + 12 scanned;
offline tier-0 1.00 field accuracy / 1.00 verdict agreement in both txt and pdf lanes,
scanned lane reported as informative-only) wired into CI as tiered regression gates,
Docker image built and smoke-tested end-to-end, and one live OpenRouter call verified
the LLM path.

## Backend flip: LLM-primary with offline runtime fallback

The backend-priority flip (LLM primary when a key is present, offline as no-key default and
runtime fallback, `fallback_reason` metadata, OpenRouter reasoning-effort `low` plumbing) was
delegated to Codex (unit `llm-first-fallback`, brief in
`docs/prompts/2026-09-03_phase9_backend_flip.md`). The dispatch wrapper died to a terminal
timeout after Codex had finished the code and test surfaces; the owner completed the close-out
(ruff fixes, README/.env.example/this section, committed brief) and re-verified every gate.
Attribution: code + tests = Codex; close-out + verification = owner. The examiner-only API key
($1 budget, one-week expiry) is delivered out-of-band and never enters this repository.

## VisionExtractor (F1)

Codex implemented the pypdfium2 page-image renderer, injectable `VisionExtractor`, explicit
OpenRouter VLM settings/API wiring, network-free unit/integration tests, and this phase's docs.

## OCR: local RapidOCR (PP-OCRv5 ONNX)

The local OCR engine is RapidOCR (PP-OCRv5, ONNX Runtime) behind the same pipeline/seam.
PaddleOCR-VL-1.6 via transformers was implemented and rejected after a live latency
measurement (~30+s/doc on 24 CPU cores); the decision and rationale are in the README.

## Multi-lane engine comparison (F3)

Codex implemented the multi-lane eval CLI, decision-table telemetry and cost
model, fake-extractor tests, and docs; the owner will run the live
`slm`/`vlm` comparison with the OpenRouter key.

## F5: auto document-type routing + structured-output simplification

Owner decision recorded before implementation: the LangChain
`with_structured_output` object MUST work — one call, one format, no format
cascade. The previous `json_schema → json_mode → raw` degradation chain and the
API's silent offline fallback were removed: an LLM failure is now a typed error
(503 configuration / 502 provider or parsing / 504 timeout), never a silently
degraded result.

The auto router was built in five dispatched Codex units (briefs under
`docs/prompts/2026-09-03_phase14_auto_router_task*.md`), each independently
re-verified by the owner before merge: T1 document-type classifier
(markitdown probe + 150-char residual-layer threshold), T2 `AutoExtractor`
(structural VLM→OCR / LLM→OCR fallback; configuration errors never masked),
T3 structured-output simplification, T4 API `auto` backend as key-present
default, T5 eval `auto` lane with per-route cost/latency rows. Wave merges ran
the full suite at every step (365 passed / 3 skipped / 29 xfailed at wave-2)
plus the offline eval gates.

## OCR spatial reconstruction & live 3-engine evaluation verification

To elevate the local OCR baseline without overfitting the synthetic golden fixtures, three generalizable architectural improvements were designed and implemented:
1. **2D spatial line clustering (`_sort_boxes_reading_order`)**: RapidOCR bounding boxes are clustered into lines by vertical center proximity and sorted horizontally left-to-right, eliminating detection polygon scrambling.
2. **Vertical multi-line key-value pairing**: Resolved vertically stacked labels and values (`Importe Total\n1250.00`, `Fecha:\n14/08/2026`) across adjacent lines.
3. **Multilingual and typographic normalization**: Added locale-independent Spanish written month parsing, European SI space-separated thousands parsing (`680 867,00 €`), and canonical Spanish invoice number prefix handling.

Final live benchmark verification (`uv run python -m eval.run --live --as-of 2026-09-03`):
- **`ocr` (78 cases)**: Jumped from 71.15% fields / 52.56% verdict to **82.05% field accuracy / 80.77% verdict agreement** (100% on Tier 0), fully offline and $0.
- **`slm` (66 cases)**: **98.23% field accuracy / 96.97% verdict agreement** (100% on `supplier_name`, `invoice_number`, `invoice_date`, `tax_id`), ~2.6 s, ~$0.000100/doc.
- **`vlm` (35 cases)**: **98.10% field accuracy / 100.00% verdict agreement** across all scanned and digital PDFs, ~1.8 s, ~$0.000918/doc.
- **Suite**: **380 passed, 3 skipped, 22 xfailed, 7 xpassed**, ruff clean.
