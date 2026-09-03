# AI Usage & Multi-Agent Orchestration Governance

This project was engineered under an explicit, disciplined **multi-agent orchestration workflow**.
The human author (**Pedro**) acted as Lead Architect and Technical Director: defining system requirements, enforcing operational constraints, verifying diffs, and making every critical design decision. No code was generated or merged blindly.

---

## 1. Tools, Meta-Stack, and Agent Roles

Rather than using an AI assistant simply for inline autocomplete, we deployed a hierarchical multi-agent engineering stack:

| Agent / Tool | Role in Workflow | Key Capabilities & Frameworks |
|---|---|---|
| **Human (Pedro)** | **Lead Architect & Director** | System specification, constraint definition (offline-first, $0 floor, B2B compliance semantics), review and sign-off on every Git diff, prompt engineering, anti-overfitting governance. |
| **Hermes Agent** | **Meta-Orchestrator** | Preset with **[Oh My Hermes](https://github.com/rlaope/oh-my-hermes)**. Acted as high-level planner and coordinator: decomposed phase briefs, spawned isolated worker tasks, managed Git branch lifecycles, and evaluated diffs before requesting human approval. |
| **Codex CLI** | **Parallel Worker Units** | Equipped with the **[Aegis](https://github.com/GanyuanRan/Aegis)** skill pack. Executed bulk implementation in sandboxed Git worktrees. Followed continuous TDD: authoring and running unit/integration tests before signaling task completion. |
| **Antigravity** | **Pair Programmer & Auditor** | Powered by **Gemini 3.8 Flash**. Conducted deep technical audits against `ai-engineer-technical-assessment.pdf`, refactored 2D spatial OCR line clustering, implemented dual-schema OpenAPI customization, and executed live multi-lane benchmarks. |

### Model Selection Strategy
- **Orchestration & Coding**: `z-ai/glm-5.3-flash` (in High and Max reasoning tiers via OpenRouter) was used for Hermes and Codex worker agents.
- **Audit & Synthesis**: `gemini-3.8-flash` in Antigravity for low-latency, high-precision code verification and assessment alignment.
- **Production Runtime Ingestion**: `z-ai/glm-5.3-flash` (in Low reasoning mode) powers the application's `LLMExtractor` and `VisionExtractor`, achieving an optimal balance of ~2s latency and ~$0.0001/doc cost.

---

## 2. Concrete Examples of Rejected AI Suggestions

A core tenet of this project was challenging LLM biases and avoiding common pitfalls. Four concrete examples demonstrate how AI proposals were critically evaluated and rejected:

### A. Rejecting Pre-training OCR Biases (Tesseract / EasyOCR vs PP-OCRv5 ONNX)
- **The AI Suggestion**: When asked for local OCR options, coding agents repeatedly suggested legacy libraries (`pytesseract`, `easyocr`) due to training data frequency, or conversely, reached for heavy 1B-parameter autoregressive document VLMs (e.g., `PaddlePaddle/PaddleOCR-VL-1.6` via Hugging Face Transformers).
- **Why Rejected**: The author consulted current document parsing benchmarks and evaluated runtime constraints. A live CPU test of `PaddleOCR-VL-1.6` on a 24-core host took **~30+ seconds per page** and required a multi-gigabyte PyTorch image. Tesseract/EasyOCR, on the other hand, produce unacceptable line scrambling on textured B2B invoices.
- **Human Decision**: Selected **RapidOCR (PP-OCRv5)** packaged via ONNX Runtime: a lightweight (~15MB) pure wheel running on CPU in **~700 ms** with zero PyTorch dependencies and pre-cached weights in Docker, fulfilling the $0 offline requirement with SOTA line recognition.

### B. Rejecting Endogenous LLM Confidence ("Fighting Fire with Fire")
- **The AI Suggestion**: Agents suggested prompting the LLM to return its own self-reported confidence score (e.g., `"confidence": 0.98`) per extracted field.
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
- **The AI Suggestion**: Agents proposed adding specific regex overrides targeting exact strings found in failing golden fixtures.
- **Why Rejected**: Patching regexes to pass known synthetic fixtures leads directly to benchmark overfitting and brittle real-world performance.
- **Human Decision**: Built the golden dataset (78 fixtures) **before** refining the extraction floor, and insisted on generalizable structural improvements:
  1. 2D vertical center line-clustering (`_sort_boxes_reading_order`) to handle arbitrary OCR bounding-box order.
  2. Multi-line vertical key-value pairing across consecutive lines.
  3. Locale-independent Spanish written month mappings (`14 ago 2026` $\to$ `2026-08-14`).

---

## 3. Verification & Governance Protocol

To prevent code degradation, branch collision, or hallucinated tests across parallel agents, a multi-layered verification protocol was enforced:

1. **Sandboxed Worktrees & Isolated Branches**: Hermes orchestrated Codex instances in separate Git worktrees. Feature branches were self-contained, preventing code collisions.
2. **Mandatory Human Diff Review**: Codex was never permitted to commit or merge directly to `main`. Hermes evaluated diffs and required explicit human confirmation before merging.
3. **Continuous TDD via Aegis**: Every phase implemented or updated unit and integration tests before writing implementation code. The test suite expanded systematically to **381 passing tests** (`pytest`, running in <5 seconds).
4. **Quantitative Evaluation Harness**: Rather than qualitative spot-checks, every extraction change was measured against the 78 golden fixtures (`eval.run`), tracking exact match, precision, recall, slice breakdowns (Tiers 0–2, EN/ES), and regression gates.
5. **Real Environment & Live API Testing**:
   - The offline OCR floor was verified with `RUN_REAL_OCR=1` and in containerized Docker builds.
   - The LLM and VLM pathways were verified against live OpenRouter endpoints with real budget-capped tokens.
   - Real PDF fixtures (`fixtures/golden/pdf_en_t0_0.pdf`) were tested end-to-end via `curl` against the running FastAPI service.

---

## 4. Main Extraction Prompts & Instruction Artifacts

All briefs dispatched to coding workers were recorded and committed to [`docs/prompts/`](docs/prompts/) to preserve complete provenance.

### Core LLM Structured Extraction Prompt
The canonical system prompt governing `LLMExtractor` and `VisionExtractor` lives in [`src/docvalidator/extraction/llm.py`](src/docvalidator/extraction/llm.py):

```text
You extract supplier invoice fields into the requested structured schema. Return the six
fields "supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", and
"tax_id". Use null for absent fields, ISO dates (YYYY-MM-DD), float amounts, and ISO-4217
currency codes.
```

The extraction binds directly to the Pydantic `InvoiceExtraction` schema using LangChain's `with_structured_output`, enforcing strict types without relying on free-form conversational text.
