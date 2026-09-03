"""LLM-backed extraction with LangChain structured output via OpenRouter."""

import json
import time
from datetime import date
from typing import Any, NoReturn

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

VISION_INSTRUCTION = (
    "Read the scanned invoice image and extract exactly these six fields: "
    '"supplier_name", "invoice_number", "invoice_date", "total_amount", '
    '"currency", "tax_id". Use null for fields that are not visible. '
    "invoice_date must be ISO YYYY-MM-DD. total_amount must be the grand "
    "total (never subtotal or tax), as a plain number without currency "
    "symbols or thousand separators. currency must be the ISO 4217 code "
    "(EUR, GBP...), null if only symbols are visible and ambiguous. "
    "tax_id is the VAT/registration identifier, null if absent."
)

_FIELD_NAMES = {
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
    "tax_id",
}
_LLM_CONFIDENCE_PRESENT = 0.75
_LLM_CONFIDENCE_ABSENT = 0.6
_StructuredResponse = dict[str, AIMessage | InvoiceExtraction | None]


class LLMConfigurationError(ExtractionError):
    """Raised when the LLM backend is not configured correctly."""


class LLMRequestError(ExtractionError):
    """Raised when the LLM backend cannot be reached or returns an API error."""


class LLMParsingError(ExtractionError):
    """Raised when the LLM response cannot be parsed into canonical fields."""


class LLMTimeoutError(ExtractionError):
    """Raised when the LLM backend does not respond before the timeout."""


def _coerce_field_value(name: str, raw: Any) -> Any:
    """Coerce raw LLM values to the types the domain model expects."""
    if raw is None:
        return None
    if name == "invoice_date":
        return date.fromisoformat(str(raw))
    if name == "total_amount":
        return float(raw)
    return str(raw)


def _llm_confidence(value: Any) -> float:
    """Evidence strength for one LLM-extracted field.

    The LLM is a single black-box call: there is no per-field internal signal
    to calibrate, so confidence reflects the *observable* evidence. A present,
    successfully typed value carries 0.75; an absent field carries 0.6 —
    meaning "the model saw the document and reported this field as absent",
    which is materially stronger evidence than the local parser's 0.0 for
    a missing field, but weaker than any parsed value.
    """
    return _LLM_CONFIDENCE_PRESENT if value is not None else _LLM_CONFIDENCE_ABSENT


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
                confidence=_llm_confidence(fields_payload[name]),
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

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        if not self.settings.openrouter_api_key:
            self._raise_configuration_error()

        started_at = time.perf_counter()
        extraction = self._invoke(document.to_text())
        duration_ms = (time.perf_counter() - started_at) * 1000
        return extraction.model_copy(
            update={"metadata": extraction.metadata.model_copy(update={"duration_ms": duration_ms})}
        )

    def _build_model(self) -> BaseChatModel:
        if self._model is not None:
            return self._model

        return _build_chat_model(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            model=self.settings.validator_llm_model,
            temperature=0,
            timeout=self.settings.validator_llm_timeout_seconds,
            max_retries=0,
            reasoning_effort=self.settings.validator_llm_reasoning_effort,
        )

    @staticmethod
    def _raise_configuration_error() -> NoReturn:
        raise LLMConfigurationError(
            "OPENROUTER_API_KEY is not configured; "
            "configure OPENROUTER_API_KEY or use the ocr backend"
        )

    def _invoke(self, text: str) -> DocumentExtraction:
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=text),
        ]
        return self._invoke_messages(messages)

    def _invoke_messages(self, messages: list[BaseMessage]) -> DocumentExtraction:
        model = self._build_model()
        chain = self._structured_model
        if chain is None:
            try:
                chain = model.with_structured_output(  # type: ignore[assignment,return-value]
                    InvoiceExtraction,
                    include_raw=True,
                )
            except Exception as exc:
                raise self._classify_error(exc) from exc
        try:
            output = chain.invoke(messages)
        except Exception as exc:
            raise self._classify_error(exc) from exc
        return self._parse_structured_output(output)

    def _parse_structured_output(
        self,
        output: _StructuredResponse,
    ) -> DocumentExtraction:
        if not isinstance(output, dict):
            raise LLMParsingError("LLM structured output has an unexpected shape")
        if "parsed" not in output or not isinstance(output["parsed"], InvoiceExtraction):
            raise LLMParsingError("LLM structured output is unparseable")
        if output.get("raw") is not None and not isinstance(output["raw"], AIMessage):
            raise LLMParsingError("LLM structured output has an unexpected raw response")
        return parse_structured_extraction(
            output["parsed"],
            output["raw"],
            self.settings.validator_llm_model,
        )

    def _classify_error(self, exc: Exception) -> Exception:
        if isinstance(
            exc,
            LLMConfigurationError | LLMRequestError | LLMParsingError | LLMTimeoutError,
        ):
            return exc
        if isinstance(exc, AuthenticationError | PermissionDeniedError):
            error = LLMConfigurationError("LLM provider rejected the configured API key")
            raise error from exc
        if isinstance(exc, APIStatusError):
            if exc.status_code in {401, 403}:
                error = LLMConfigurationError("LLM provider rejected the configured API key")
                raise error from exc
            error = LLMRequestError(f"LLM provider returned HTTP {exc.status_code}")
            raise error from exc
        if isinstance(exc, openai.APITimeoutError | TimeoutError):
            error = LLMTimeoutError("LLM extraction timed out")
            raise error from exc
        if isinstance(exc, OutputParserException | ValidationError):
            raise LLMParsingError("LLM response has invalid field values") from exc
        if isinstance(exc, openai.APIConnectionError | ConnectionError):
            raise LLMRequestError("unable to reach the LLM provider") from exc
        if isinstance(exc, ValueError | TypeError | KeyError | IndexError):
            raise LLMParsingError("LLM provider returned an invalid completion response") from exc
        raise LLMRequestError("LLM extraction failed") from exc


def _build_chat_model(
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    timeout: float,
    reasoning_effort: str,
    max_retries: int,
) -> BaseChatModel:
    from langchain_openai.chat_models import base as langchain_openai_base

    return langchain_openai_base.ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
        timeout=timeout,
        max_retries=0,
        **({"extra_body": {"reasoning": {"effort": reasoning_effort}}} if reasoning_effort else {}),
    )
