"""Integration tests: LLM-first backend selection and typed error handling (API)."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from docvalidator.api.main import _default_backend, app
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.llm import (
    LLMConfigurationError,
    LLMParsingError,
    LLMRequestError,
    LLMTimeoutError,
)
from docvalidator.extraction.offline import OfflineExtractor

GOLDEN_DIR = Path(__file__).parents[2] / "fixtures" / "golden"
FULL_DOC_TEXT = (GOLDEN_DIR / "t0_en_0.txt").read_text(encoding="utf-8")

client = TestClient(app)


class _ExplodingExtractor:
    """Drop-in LLMExtractor double whose extract raises a canned exception."""

    exc: Exception | None = None

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def extract(self, document: DocumentInput) -> Any:
        assert _ExplodingExtractor.exc is not None
        raise _ExplodingExtractor.exc


def _patch_llm_extract(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Patch the lazily-imported LLMExtractor used inside _build_extractor."""
    _ExplodingExtractor.exc = exc
    monkeypatch.setattr("docvalidator.extraction.llm.LLMExtractor", _ExplodingExtractor)


@pytest.fixture()
def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


class TestBackendSelection:
    def test_default_backend_selects_llm_only_with_api_key(self) -> None:
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            assert _default_backend() == "offline"
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            assert _default_backend() == "auto"

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
        """Explicit llm backend without any key configured -> 503, no fallback."""
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["error"]["code"] == "llm_configuration_error"

    def test_llm_request_error_returns_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm_extract(monkeypatch, LLMRequestError("provider down"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.json()["error"]["code"] == "llm_response_error"

    def test_llm_timeout_returns_504(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm_extract(monkeypatch, LLMTimeoutError("slow"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_504_GATEWAY_TIMEOUT
        assert response.json()["error"]["code"] == "llm_timeout"

    def test_llm_parsing_error_returns_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm_extract(monkeypatch, LLMParsingError("garbage"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.json()["error"]["code"] == "llm_response_error"

    def test_extract_endpoint_llm_failure_is_typed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_llm_extract(monkeypatch, LLMRequestError("provider down"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        response = client.post(
            "/v1/extract",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.json()["error"]["code"] == "llm_response_error"


class TestLLMConfiguration:
    def test_llm_configuration_error_returns_503_without_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit llm backend with a rejected key returns 503, never offline."""
        _patch_llm_extract(monkeypatch, LLMConfigurationError("rejected key"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
        response = client.post(
            "/v1/validate",
            json={"text": FULL_DOC_TEXT, "extraction_backend": "llm"},
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["error"]["code"] == "llm_configuration_error"


class TestOfflineExtractorSanity:
    def test_offline_extractor_reads_golden_fixture(self) -> None:
        """The credential-free backend extracts from the v2 golden fixture."""
        extraction = OfflineExtractor().extract(DocumentInput(text=FULL_DOC_TEXT))
        assert extraction.metadata.backend == "offline"
        values = {k: f.value for k, f in extraction.fields.items()}
        assert any(v is not None for v in values.values())
