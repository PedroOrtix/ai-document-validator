# AI Usage & Engineering Governance

This project was developed with the assistance of AI coding tools, adhering strictly to the guidelines in `ai-engineer-technical-assessment.pdf`: maintaining clear technical ownership, reviewing all diffs, and critically challenging generated suggestions.

---

## 1. Tools and Roles

AI assistants were utilized across the development lifecycle for specific, bounded engineering tasks:

- **AI Assistants (Claude / Codex / Antigravity)**: Used for scaffolding boilerplate (FastAPI endpoints, Pydantic models), drafting test fixtures, generating synthetic document variants, and iterating on regular expressions.
- **Human Engineer**: Defined system requirements and domain models, established the offline $0 floor constraint, designed the rules engine architecture, conducted line-by-line diff reviews, verified test coverage, and directed the evaluation methodology.

### Model Usage
- **Development & Coding Assistance**: Models via OpenRouter and Gemini were used for code drafting, refactoring, and benchmark analysis.
- **Production Runtime Ingestion**: `z-ai/glm-5.3-flash` powers `LLMExtractor` and `VisionExtractor`, selected for its balance of ~2s latency and ~$0.0001/doc cost.

---

## 2. Concrete Examples of Rejected AI Suggestions

A core tenet of this project was challenging LLM biases and avoiding common pitfalls. Five concrete examples demonstrate how AI proposals were critically evaluated and rejected:

### A. Rejecting Pre-training OCR Biases (Tesseract / EasyOCR vs PP-OCRv5 ONNX)
- **The AI Suggestion**: When asked for local OCR options, coding assistants repeatedly suggested legacy libraries (`pytesseract`, `easyocr`) due to training data frequency, or conversely, reached for heavy 1B-parameter autoregressive document VLMs (e.g., `PaddlePaddle/PaddleOCR-VL-1.6` via Hugging Face Transformers).
- **Why Rejected**: The author consulted current document parsing benchmarks and evaluated runtime constraints. A live CPU test of `PaddleOCR-VL-1.6` took **~30+ seconds per page** and required a multi-gigabyte PyTorch image. Tesseract/EasyOCR, on the other hand, produce unacceptable line scrambling on textured B2B invoices.
- **Human Decision**: Selected **RapidOCR (PP-OCRv5)** packaged via ONNX Runtime: a lightweight (~15MB) pure wheel running on CPU in **~700 ms** with zero PyTorch dependencies and pre-cached weights in Docker, fulfilling the $0 offline requirement with SOTA line recognition.

### B. Rejecting Endogenous LLM Confidence ("Fighting Fire with Fire")
- **The AI Suggestion**: Assistants suggested prompting the LLM to return its own self-reported confidence score (e.g., `"confidence": 0.98`) per extracted field.
- **Why Rejected**: *A model grading its own certainty is fighting fire with fire.* LLM-reported probabilities are poorly calibrated, prone to overconfidence on hallucinations, and cannot be trusted in audit-grade compliance.
- **Human Decision**: Enforced an **exogenous confidence model** based on observable evidence tiers:
  - Labeled regex match: `0.95`
  - Structural pattern match: `0.80` – `0.90`
  - LLM structured extraction: anchored at `0.75` (parsed value present) or `0.60` (explicitly absent)
  - Missing required field: `0.00`
  This was validated empirically in the evaluation harness via the `conf-ok` vs `conf-bad` separation metric.

### C. Rejecting Silent `FAIL` on Missing Data in Compliance Rules
- **The AI Suggestion**: Early rule implementations marked a document as `FAIL` whenever a required field (e.g., `invoice_date` or `total_amount`) was absent from the document.
- **Why Rejected**: In B2B compliance workflows, conflating *"the document broke a rule"* with *"the document is missing data"* is dangerous. A `FAIL` blocks a supplier automatically, whereas missing data requires human escalation (`REVIEW`).
- **Human Decision**: Introduced the `inconclusive=True` rule state. If input data is missing, the rule is marked inconclusive and the overall verdict defaults to `REVIEW`. A `FAIL` is strictly reserved for documents where extracted data actively violates a threshold (e.g., date older than 90 days or negative amount).

