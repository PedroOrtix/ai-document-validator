# AI Usage & Engineering Governance

This project was developed with the active assistance of AI engineering tools, adhering strictly to the guidelines in `ai-engineer-technical-assessment.pdf` (pages 1, 6, and 7): maintaining clear human technical ownership, critically evaluating suggestions, and remaining fully accountable for every architectural decision and line of code merged.

---

## 1. Tools, Architecture & Engineering Division of Labor

Rather than using an AI assistant simply for inline code autocomplete, development followed a disciplined, multi-agent workflow with clear role separation:

| Role / Tool | Environment | Key Responsibilities |
|---|---|---|
| **Pedro (Human Lead)** | **System Architect & Director** | Defined domain boundaries, compliance invariants, the non-negotiable $0 offline floor, the three-pipeline vision, inconclusive/review logic, and conducted line-by-line diff reviews before any merge. |
| **Hermes Desktop** | **Meta-Orchestrator** | Managed high-level task planning, decomposed technical phases, handled task queues, and orchestrated isolated Git worktree lifecycles. |
| **Codex CLI** | **Autonomous TDD Worker** | Executed implementation within sandboxed Git worktrees. Operated strictly under Test-Driven Development (TDD): drafting unit/integration tests before writing implementation code. |
| **Antigravity 2.0** | **Pair Assistant & Fast Auditor** | Powered by Gemini. Leveraged for high token-throughput tasks: technical auditing against assessment requirements, fast sanity checks, dual OpenAPI schema alignment, and documentation generation and refinement. |

### Model Selection Strategy
- **Development & Auditing**: High-throughput reasoning models via Antigravity 2.0 and OpenRouter were used for rapid verification, test scaffolding, and documentation.
- **Production Runtime Ingestion**: `z-ai/glm-5.3-flash` powers the runtime `LLMExtractor` and `VisionExtractor`, selected for its optimal balance of ~2s latency and ~$0.0001/doc cost.

---

## 2. Concrete Examples of Rejected AI Suggestions

A core principle of this implementation was critically challenging AI defaults, pre-training biases, and naive patterns. Five concrete examples illustrate where AI proposals were evaluated and rejected:

### A. Rejecting a Naive Regex Monolith in Favor of Three Swappable Pipelines
- **The AI Suggestion**: Early proposals suggested relying solely on regex heuristics for the entire document extraction pipeline, or building a monolithic extractor.
- **Why Rejected**: Firsthand production experience demonstrates that real-world B2B documents break naive heuristic parsing immediately. Variable table layouts, multi-column arrangements, and visual noise cannot be reliably handled by regexes alone. Conversely, relying exclusively on an LLM violates the $0 offline requirement.
- **Human Decision**: Architected **three swappable extraction pipelines** coordinated by an `AutoRouter`:
  1. *Lane 1 (Heuristic / OCR Floor)*: 100% offline, $0 cost, running local ONNX OCR.
  2. *Lane 2 (Structured SLM)*: Fast, low-cost text extraction via LangChain structured output.
  3. *Lane 3 (Multimodal VLM)*: Direct visual reasoning for degraded, scanned, or complex rasterized invoices.

### B. Pivoting from Legacy OCR & Heavy VLMs to PP-OCRv5 ONNX with Spatial Clustering
- **The AI Suggestion**: Assistants initially defaulted to legacy OCR engines (`pytesseract`, `easyocr`), or alternatively recommended heavy 1B-parameter document VLMs (`PaddlePaddle/PaddleOCR-VL-1.6` via Hugging Face Transformers).
- **Why Rejected**:
  - Legacy engines (`pytesseract`, `easyocr`) are notoriously mediocre on textured B2B invoices and struggle with non-English date structures.
  - Heavy autoregressive VLMs (`PaddleOCR-VL-1.6`) were benchmarked on a CPU host: processing took **~30+ seconds per page** and required a multi-gigabyte PyTorch Docker image, rendering it unviable for local or production use.
  - Additionally, naive box joining (`"\n".join(...)`) returned text scrambled out of reading order.
- **Human Decision**:
  - Selected **RapidOCR (PP-OCRv5)** packaged via ONNX Runtime: a lightweight (~15MB) wheel running on CPU in **~700 ms** with zero PyTorch dependencies and pre-cached weights in Docker ($0 offline floor).
  - Designed a custom **2D vertical-tolerance line clustering algorithm** (`_sort_boxes_reading_order` in `src/docvalidator/extraction/ocr.py`) that calculates bounding-box centers, clusters horizontal text lines within vertical thresholds, and sorts tokens left-to-right to guarantee natural reading order.

### C. Rejecting Endogenous LLM Confidence ("Fighting Fire with Fire")
- **The AI Suggestion**: Assistants suggested prompting the LLM to return its own self-reported confidence score (e.g., `"confidence": 0.98`) per extracted field.
- **Why Rejected**: *A model grading its own certainty is fighting fire with fire.* LLM-reported probabilities are poorly calibrated, prone to overconfidence on hallucinations, and cannot be trusted in audit-grade compliance.
- **Human Decision**: Enforced an **exogenous confidence model** based on observable evidence tiers:
  - Labeled regex match: `0.95`
  - Structural pattern match: `0.80` – `0.90`
  - LLM structured extraction: anchored at `0.75` (parsed value present) or `0.60` (explicitly absent)
  - Missing required field: `0.00`
  This was validated empirically in the evaluation harness via the `conf-ok` vs `conf-bad` separation metric.

