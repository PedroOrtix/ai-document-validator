import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docvalidator.api.main import app

client = TestClient(app)
fixture = Path(__file__).parents[2] / "fixtures" / "golden" / "t0_en_0.txt"
invoice_text = fixture.read_text(encoding="utf-8")


def test_structured_logging_validate(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="docvalidator.api.main"):
        response = client.post(
            "/v1/validate",
            json={"text": invoice_text, "config": {}},
        )

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id

    records = [
        r
        for r in caplog.records
        if r.getMessage() == "request completed" and hasattr(r, "log_data")
    ]
    assert len(records) >= 1
    log_data = records[-1].log_data
    assert log_data["request_id"] == request_id
    assert "latency_ms" in log_data
    assert log_data["latency_ms"] >= 0
    assert log_data["backend"] == "ocr"
    assert "model" in log_data
    assert "total_tokens" in log_data
    assert log_data["verdict_status"] == "PASS"


def test_structured_logging_extract(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="docvalidator.api.main"):
        response = client.post(
            "/v1/extract",
            json={"text": invoice_text},
        )

    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id

    records = [
        r
        for r in caplog.records
        if r.getMessage() == "request completed" and hasattr(r, "log_data")
    ]
    assert len(records) >= 1
    log_data = records[-1].log_data
    assert log_data["request_id"] == request_id
    assert "latency_ms" in log_data
    assert log_data["backend"] == "ocr"
    assert "model" in log_data
    assert "total_tokens" in log_data
