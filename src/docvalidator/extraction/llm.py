"""LLM-backed extraction with LangChain structured output via OpenRouter."""

import json
import re
import time
from datetime import date
from typing import Any

import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from openai import APIStatusError, AuthenticationError, PermissionDeniedError
from pydantic import BaseModel, Field, ValidationError

from docvalidator.domain.models import DocumentExtraction, ExtractedField, ExtractionMetadata
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.settings import LLMSettings


class InvoiceExtraction(BaseModel):
    """The six nullable invoice fields requested from the LLM."""

    supplier_name: str | None
    invoice_number: str | None
    invoice_date: date | None = Field(description="ISO date YYYY-MM-DD")
    total_amount: float | None
    currency: str | None = Field(description="ISO 4217 currency code")
    tax_id: str | None


SYSTEM_PROMPT = (
    "You extract supplier invoice fields into the requested structured schema. "
    'Return the six fields "supplier_name", "invoice_number", "invoice_date", '
    '"total_amount", "currency", and "tax_id". Use null for absent fields, ISO dates '
    "(YYYY-MM-DD), float amounts, and ISO-4217 currency codes."
)

_FIELD_NAMES = {
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
    "tax_id",
}
_LLM_CONFIDENCE = 0.75
_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_StructuredResponse = dict[str, Any] | InvoiceExtraction | AIMessage
_StructuredModel = Runnable[Any, _StructuredResponse]


class LLMConfigurationError(ExtractionError):
    """Raised when the LLM backend is not configured correctly."""


class LLMRequestError(ExtractionError):
    """Raised when the LLM backend cannot be reached or returns an API error."""


class LLMParsingError(ExtractionError):
    """Raised when the LLM response cannot be parsed into canonical fields."""


class LLMTimeoutError(ExtractionError):
    """Raised when the LLM backend does not respond before the timeout."""


class _UnsupportedResponseFormat(Exception):
    """Internal signal used to select the next structured-output strategy."""