### D. Rejecting Silent `FAIL` on Missing Data in Compliance Rules
- **The AI Suggestion**: Early rule implementations marked a document as `FAIL` whenever a required field (e.g., `invoice_date` or `total_amount`) was absent.
- **Why Rejected**: In B2B compliance workflows, conflating *"the document broke a business rule"* with *"the extractor failed to locate a field"* is dangerous. A `FAIL` automatically blocks a supplier or halts payment, whereas missing data requires human escalation (`REVIEW`).
- **Human Decision**: Introduced the `inconclusive=True` rule state and `requests_review`. If data needed to judge a rule is missing, the rule is marked inconclusive and the overall verdict defaults to `REVIEW`. A `FAIL` is strictly reserved for documents where extracted data actively violates a threshold (e.g., date older than 90 days or negative amount).

### E. Enforcing Anti-Overfitting Discipline on Evaluation Fixtures
- **The AI Suggestion**: Assistants proposed adding specific regex overrides targeting exact string patterns found in failing golden fixtures.
- **Why Rejected**: Patching regexes to pass known synthetic fixtures leads directly to benchmark overfitting and brittle real-world performance.
- **Human Decision**: Built generalizable structural improvements rather than dataset-specific patches:
  1. Multi-line vertical key-value pairing across consecutive lines.
  2. Locale-independent Spanish written month mappings (`14 ago 2026` $\to$ `2026-08-14`).
  3. Strict isolation between test fixture generation and extraction logic.

---

## 3. Evaluation Dataset Curation & Verification Protocol

### Bilingual & Multi-Tier Golden Set (78 Fixtures)
The evaluation dataset was deliberately designed across two languages—**Spanish and English**—to reflect European B2B compliance reality, structured across three difficulty tiers:
- **Tier 0**: Clean digital documents with standard layout.
- **Tier 1**: Complex real-world layout variations, multi-column tables, and varied date notations.
- **Tier 2**: Degraded, scanned raster images with noise and rotation artifacts.
- **Adversarial Edge Cases**: Future invoice dates, non-whitelisted currencies, missing totals, and ambiguous currency symbols.

### Subagent Validation Discipline
To prevent synthetic data hallucinations (such as line items not summing to the total, impossible calendar dates like February 30, or syntactically invalid tax IDs), specialized **validator subagents** were deployed during fixture generation. These subagents audited the synthetic documents against strict mathematical and domain rules before freezing the golden ground truth oracle.

### Verification Quality Gates
1. **Continuous TDD**: 403 unit and integration tests (`pytest`, running in <5 seconds) maintained across all modules.
2. **Quantitative Evaluation Harness**: Every extraction change was benchmarked across the 78 golden fixtures (`eval.run`), tracking exact match, precision, recall, and regression gates.
3. **Hermetic & Live Verification**:
   - The offline OCR floor was verified locally with `RUN_REAL_OCR=1` and in containerized Docker builds.
   - The SLM and VLM pathways were verified against live OpenRouter endpoints.
   - Real PDF fixtures (`fixtures/golden/pdf_en_t0_0.pdf`) were tested end-to-end via `curl` against the running FastAPI service.

---

## 4. Extraction Prompts & Defensive Prompt Engineering

The system instructions used for document extraction are embedded directly in [`src/docvalidator/extraction/llm.py`](src/docvalidator/extraction/llm.py) and bound to strict Pydantic schemas via LangChain's `with_structured_output`:

### Core LLM Structured Extraction Prompt
```text
You extract supplier invoice fields into the requested structured schema. Return the six
fields "supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", and
"tax_id". Use null for absent fields, ISO dates (YYYY-MM-DD), float amounts, and ISO-4217
currency codes.
```

### Multimodal VLM Scanned Document Extraction Prompt (`VISION_INSTRUCTION`)
```text
Read the scanned invoice image and extract exactly these six fields: "supplier_name",
"invoice_number", "invoice_date", "total_amount", "currency", "tax_id". Use null for
fields that are not visible. invoice_date must be ISO YYYY-MM-DD. total_amount must be the
grand total (never subtotal or tax), as a plain number without currency symbols or thousand
separators. currency must be the ISO 4217 code (EUR, GBP...), null if only symbols are
visible and ambiguous. tax_id is the VAT/registration identifier, null if absent.
```

### Prompt Design Decisions & Failure Modes Prevented
Each clause in these prompts addresses a specific, observed failure mode of generative models:

1. **`"total_amount must be the grand total (never subtotal or tax)"`**:
   - *Failure Mode Prevented*: Without this explicit guardrail, models routinely selected the net taxable base (subtotal) or the tax line item instead of the final invoice total.
2. **`"as a plain number without currency symbols or thousand separators"`**:
   - *Failure Mode Prevented*: Models frequently return formatted strings (e.g., `"$1,250.00"` or `"1.250,00 €"`). Pydantic's strict `float` validator rejects strings containing commas or symbols with a `ValidationError`. Enforcing plain numeric output guarantees clean type coercion.
3. **`"currency must be the ISO 4217 code (EUR, GBP...), null if only symbols are visible and ambiguous"`**:
   - *Failure Mode Prevented*: The symbol `$` is shared by USD, CAD, AUD, and others. Unconstrained models systematically guess `USD` even on Canadian or Australian invoices. Requiring an explicit ISO code or null prevents false currency validation against whitelists.
4. **`"tax_id is the VAT/registration identifier, null if absent"`**:
   - *Failure Mode Prevented*: Prevents the model from populating `tax_id` with unrelated numbers found in invoice headers, such as postal codes, commercial register numbers, or bank account IBANs.
