"""LLM-backed extraction against an OpenAI-compatible chat completions API."""

import json
import re
import time
from typing import Any

import httpx

from docvalidator.domain.models import DocumentExtraction, ExtractedField, ExtractionMetadata
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.settings import LLMSettings

SYSTEM_PROMPT = (
    "You extract supplier invoice fields. Return ONLY strict JSON with keys "
    '"supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", '
    '"tax_id". Use null for absent fields, ISO dates (YYYY-MM-DD), float amounts, and '
    "ISO-4217 currency codes. Example: "
    '{"supplier_name":"ACME Ltd","invoice_number":"INV-1",'
    '"invoice_date":"2026-01-31","total_amount":123.45,"currency":"EUR",'
    '"tax_id":"DE123456789"}'
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


class LLMConfigurationError(ExtractionError):
    """Raised when the LLM backend is not configured correctly."""


class LLMRequestError(ExtractionError):
    """Raised when the LLM backend cannot be reached or returns an API error."""


class LLMParsingError(ExtractionError):
    """Raised when the LLM response is not valid canonical JSON."""


class LLMTimeoutError(ExtractionError):
    """Raised when the LLM backend does not respond before the timeout."""


def _strip_markdown_fences(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    return _FENCE_PATTERN.sub("", stripped).strip()


def parse_llm_response(
    raw_content: str,
    response_payload: dict[str, Any],
    model: str,
) -> DocumentExtraction:
    """Parse one LLM completion into the canonical extraction model."""
    try:
        fields_payload = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError):
        try:
            fields_payload = json.loads(_strip_markdown_fences(raw_content))
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMParsingError("LLM response is not valid JSON") from exc

    if not isinstance(fields_payload, dict) or set(fields_payload) != _FIELD_NAMES:
        raise LLMParsingError("LLM response must contain exactly the six required fields")

    total_tokens = response_payload.get("usage", {}).get("total_tokens")
    fields = {
        name: ExtractedField(
            value=fields_payload[name],
            confidence=_LLM_CONFIDENCE,
            evidence=raw_content,
        )
        for name in _FIELD_NAMES
    }
    metadata = ExtractionMetadata(
        backend="llm",
        model=model,
        provider="openrouter",
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
    )
    try:
        return DocumentExtraction(fields=fields, metadata=metadata)
    except ValueError as exc:
        raise LLMParsingError("LLM response has invalid field values") from exc


class LLMExtractor(Extractor):
    """Extract invoice fields using an OpenRouter-compatible chat completions API."""

    def __init__(
        self,
        settings: LLMSettings | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings or LLMSettings()
        self._transport = transport

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        if not self.settings.openrouter_api_key:
            raise LLMConfigurationError(
                "OPENROUTER_API_KEY is not configured; "
                "configure OPENROUTER_API_KEY or use the offline backend"
            )

        started_at = time.perf_counter()
        response_payload = self._request_llm(document.to_text())
        duration_ms = (time.perf_counter() - started_at) * 1000
        raw_content = response_payload["choices"][0]["message"]["content"]
        extraction = parse_llm_response(
            raw_content,
            response_payload,
            self.settings.validator_llm_model,
        )
        return extraction.model_copy(
            update={
                "metadata": extraction.metadata.model_copy(
                    update={"duration_ms": duration_ms}
                )
            }
        )

    def _request_llm(self, text: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.settings.openrouter_api_key}"}
        payload = {
            "model": self.settings.validator_llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }
        try:
            with httpx.Client(
                base_url=self.settings.openrouter_base_url,
                timeout=self.settings.validator_llm_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.post("/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("LLM extraction timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMRequestError("unable to reach the LLM provider") from exc

        if response.status_code in {401, 403}:
            raise LLMConfigurationError("LLM provider rejected the configured API key")
        if response.status_code >= 400:
            raise LLMRequestError(f"LLM provider returned HTTP {response.status_code}")
        try:
            payload = response.json()
            return payload
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMParsingError("LLM provider returned an invalid completion response") from exc
