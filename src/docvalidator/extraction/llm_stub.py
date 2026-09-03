"""Recorded LLM responses for credential-free development and evaluation."""

import json
import time
from pathlib import Path
from typing import Any

from docvalidator.domain.models import DocumentExtraction
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput
from docvalidator.extraction.llm import parse_llm_response
from docvalidator.settings import LLMSettings

_RECORDED_DIR = Path(__file__).parents[3] / "fixtures" / "llm_recorded"


class RecordedLLMExtractor(Extractor):
    """Return canned, LLM-shaped responses matched to the golden fixtures."""

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings or LLMSettings()

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        record = self._record_for(document.to_text())
        started_at = time.perf_counter()
        extraction = parse_llm_response(
            record["raw_response"],
            record,
            record.get("model", self.settings.validator_llm_model),
        )
        duration_ms = (time.perf_counter() - started_at) * 1000
        return extraction.model_copy(
            update={
                "metadata": extraction.metadata.model_copy(
                    update={"duration_ms": duration_ms}
                )
            }
        )

    def _record_for(self, text: str) -> dict[str, Any]:
        for path in sorted(_RECORDED_DIR.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if any(marker in text for marker in record["matches"]):
                return record
        raise ValueError("no recorded LLM response matches the supplied document")
