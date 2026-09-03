# Phase 11 — VisionExtractor (F1)

Build a vision-LLM extraction lane that sends the scanned invoice PDF **page image**, not its
text, to OpenRouter `z-ai/glm-5.3-flash` at reasoning effort `low`, reusing the existing
`InvoiceExtraction` structured output, error taxonomy, and canonical metadata contract.

## Scope

- Render PDF pages to PNG with `pypdfium2` at about 150 DPI; text-only input raises the typed
  extraction error. The fixtures are single-page, so send the first page and document that
  limitation.
- Implement injectable `VisionExtractor` with multimodal LangChain messages, OpenRouter
  structured output, provider/model/token/duration metadata, and shared classification helpers.
- Add explicit `extraction_backend: "vlm"` API wiring. Keep the default backend and F4 cascade
  behavior unchanged.
- Add network-free unit/integration tests, README/.env/docker documentation, and this brief.
- Do not alter fixtures/ or eval/.
