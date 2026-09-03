"""Unit tests for the LangChain structured-output LLM parsing path."""

from datetime import date
from typing import Any

import pytest

from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.llm import (
    LLMParsingError,
    InvoiceExtraction,
    parse_llm_response,
    parse_structured_extraction,
)
from docvalidator.settings import LLMSettings

_VALID_PAYLOAD: dict[str, Any] = {
    "supplier_name": "ACME Ltd",
    "invoice_number": "INV-1",
    "invoice_date": "2026-01-31",
    "total_amount": 123.45,
    "currency": "EUR",
    "tax_id": "DE123456789",
}


class TestInvoiceExtractionModel:
    def test_valid_payload_round_trip(self) -> None:
        parsed = InvoiceExtraction.model_validate(_VALID_PAYLOAD)
        assert parsed.invoice_date == date(2026, 1, 31)
        assert parsed.total_amount == 123.45

    def test_all_fields_optional_none(self) -> None:
        parsed = InvoiceExtraction.model_validate({name: None for name in _VALID_PAYLOAD})
        assert parsed.supplier_name is None

    def test_invalid_date_raises_validation_error(self) -> None:
        payload = {**_VALID_PAYLOAD, "invoice_date": "31/01/2026"}
        with pytest.raises(Exception, match="invoice_date"):
            InvoiceExtraction.model_validate(payload)


class TestParseStructuredExtraction:
    def test_happy_path_produces_canonical_fields(self) -> None:
        parsed = InvoiceExtraction.model_validate(_VALID_PAYLOAD)
        extraction = parse_structured_extraction(parsed, None, "m")
        assert extraction.fields["supplier_name"].value == "ACME Ltd"
        assert extraction.fields["invoice_date"].value == date(2026, 1, 31)
        assert extraction.metadata.backend == "llm"
        assert extraction.metadata.provider == "openrouter"
        assert extraction.metadata.model == "m"
        assert all(f.confidence == 0.75 for f in extraction.fields.values())

    def test_dict_payload_with_exact_fields_is_accepted(self) -> None:
        extraction = parse_structured_extraction(_VALID_PAYLOAD, None, "m")
        assert extraction.fields["total_amount"].value == 123.45

    def test_missing_field_raises_parsing_error(self) -> None:
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "tax_id"}
        with pytest.raises(LLMParsingError):
            parse_structured_extraction(payload, None, "m")

    def test_extra_field_raises_parsing_error(self) -> None:
        payload = {**_VALID_PAYLOAD, "po_number": "PO-9"}
        with pytest.raises(LLMParsingError):
            parse_structured_extraction(payload, None, "m")

    def test_invalid_value_type_raises_parsing_error(self) -> None:
        payload = {**_VALID_PAYLOAD, "total_amount": "not-a-number"}
        with pytest.raises(LLMParsingError):
            parse_structured_extraction(payload, None, "m")


class TestParseLlmResponse:
    def test_raw_json_is_parsed_defensively(self) -> None:
        extraction = parse_llm_response(
            '{"supplier_name":"A","invoice_number":"I","invoice_date":"2026-01-02",'
            '"total_amount":1.0,"currency":"EUR","tax_id":null}',
            {"usage": {"total_tokens": 42}},
            "m",
        )
        assert extraction.fields["tax_id"].value is None
        assert extraction.metadata.total_tokens == 42

    def test_fenced_json_is_unwrapped(self) -> None:
        extraction = parse_llm_response(
            '```json\n{"supplier_name":null,"invoice_number":null,"invoice_date":null,'
            '"total_amount":null,"currency":null,"tax_id":null}\n```',
            {},
            "m",
        )
        assert extraction.fields["supplier_name"].value is None

    def test_non_json_raises(self) -> None:
        with pytest.raises(LLMParsingError):
            parse_llm_response("the answer is 42", {}, "m")


class TestSettings:
    def test_defaults(self) -> None:
        settings = LLMSettings()
        assert settings.validator_llm_model == "z-ai/glm-5.3-flash"
        assert settings.validator_llm_reasoning_effort == "low"
        assert settings.validator_llm_timeout_seconds == 30.0


class TestReasoningEffortWiring:
    def _install_factory(
        self, monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
    ) -> None:
        class FakeChatOpenAI:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

            def with_structured_output(self, *args: object, **kwargs: object) -> Any:
                return _StructuredStub(InvoiceExtraction.model_validate(_VALID_PAYLOAD))

        monkeypatch.setattr(
            "langchain_openai.chat_models.base.ChatOpenAI", FakeChatOpenAI
        )

    def test_reasoning_effort_passed_as_extra_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from docvalidator.extraction.llm import LLMExtractor

        captured: dict[str, object] = {}
        self._install_factory(monkeypatch, captured)
        extractor = LLMExtractor(
            LLMSettings(openrouter_api_key="k", validator_llm_reasoning_effort="low")
        )
        extraction = extractor.extract(DocumentInput(text="ACME"))
        assert extraction.fields["supplier_name"].value == "ACME Ltd"
        assert captured.get("extra_body") == {"reasoning": {"effort": "low"}}

    def test_reasoning_effort_omitted_when_setting_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from docvalidator.extraction.llm import LLMExtractor

        captured: dict[str, object] = {}
        self._install_factory(monkeypatch, captured)
        extractor = LLMExtractor(
            LLMSettings(openrouter_api_key="k", validator_llm_reasoning_effort="")
        )
        extractor.extract(DocumentInput(text="ACME"))
        assert "extra_body" not in captured


class _StructuredStub:
    """Fake structured-output chain returning a canned InvoiceExtraction."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def invoke(self, messages: Any) -> Any:
        return {"parsed": self._result, "raw": None}
