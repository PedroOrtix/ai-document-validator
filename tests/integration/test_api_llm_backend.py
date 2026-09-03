from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from docvalidator.api.main import app

client = TestClient(app)
fixture = Path(__file__).parents[2] / "fixtures" / "invoices" / "happy_path_eur.txt"
invoice_text = fixture.read_text(encoding="utf-8")


def test_validate_recorded_llm_backend_returns_llm_metadata() -> None:
    response = client.post(
        "/v1/validate",
        json={"text": invoice_text, "extraction_backend": "llm-recorded"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["extraction"]["metadata"]["backend"] == "llm"
    assert body["extraction"]["metadata"]["model"] == "z-ai/glm-5.3-flash"
    assert body["extraction"]["metadata"]["provider"] == "openrouter"


def test_extract_recorded_llm_backend_returns_llm_metadata() -> None:
    response = client.post(
        "/v1/extract",
        json={"text": invoice_text, "extraction_backend": "llm-recorded"},
    )

    assert response.status_code == 200
    metadata = response.json()["metadata"]
    assert metadata["backend"] == "llm"
    assert metadata["model"] == "z-ai/glm-5.3-flash"


def test_real_llm_backend_without_api_key_returns_503() -> None:
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
        response = client.post(
            "/v1/extract",
            json={"text": invoice_text, "extraction_backend": "llm"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "llm_configuration_error"
    assert body["error"]["details"]["hint"] == (
        "configure OPENROUTER_API_KEY or use the offline backend"
    )
