import json

import httpx
import pytest

from docvalidator.extraction.input import DocumentInput, ExtractionError
from docvalidator.extraction.llm import (
    LLMExtractor,
    LLMParsingError,
    LLMRequestError,
    LLMTimeoutError,
    parse_llm_response,
)
from docvalidator.settings import LLMSettings

_FIELD_NAMES = {
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
    "tax_id",
}
_FIELDS = dict.fromkeys(_FIELD_NAMES, None)
_FIELDS.update(
    {
        "supplier_name": "ACME Ltd",
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-31",
        "total_amount": 123.45,
        "currency": "EUR",
        "tax_id": "DE123456789",
    }
)
_RAW = json.dumps(_FIELDS)
_PAYLOAD = {"choices": [{"message": {"content": _RAW}}], "usage": {"total_tokens": 123}}
_SETTINGS = LLMSettings(openrouter_api_key="test-key", validator_llm_model="test-model")


def test_parse_clean_json_maps_fields_confidence_evidence_and_usage() -> None:
    extraction = parse_llm_response(_RAW, _PAYLOAD, "test-model")

    assert extraction.fields["supplier_name"].value == "ACME Ltd"
    assert extraction.fields["supplier_name"].confidence == 0.75
    assert extraction.fields["supplier_name"].evidence == _RAW
    assert extraction.fields["invoice_number"].value == "INV-1"
    assert extraction.fields["invoice_number"].confidence == 0.75
    assert extraction.fields["invoice_number"].evidence == _RAW
    assert extraction.metadata.backend == "llm"
    assert extraction.metadata.model == "test-model"
    assert extraction.metadata.provider == "openrouter"
    assert extraction.metadata.total_tokens == 123


def test_parse_fenced_json_uses_retry() -> None:
    fenced = "```json\n" + _RAW + "\n```"
    extraction = parse_llm_response(fenced, _PAYLOAD, "test-model")

    assert extraction.fields["supplier_name"].value == "ACME Ltd"
    assert extraction.metadata.total_tokens == 123


def test_parse_malformed_json_raises_error() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        parse_llm_response("{invalid", _PAYLOAD, "test-model")

    assert isinstance(exc_info.value, LLMParsingError)


def test_parse_extra_or_missing_fields_raises_error() -> None:
    fields = dict(_FIELDS)
    fields["unexpected"] = None

    with pytest.raises(ExtractionError, match="six required fields"):
        parse_llm_response(json.dumps(fields), _PAYLOAD, "test-model")


def test_parse_missing_usage_is_tolerated() -> None:
    extraction = parse_llm_response(_RAW, {"choices": _PAYLOAD["choices"]}, "test-model")

    assert extraction.metadata.total_tokens is None


def test_extractor_constructor_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ExtractionError, match="OPENROUTER_API_KEY"):
        LLMExtractor(LLMSettings()).extract(DocumentInput(text="hello"))


def test_extractor_maps_fields_from_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_PAYLOAD)

    extractor = LLMExtractor(_SETTINGS, transport=httpx.MockTransport(handler))
    extraction = extractor.extract(DocumentInput(text="hello"))

    assert extraction.fields["supplier_name"].value == "ACME Ltd"
    assert extraction.fields["supplier_name"].confidence == 0.75
    assert extraction.fields["supplier_name"].evidence == _RAW
    assert extraction.metadata.backend == "llm"
    assert extraction.metadata.model == "test-model"
    assert extraction.metadata.total_tokens == 123
    assert extraction.metadata.duration_ms is not None


def test_extractor_maps_provider_http_errors() -> None:
    settings = LLMSettings(openrouter_api_key="test-key")
    extractor = LLMExtractor(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(DocumentInput(text="hello"))

    assert isinstance(exc_info.value, LLMRequestError)


def test_extractor_maps_provider_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    settings = LLMSettings(openrouter_api_key="test-key")
    extractor = LLMExtractor(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(DocumentInput(text="hello"))

    assert isinstance(exc_info.value, LLMTimeoutError)
