"""Unit tests for the LangChain structured-output LLM parsing path."""

from datetime import date
from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage

from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.llm import (
    InvoiceExtraction,
    LLMExtractor,
    LLMParsingError,
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

    def test_absent_fields_carry_presence_based_confidence(self) -> None:
        null_payload = {name: None for name in _VALID_PAYLOAD}
        null_extraction = parse_structured_extraction(null_payload, None, "m")
        assert {f.confidence for f in null_extraction.fields.values()} == {0.6}

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


class TestStructuredOutputContract:
    def test_output_parser_error_is_typed_and_not_retried(self) -> None:
        class _FailingChain:
            def invoke(self, messages: Any) -> Any:
                raise OutputParserException("Failed to parse")

        class _FailingModel:
            def with_structured_output(self, *args: object, **kwargs: object) -> _FailingChain:
                return _FailingChain()

        extractor = LLMExtractor(
            LLMSettings(openrouter_api_key="k"),
            model=_FailingModel(),
        )

        with pytest.raises(LLMParsingError, match="invalid field values"):
            extractor.extract(DocumentInput(text="ACME"))

    def test_null_parsed_response_raises_parsing_error(self) -> None:
        raw = AIMessage(
            content="",
            usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 42},
        )

        class _NullChain:
            def invoke(self, messages: Any) -> Any:
                return {"parsed": None, "raw": raw}

        class _NullModel:
            def with_structured_output(self, *args: object, **kwargs: object) -> _NullChain:
                return _NullChain()

        extractor = LLMExtractor(
            LLMSettings(openrouter_api_key="k"),
            model=_NullModel(),
        )

        with pytest.raises(LLMParsingError, match="unparseable"):
            extractor.extract(DocumentInput(text="ACME"))


class _StructuredStub:
    """Fake structured-output chain returning a canned InvoiceExtraction."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def invoke(self, messages: Any) -> Any:
        return {"parsed": self._result, "raw": None}
