"""Shared test fixtures.

LLM-backed integration tests must never depend on (or leak into) the host
environment: a developer running the suite with OPENROUTER_API_KEY exported
would otherwise hit the live provider from the test suite. Every test starts
from a clean no-key environment; tests that need a key set it explicitly via
monkeypatch.setenv.
"""

import pytest


@pytest.fixture(autouse=True)
def _hermetic_openrouter_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
