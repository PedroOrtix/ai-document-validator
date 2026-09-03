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

_(final list at submission; examples captured during the run)_

1. **Rejected: LLM-first extraction architecture.** Early drafts reached for an LLM call as
   the primary extraction path. Rejected because (a) the assessment explicitly rewards an
   offline path reviewers can run without credentials, (b) deterministic extraction is
   testable and free, and (c) "heuristic vs LLM vs hybrid — choose the simplest approach
   that meets the goal" is itself a scored criterion. The LLM became an optional adapter.
2. **Rejected: `date.today()` called deep inside the freshness rule.** The first rule
   implementation read the system clock directly, which makes the age boundary untestable.
   Rejected in review; replaced with a `today: date` injection parameter (default preserves
   behavior, tests inject fixed dates).

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
`src/docvalidator/extraction/llm.py` (single source of truth, also printed in the README).

<!-- Final metrics: tests count, eval results — filled at submission -->
