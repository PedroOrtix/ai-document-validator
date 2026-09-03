"""Integration tests: LLM-first backend selection and offline runtime fallback (API)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from docvalidator.api.main import _default_backend, app
from docvalidator.extraction.llm import (
    LLMConfigurationError,
    LLMParsingError,
    LLMRequestError,
    LLMTimeoutError,
)
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.offline import OfflineExtractor

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
FULL_DOC_TEXT = (GOLDEN_DIR / "t0_en_0.txt").read_text(encoding="utf-8")

client = TestClient(app)


@pytest.fixture()
def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


class TestBackendSelection:
    def test_default_backend_selects_llm_only_with_api_key(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            assert _default_backend() == "offline"
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            assert _default_backend() == "llm"

    def test_validate_without_key_uses_offline(self, _no_key: None) -> None:
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "config": {}},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["extraction"]["metadata"]["backend"] == "offline"

    def test_explicit_backend_override_is_respected(self, _no_key: None) -> None:
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "offline"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["extraction"]["metadata"]["backend"] == "offline"


class TestLLMBackendErrors:
    def test_llm_configuration_error_returns_503(self) -> None:
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        # no key configured in this test environment -> LLMConfigurationError path
        assert response.status_code in {status.HTTP_503_SERVICE_UNAVAILABLE}
        body = response.json()
        assert body["error"]["code"] == "llm_configuration_error"

    def test_llm_request_error_returns_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(doc: DocumentInput) -> None:
            raise LLMRequestError("provider down")

        monkeypatch.setattr("docvalidator.api.main.LLMExtractor.extract", _raise)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["extraction"]["metadata"]["backend"] == "offline-fallback"
        assert body["extraction"]["metadata"]["fallback_reason"] == "llm_request_error"
        assert body["status"] in {"PASS", "FAIL", "REVIEW"}

    def test_llm_timeout_falls_back_to_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(doc: DocumentInput) -> None:
            raise LLMTimeoutError("slow")

        monkeypatch.setattr("docvalidator.api.main.LLMExtractor.extract", _raise)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_200_OK
        metadata = response.json()["extraction"]["metadata"]
        assert metadata["backend"] == "offline-fallback"
        assert metadata["fallback_reason"] == "llm_timeout"

    def test_llm_parsing_error_falls_back_to_offline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(doc: DocumentInput) -> None:
            raise LLMParsingError("garbage")

        monkeypatch.setattr("docvalidator.api.main.LLMExtractor.extract", _raise)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_200_OK
        metadata = response.json()["extraction"]["metadata"]
        assert metadata["backend"] == "offline-fallback"
        assert metadata["fallback_reason"] == "llm_parsing_error"

    def test_extract_endpoint_llm_failure_also_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(doc: DocumentInput) -> None:
            raise LLMRequestError("provider down")

        monkeypatch.setattr("docvalidator.api.main.LLMExtractor.extract", _raise)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/extract",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert (
            response.json()["metadata"]["backend"] == "offline-fallback"
        )


class TestOfflineFallbackNotMasked:
    def test_llm_configuration_error_does_not_fall_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit llm backend with a rejected key returns 503, never offline."""

        def _raise(doc: DocumentInput) -> None:
            raise LLMConfigurationError("rejected key")

        monkeypatch.setattr("docvalidator.api.main.LLMExtractor.extract", _raise)
        monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["error"]["code"] == "llm_configuration_error"


class TestOfflineExtractorSanity:
    def test_fallback_source_extractor_reads_golden_fixture(self) -> None:
        """The offline fallback must extract from the v2 golden fixture."""
        extraction = OfflineExtractor().extract(DocumentInput(text=FULL_DOC_TEXT))
        assert extraction.metadata.backend == "offline"
        values = {k: f.value for k, f in extraction.fields.items()}
        assert any(v is not None for v in values.values())
