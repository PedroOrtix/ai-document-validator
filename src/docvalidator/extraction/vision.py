"""Vision-LLM extraction for scanned invoice PDFs via OpenRouter."""

import base64
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.llm import (
    VISION_INSTRUCTION,
    InvoiceExtraction,
    LLMExtractor,
    _build_chat_model,
)
from docvalidator.extraction.rendering import render_pdf_pages_to_png
from docvalidator.settings import LLMSettings


class VisionExtractor(LLMExtractor, Extractor):
    """Extract invoice fields from the rendered first page of a PDF."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        model: BaseChatModel | None = None,
        structured_model: Runnable[Any, dict[str, AIMessage | InvoiceExtraction | None]]
        | None = None,
    ) -> None:
        super().__init__(settings, model, structured_model)

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        if not self.settings.openrouter_api_key:
            self._raise_configuration_error()

        pages = self._render_pages(document)
        started_at = time.perf_counter()
        extraction = self._invoke_page(pages[0])
        duration_ms = (time.perf_counter() - started_at) * 1000
        return extraction.model_copy(
            update={
                "metadata": extraction.metadata.model_copy(
                    update={
                        "backend": "vlm",
                        "model": self.settings.validator_vlm_model,
                        "duration_ms": duration_ms,
                    }
                )
            }
        )

    def _render_pages(self, document: DocumentInput) -> list[bytes]:
        if document.pdf_bytes is None:
            raise ExtractionError("VisionExtractor only accepts PDF documents")
        pages = render_pdf_pages_to_png(document.pdf_bytes)
        if not pages:
            raise ExtractionError("PDF has no pages to render")
        return pages

    def _invoke_page(self, png_bytes: bytes) -> DocumentExtraction:
        image_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": VISION_INSTRUCTION},
            ]
        )
        return self._invoke_messages([message])

    # The shared chain performs one structured-output call; parse failures are
    # typed errors rather than retries through another response format.

    def _build_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model

        return _build_chat_model(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            model=self.settings.validator_vlm_model,
            temperature=0,
            timeout=self.settings.validator_vlm_timeout_seconds,
            max_retries=0,
            reasoning_effort=self.settings.validator_vlm_reasoning_effort,
        )
