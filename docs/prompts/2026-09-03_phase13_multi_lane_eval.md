# Phase 13: Multi-Lane Engine Comparison (F3)

Upgrade `eval.run` from the offline-only baseline into a decision-table harness
for `offline` (regex), `slm` (text LLM), `vlm` (vision LLM), and `ocr` (local
RapidOCR). Preserve every existing offline tier gate exactly.

Requirements:

- CLI lanes: `--lane offline|slm|vlm|ocr|all`, with comma-separated/repeatable
  values. Default to the available network-free lanes (`offline`, plus `ocr`
  when the optional extra imports). `--live` plus `OPENROUTER_API_KEY` enables
  `slm`/`vlm`; otherwise skip clearly without crashing.
- Eligibility: `offline` = txt + pdf + scanned miss; `slm` = txt + pdf;
  `vlm` = scanned + pdf; `ocr` = scanned + pdf.
- Per-case telemetry: duration and total tokens (LLM lanes); extraction/API
  errors remain misses. Add an extractor-factory seam for network-free tests.
- Output a compact decision table by lane x format x tier with accuracy,
  verdict agreement, avg duration, avg tokens, and estimated cost/doc. Use
  published glm-5.3-flash token-price constants for LLM lanes and $0 for local
  lanes; summarize each lane across its eligible formats.
- Preserve hermetic tests/CI. Cover lane eligibility, cost estimation, and
  report shape with fake extractors. Do not run live lanes during implementation.
