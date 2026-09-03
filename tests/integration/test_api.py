import json
from base64 import b64encode
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from docvalidator.api.main import app

client = TestClient(app)
fixture = Path(__file__).parents[2] / "fixtures" / "golden" / "t0_en_0.txt"
invoice_text = fixture.read_text(encoding="utf-8")


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validate_json_text_returns_pass_verdict() -> None:
    response = client.post(
        "/v1/validate",
        json={"text": invoice_text, "config": {}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASS"
    assert body["extraction"]["fields"]["supplier_name"]["value"] == "Oakbridge Logistics Ltd"
    assert body["request_id"]


def test_validate_multipart_txt_returns_pass_verdict() -> None:
    response = client.post(
        "/v1/validate",
        files={"file": ("t0_en_0.txt", invoice_text.encode(), "text/plain")},
        data={"config": json.dumps({"max_age_days": 90})},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASS"
    assert body["extraction"]["metadata"]["backend"] == "offline"


def test_invalid_config_returns_422() -> None:
    response = client.post(
        "/v1/validate",
        json={"text": invoice_text, "config": {"max_age_days": 0}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_garbage_text_returns_review_without_server_error() -> None:
    response = client.post("/v1/validate", json={"text": "??? !!! random noise"})

    assert response.status_code == 200
    assert response.json()["status"] in {"REVIEW", "FAIL"}


def test_request_id_is_echoed_and_generated() -> None:
    supplied = "request-id-123"
    supplied_response = client.get("/health", headers={"X-Request-ID": supplied})
    generated_response = client.get("/health")

    assert supplied_response.headers["X-Request-ID"] == supplied
    generated_request_id = generated_response.headers["X-Request-ID"]
    assert len(generated_request_id) == 36
    assert generated_request_id != supplied


def test_extract_returns_document_extraction_only() -> None:
    response = client.post("/v1/extract", json={"text": invoice_text})

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["backend"] == "offline"
    assert set(body["fields"]) == {
        "supplier_name",
        "invoice_number",
        "invoice_date",
        "total_amount",
        "currency",
        "tax_id",
    }
    assert "request_id" not in body


def test_reject_json_with_both_content_sources() -> None:
    response = client.post(
        "/v1/validate",
        json={"text": "hello", "content_b64": b64encode(b"hello").decode()},
    )

    assert response.status_code == 422


@pytest.mark.skip(reason="LLM backend behavior is owned by another workstream")
def test_llm_backend_without_api_key_returns_503() -> None:
    with patch("docvalidator.api.main._llm_api_key", return_value=""):
        response = client.post(
            "/v1/extract", json={"text": invoice_text, "extraction_backend": "llm"}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_configuration_error"
