# Phase 10 — Scanned-Invoice Fixture Lane

## Goal

Build a scanned golden lane for the frozen v2 spec: 12 image-only PDFs with
trusted truth, selected from existing PDF rows. These fixtures are a measurement
surface for the upcoming VLM and OCR extraction engines; they intentionally
remain unreadable to the offline service.

## Scope

- Add `fixtures/generator/scanned_build.py` with the existing `--verify` CLI
  convention.
- Select 4 cases per tier (2 EN + 2 ES per tier) from `PDF_PLAN`; reuse the PDF
  truth so every scanned case has a text twin.
- Render a deterministic Pillow page mirroring the PDF tier layout, then apply a
  seeded office-scan transform: rotation, brightness/contrast shift, gaussian
  noise, blur, and JPEG re-encoding.
- Wrap the degraded page in a single-page image-only fpdf2 PDF; assert no text
  layer during build and verification.
- Extend the merged manifest with `scanned_cases`, write
  `manifest_scanned.json`, and add contract tests.
- Add a separate scanned eval section and `--include-scanned/--no-include-scanned`
  controls. Scanned results are informative and never change the existing
  txt/pdf gates.
- Document the lane and its offline-extraction failure mode in the README.

## Constraints

- Keep Pillow/fpdf2/pypdf usage in the dev dependency path.
- Seed all randomness and preserve byte-identical rebuilds.
- Do not modify existing txt/pdf fixtures or frozen spec constants.
- No OCR or VLM backend implementation in this phase.

## Verification

```bash
uv run python -m fixtures.generator.scanned_build
uv run python -m fixtures.generator.scanned_build --verify
uv run pytest -q
uv run ruff check .
uv run python -m eval.run --as-of 2026-09-03
```
