# Unit brief — f5-t5-eval-auto-lane

# Goal

Add the `auto` lane to the golden-set multi-lane eval so the decision table reports,
per format and tier, field accuracy, verdict agreement, average latency (avg_ms) and
estimated cost per document ($/doc) — including a per-route breakdown
(route:llm / route:vlm / route:ocr) — on branch `agent/f5-t5-eval-auto-lane` based on
the integration branch HEAD (contains T1+T2+T3+T4: AutoExtractor in
`extraction/routing.py`, API `auto` backend).

OUT of scope: `src/docvalidator/**` (extractors are frozen), API tests, README.

# Context

- Repo: `/home/pedro/Documents/prueba_tecnica_nalanda` (worktree by dispatcher),
  Python >=3.12, uv, ruff line-length 100.
- `eval/lanes.py` anchors:
  - `LANE_NAMES = ("offline", "slm", "vlm", "ocr")`;
    `LANE_FORMATS` maps each lane to eligible formats (`offline`: txt,pdf,scanned;
    `slm`: txt,pdf; `vlm`: scanned,pdf; `ocr`: scanned,pdf).
  - `resolve_lane_plans(requested, *, live, has_api_key)` — slm/vlm require
    `--live` + key, else `LanePlan(..., available=False, skip_reason=...)`.
  - `estimate_cost_usd(total_tokens, *, lane)` prices only `{"slm","vlm"}` with
    `GLM_FLASH_PRICE_PER_TOKEN` (published USD/token blend).
  - `decision_table(report)` builds rows per lane/format/tier with
    `avg_ms`, `avg_tokens`, `est_cost_per_doc`; `print_decision_table` prints them.
  - `make_offline_extractor()` is the public factory pattern.
- `eval/run.py` anchors: `make_llm_extractor()`, `make_vision_extractor()`,
  `make_ocr_extractor()` factories; `run_case(...)` builds one result dict with
  `duration_ms`/`total_tokens` from `extraction_telemetry(extraction)` and slices
  from `expected["slices"]`; `slice_metrics(results)` groups by dimensions
  ("language","tier","scenario","format") reading `result["slices"]`;
  `prepare_cases(manifest)` returns txt+pdf cases, `prepare_scanned_cases(manifest)`
  the scanned ones; `run_lane(cases, *, today, lane_name, formats,
  extractor_factory)` runs one lane; the CLI resolves lanes and runs each eligible
  one (see `main()`/`run_report` wiring in the file).
- `docvalidator.extraction.routing` (merged): `AutoExtractor` — metadata after a run:
  `backend="auto"`, `model` ∈ {"llm","vlm","ocr"} (the sub-route), `fallback_reason`
  set when OCR fallback fired, `total_tokens` preserved from the delegate.
- Golden set: 43 txt + 23 pdf + 12 scanned cases (single-page fixtures).
- Tests hermetic; live lanes are OWNER-run (`--live` + key), skipped otherwise with
  an explicit message. Baseline suite: 351+ passed / 3 skipped / 29 xfailed (grows).

# Required changes

1. `eval/lanes.py`:
   - `LANE_NAMES` gains `"auto"`; `LANE_FORMATS["auto"] = ("txt", "pdf", "scanned")`.
   - `resolve_lane_plans`: treat `auto` like `slm`/`vlm` (requires --live + key).
   - `estimate_cost_usd`: add `"auto"` to the priced-lane set (tokens recorded by the
     delegate drive the cost; OCR-fallback docs contribute 0 because their
     `total_tokens` is None/0 — that asymmetry is intended and costed correctly).
   - `make_auto_extractor() -> Extractor` factory returning `AutoExtractor()`
     (lazy import from `docvalidator.extraction.routing` inside the factory).
   - Per-route visibility: extend `run_case` in `eval/run.py` (see 2) so each result
     carries `sub_route`; then in `decision_table`, for the `auto` lane ALSO emit one
     row per `route:<sub-route>` slice using the same tier/format grouping — simplest
     correct approach: group auto-lane results by `result["sub_route"]` within each
     (format, tier) and add rows with `lane="auto:<llm|vlm|ocr>"`.
2. `eval/run.py`:
   - `run_case` result dict gains `"sub_route": extraction.metadata.model if
     extraction.metadata.backend == "auto" else None` (compute BEFORE the try/except
     no-response path; on extraction failure sub_route is None).
   - Register `make_auto_extractor` and wire the `auto` lane into the lane execution
     exactly like `slm`/`vlm` (live+key gating, case selection by
     `LANE_FORMATS["auto"]` — txt + pdf + scanned).
3. `tests/unit/test_eval_lanes.py` (append, keep existing):
   - `resolve_lane_plans(("auto",), live=True, has_api_key=True)` → one available
     plan with formats ("txt","pdf","scanned").
   - `resolve_lane_plans(("auto",), live=False, ...)` → unavailable with
     "requires --live" reason.
   - `estimate_cost_usd(1000, lane="auto") > 0` and equals
     `1000 * GLM_FLASH_PRICE_PER_TOKEN`.
   - `decision_table` on a synthetic report containing an auto lane with mixed
     sub-routes emits `auto` rows AND `auto:<route>` rows with correct cost math.
   - Network-free only (do NOT call AutoExtractor with real extractors; build the
     report dict by hand).
4. Commit the brief unchanged as
   `docs/prompts/2026-09-03_phase14_auto_router_task5_eval_auto_lane.md`.

# Constraints

- Offline gates unchanged: `uv run --extra ocr python -m eval.run --lane offline,ocr
  --as-of 2026-09-03` must still exit 0 with its gates PASS (no threshold edits).
- No network in tests; no real provider calls anywhere in the eval default path.
- Existing lane behavior byte-compatible (offline/slm/vlm/ocr rows unchanged in shape).
- Type hints; ruff clean; tight diff: eval/lanes.py, eval/run.py,
  tests/unit/test_eval_lanes.py + brief.
- Commit message: `feat(eval): auto lane with per-route cost/latency decision rows`.

# Verification commands

```
uv run --extra ocr pytest tests/unit/test_eval_lanes.py -v
uv run --extra ocr pytest
uv run ruff check .
uv run --extra ocr python -m eval.run --lane offline,ocr --as-of 2026-09-03
```

The last command must keep its offline gates PASS (same thresholds as main).
