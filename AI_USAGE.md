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

Also caught in orchestrator review and fixed before merge: an amount regex that truncated
4+ digit integers without thousands separators (`1250.00 → 125.0`), a duplicated request
body parse in `/v1/extract`, and a duplicated Pydantic field declaration.

## How correctness was verified

- Unit tests per extraction field (labeled, fallback, format variants, missing) and per rule
  (pass / fail / skip, boundary at exactly `max_age_days`).
- Aggregation tests: missing required field ⇒ `REVIEW`; violated rule ⇒ `FAIL`; both ⇒ `FAIL`.
- Golden set of ≥6 invoice fixtures with expected field values and expected verdicts;
  eval harness reports field-level exact-match rate and verdict agreement.
- Every merged phase re-run locally: `uv run ruff check .` and `uv run pytest` before commit.

## Main extraction prompts / instructions

The exact briefs given to the coding executor are committed verbatim in
[`docs/prompts/`](docs/prompts/). The offline extractor's field heuristics were specified in
`docs/prompts/2026-09-03_phase123_core.md`; the LLM extraction system prompt lives in
`src/docvalidator/extraction/llm.py` (single source of truth, reproduced in the README).

Final state at submission: 96 tests green (`uv run pytest`), ruff clean, eval harness over a
20-fixture golden set (offline 0.99 field accuracy / 1.00 verdict agreement; recorded-LLM
1.00 / 1.00) wired into CI as a regression gate, Docker image built and smoke-tested
end-to-end, and one live OpenRouter call verified the LLM path.