### D. Enforcing Anti-Overfitting Discipline on the Evaluation Dataset
- **The AI Suggestion**: Assistants proposed adding specific regex overrides targeting exact strings found in failing golden fixtures.
- **Why Rejected**: Patching regexes to pass known synthetic fixtures leads directly to benchmark overfitting and brittle real-world performance.
- **Human Decision**: Built the golden dataset (78 fixtures) **before** refining the extraction floor, and insisted on generalizable structural improvements:
  1. 2D vertical center line-clustering (`_sort_boxes_reading_order`) to handle arbitrary OCR bounding-box order.
  2. Multi-line vertical key-value pairing across consecutive lines.
  3. Locale-independent Spanish written month mappings (`14 ago 2026` $\to$ `2026-08-14`).

### E. Ground Truth Oracle Consistency on Compliance Rules
- **The Finding**: When evaluating rule outcomes against synthetic documents where currency was absent under a configured currency whitelist (`allowed_currencies=["EUR", "GBP"]`), initial fixtures marked the expected verdict as `PASS`.
- **Why Corrected**: Under compliance specifications, missing required data needed to evaluate a configured rule cannot pass; it is inconclusive and must route to `REVIEW` (`RuleResult.severity="review"`).
- **Human Decision**: The ground truth generation script (`fixtures.generator.spec_v2`) and dataset manifests were deterministically regenerated to reflect this mathematical rule invariant across both truth files and test assertions, ensuring 100% formal consistency between documented rule semantics and expected outcomes.

---

## 3. Verification & Quality Control

To ensure code correctness and maintain strict discipline across all changes:

1. **Mandatory Diff Review**: Every code change was reviewed line-by-line before merging.
2. **Continuous TDD**: Unit and integration tests were maintained alongside code, resulting in **403 passing tests** (`pytest`, running in <5 seconds).
3. **Quantitative Evaluation Harness**: Rather than qualitative spot-checks, changes were measured against the 78 golden fixtures (`eval.run`), tracking exact match, precision, recall, and regression gates.
4. **Hermetic & Live Verification**:
   - The offline OCR floor was verified locally with `RUN_REAL_OCR=1` and in containerized Docker builds.
   - The LLM and VLM pathways were verified against live OpenRouter endpoints.
   - Real PDF fixtures (`fixtures/golden/pdf_en_t0_0.pdf`) were tested end-to-end via `curl` against the running FastAPI service.

---

## 4. Extraction Prompts & System Instructions

The system instructions used for document extraction are embedded directly in the codebase and bound to strict Pydantic schemas:

### Core LLM Structured Extraction Prompt
The canonical system prompt governing `LLMExtractor` lives in [`src/docvalidator/extraction/llm.py`](src/docvalidator/extraction/llm.py):

```text
You extract supplier invoice fields into the requested structured schema. Return the six
fields "supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", and
"tax_id". Use null for absent fields, ISO dates (YYYY-MM-DD), float amounts, and ISO-4217
currency codes.
```

The extraction binds directly to the Pydantic `InvoiceExtraction` schema using LangChain's `with_structured_output`, enforcing strict types without relying on free-form conversational text.

### Multimodal VLM Scanned Document Extraction Prompt (`VISION_INSTRUCTION`)
The specialized multimodal instruction governing `VisionExtractor` when interpreting rasterized PDF pages and noisy scanned images lives in [`src/docvalidator/extraction/llm.py`](src/docvalidator/extraction/llm.py):

```text
Read the scanned invoice image and extract exactly these six fields: "supplier_name",
"invoice_number", "invoice_date", "total_amount", "currency", "tax_id". Use null for
fields that are not visible. invoice_date must be ISO YYYY-MM-DD. total_amount must be the
grand total (never subtotal or tax), as a plain number without currency symbols or thousand
separators. currency must be the ISO 4217 code (EUR, GBP...), null if only symbols are
visible and ambiguous. tax_id is the VAT/registration identifier, null if absent.
```

This instruction explicitly addresses visual distractor disambiguation (distinguishing grand total from tax/subtotal, mapping ambiguous currency symbols to ISO 4217 or null) while binding to the exact same canonical schema.