def _strip_markdown_fences(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    return _FENCE_PATTERN.sub("", stripped).strip()


def _coerce_field_value(name: str, raw: Any) -> Any:
    """Coerce raw LLM values to the types the domain model expects."""
    if raw is None:
        return None
    if name == "invoice_date":
        return date.fromisoformat(str(raw))
    if name == "total_amount":
        return float(raw)
    return str(raw)


def _build_extraction(
    fields_payload: dict[str, Any],
    total_tokens: int | None,
    model: str,
    evidence_context: str,
) -> DocumentExtraction:
    if set(fields_payload) != _FIELD_NAMES:
        raise LLMParsingError("LLM response must contain exactly the six required fields")

    try:
        fields = {
            name: ExtractedField(
                value=_coerce_field_value(name, fields_payload[name]),
                confidence=_LLM_CONFIDENCE,
                evidence=(
                    str(fields_payload[name])
                    if fields_payload[name] is not None
                    else evidence_context
                ),
            )
            for name in _FIELD_NAMES
        }
        metadata = ExtractionMetadata(
            backend="llm",
            model=model,
            provider="openrouter",
            total_tokens=total_tokens,
        )
        return DocumentExtraction(fields=fields, metadata=metadata)
    except (ValueError, TypeError) as exc:
        raise LLMParsingError(f"LLM response has invalid field values: {exc}") from exc


def _total_tokens(response: AIMessage | dict[str, Any] | None) -> int | None:
    if isinstance(response, AIMessage):
        usage = response.usage_metadata
        if usage and isinstance(usage.get("total_tokens"), int):
            return usage["total_tokens"]
        response = response.response_metadata
    if not isinstance(response, dict):
        return None
    usage = response.get("usage", response.get("token_usage", {}))
    if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
        return usage["total_tokens"]
    if isinstance(response.get("total_tokens"), int):
        return response["total_tokens"]
    return None


def parse_structured_extraction(
    fields: InvoiceExtraction | dict[str, Any],
    response: AIMessage | dict[str, Any] | None,
    model: str,
) -> DocumentExtraction:
    """Validate a structured response and map it to the canonical extraction."""
    if isinstance(fields, InvoiceExtraction):
        fields_payload = fields.model_dump(mode="python")
    else:
        if not isinstance(fields, dict) or set(fields) != _FIELD_NAMES:
            raise LLMParsingError("LLM response must contain exactly the six required fields")
        try:
            fields_payload = InvoiceExtraction.model_validate(fields).model_dump(mode="python")
        except ValidationError as exc:
            raise LLMParsingError(f"LLM response has invalid field values: {exc}") from exc

    context = json.dumps(fields_payload, default=str)
    return _build_extraction(fields_payload, _total_tokens(response), model, context)


def parse_llm_response(
    raw_content: str,
    response_payload: dict[str, Any],
    model: str,
) -> DocumentExtraction:
    """Defensively parse one JSON completion; retained for raw fallback and replay."""
    try:
        fields_payload = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        try:
            fields_payload = json.loads(_strip_markdown_fences(raw_content))
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMParsingError("LLM response is not valid JSON") from exc

    if not isinstance(fields_payload, dict):
        raise LLMParsingError("LLM response must contain exactly the six required fields")
    return _build_extraction(fields_payload, _total_tokens(response_payload), model, raw_content)


def _message_text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else json.dumps(content)


class LLMExtractor(Extractor):
    """Extract invoice fields through a LangChain-compatible OpenRouter model."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        model: BaseChatModel | None = None,
        structured_model: Runnable[Any, _StructuredResponse] | None = None,
    ) -> None:
        self.settings = settings or LLMSettings()
        self._model = model
        self._structured_model = structured_model
        self._method = "json_schema"

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        if not self.settings.openrouter_api_key:
            raise LLMConfigurationError(
                "OPENROUTER_API_KEY is not configured; "
                "configure OPENROUTER_API_KEY or use the offline backend"
            )

        started_at = time.perf_counter()
        extraction = self._invoke(document.to_text())
        duration_ms = (time.perf_counter() - started_at) * 1000
        return extraction.model_copy(
            update={
                "metadata": extraction.metadata.model_copy(
                    update={"duration_ms": duration_ms}
                )
            }
        )

    def _build_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model

        from langchain_openai.chat_models import base as langchain_openai_base

        return langchain_openai_base.ChatOpenAI(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            model=self.settings.validator_llm_model,
            temperature=0,
            timeout=self.settings.validator_llm_timeout_seconds,
            max_retries=0,
            **(
                {
                    "extra_body": {
                        "reasoning": {"effort": self.settings.validator_llm_reasoning_effort}
                    }
                }
            if self.settings.validator_llm_reasoning_effort
            else {}
        ),
        )

    def _invoke(self, text: str) -> DocumentExtraction:
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=text)]
        model = self._build_model()
        try:
            output = self._invoke_structured(model, messages)
        except _UnsupportedResponseFormat:
            if self._method != "json_schema":
                self._method = "raw"
                return self._extract_raw(model, messages)
            self._method = "json_mode"
            try:
                output = self._invoke_structured(model, messages)
            except _UnsupportedResponseFormat:
                self._method = "raw"
                return self._extract_raw(model, messages)

        if isinstance(output, AIMessage):
            return parse_llm_response(
                _message_text(output),
                output.response_metadata,
                self.settings.validator_llm_model,
            )
        if isinstance(output, InvoiceExtraction):
            output = {"parsed": output, "raw": None}
        if not isinstance(output, dict) or "parsed" not in output:
            raise LLMParsingError("LLM structured output has an unexpected shape")
        parsed = output["parsed"]
        if not isinstance(parsed, InvoiceExtraction | dict):
            raise LLMParsingError("LLM structured output has an unexpected type")
        return parse_structured_extraction(
            parsed,
            output.get("raw"),
            self.settings.validator_llm_model,
        )

    def _invoke_structured(
        self,
        model: BaseChatModel,
        messages: list[BaseMessage],
    ) -> _StructuredResponse:
        chain = self._structured_model
        if chain is None or self._method != "json_schema":
            try:
                chain = model.with_structured_output(  # type: ignore[assignment,return-value]
                    InvoiceExtraction,
                    method=self._method,
                    include_raw=True,
                )
            except Exception as exc:
                raise self._classify_error(exc) from exc
        try:
            return chain.invoke(messages)
        except Exception as exc:
            classified = self._classify_error(exc)
            raise classified from exc

    def _extract_raw(
        self,
        model: BaseChatModel,
        messages: list[BaseMessage],
    ) -> DocumentExtraction:
        try:
            response = model.invoke(messages)
        except Exception as exc:
            raise self._classify_error(exc) from exc
        if not isinstance(response, AIMessage):
            raise LLMParsingError("LLM provider returned an invalid completion response")
        return parse_llm_response(
            _message_text(response),
            response.response_metadata,
            self.settings.validator_llm_model,
        )

    def _classify_error(self, exc: Exception) -> Exception:
        if isinstance(
            exc,
            _UnsupportedResponseFormat
            | LLMConfigurationError
            | LLMRequestError
            | LLMParsingError
            | LLMTimeoutError,
        ):
            return exc
        if isinstance(exc, AuthenticationError | PermissionDeniedError):
            error = LLMConfigurationError("LLM provider rejected the configured API key")
            raise error from exc
        if isinstance(exc, APIStatusError):
            if exc.status_code in {401, 403}:
                error = LLMConfigurationError(
                    "LLM provider rejected the configured API key"
                )
                raise error from exc
            if self._uses_response_format(exc):
                return _UnsupportedResponseFormat()
            error = LLMRequestError(f"LLM provider returned HTTP {exc.status_code}")
            raise error from exc
        if isinstance(exc, openai.APITimeoutError | TimeoutError):
            error = LLMTimeoutError("LLM extraction timed out")
            raise error from exc
        if isinstance(exc, OutputParserException | ValidationError):
            raise LLMParsingError("LLM response has invalid field values") from exc
        if isinstance(exc, openai.APIConnectionError | ConnectionError):
            raise LLMRequestError("unable to reach the LLM provider") from exc
        if self._uses_response_format(exc):
            return _UnsupportedResponseFormat()
        if isinstance(exc, ValueError | TypeError | KeyError | IndexError):
            raise LLMParsingError(
                "LLM provider returned an invalid completion response"
            ) from exc
        raise LLMRequestError("LLM extraction failed") from exc

    @staticmethod
    def _uses_response_format(exc: Exception) -> bool:
        return "response_format" in str(exc).lower()
