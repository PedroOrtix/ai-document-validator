"""Contract tests driven by the frozen golden manifest (fixtures/golden)."""

import json
from datetime import date
from pathlib import Path

import pytest

from docvalidator.domain.models import ValidationConfig
from docvalidator.extraction import DocumentInput, OfflineExtractor
from docvalidator.rules_engine import RulesEngine

GOLDEN = Path(__file__).parents[2] / "fixtures" / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
AS_OF = date.fromisoformat(MANIFEST["as_of"])
CONFIG = ValidationConfig(
    max_age_days=MANIFEST["max_age_days"],
    allowed_currencies=MANIFEST["allowed_currencies"],
)

# Fully-passing canonical cases per language: the offline extractor must keep
# a perfect score on tier-0 documents (regression contract). FR/IT t0 have
# known misses at this baseline; they live in the eval harness, not here.
CONTRACT_CASES = (
    "t0_en_0", "t0_en_1", "t0_en_2",
    "t0_es_0", "t0_es_1", "t0_es_2",
    "t0_de_0", "t0_de_1", "t0_de_2",
)

# Forced tier-3 scenarios with an unambiguous expected outcome by design.
SCENARIO_EXPECTATIONS = {
    "t3_stale_just_over": "FAIL",
    "t3_stale_old": "FAIL",
    "t3_future_date": "PASS",
    "t3_zero_amount": "FAIL",
    "t3_negative_amount": "FAIL",
    "t3_missing_number": "REVIEW",
    "t3_missing_date": "REVIEW",
    "t3_missing_total": "REVIEW",
    "t3_disallowed_currency": "FAIL",
    "t3_all_present_pass": "PASS",
    "t3_everything_ok_eur": "PASS",
}


def _load(case_id: str) -> tuple[str, dict]:
    text = (GOLDEN / f"{case_id}.txt").read_text(encoding="utf-8")
    expected = json.loads((GOLDEN / f"{case_id}.expected.json").read_text(encoding="utf-8"))
    raw_fields = expected["expected_fields"]
    if raw_fields.get("invoice_date") is not None:
        raw_fields["invoice_date"] = date.fromisoformat(str(raw_fields["invoice_date"]))
    return text, expected


@pytest.mark.parametrize("case_id", CONTRACT_CASES)
def test_tier0_canonical_cases_extract_and_pass(case_id: str) -> None:
    """Contract: canonical invoices in EN/ES/DE extract fully and validate PASS."""
    text, expected = _load(case_id)
    extraction = OfflineExtractor().extract(DocumentInput(text=text))
    engine = RulesEngine()
    verdict = engine.evaluate(extraction, CONFIG, today=AS_OF)
    for field_name, expected_value in expected["expected_fields"].items():
        assert extraction.fields[field_name].value == expected_value, (
            f"{case_id}: field {field_name}"
        )
    assert verdict.status == "PASS"


@pytest.mark.parametrize("case_id", sorted(SCENARIO_EXPECTATIONS))
def test_forced_scenarios_produce_expected_verdict(case_id: str) -> None:
    """Contract: the engine's verdict semantics on isolated rule scenarios."""
    text, expected = _load(case_id)
    extraction = OfflineExtractor().extract(DocumentInput(text=text))
    verdict = RulesEngine().evaluate(extraction, CONFIG, today=AS_OF)
    assert verdict.status == SCENARIO_EXPECTATIONS[case_id], (
        f"{case_id}: engine said {verdict.status}"
    )


def test_manifest_counts_are_stable() -> None:
    """The frozen dataset shape must not drift silently."""
    assert MANIFEST["counts"] == {"txt": 58, "pdf": 12}
    assert len(MANIFEST["txt_cases"]) == 58
    assert len(MANIFEST["pdf_cases"]) == 12


def test_multipage_pdf_carries_all_fields_across_pages() -> None:
    pdf_path = GOLDEN / "pdf_multipage_a.pdf"
    extraction = OfflineExtractor().extract(
        DocumentInput(pdf_bytes=pdf_path.read_bytes(), filename="pdf_multipage_a.pdf")
    )
    expected = json.loads(
        (GOLDEN / "pdf_multipage_a.expected.json").read_text(encoding="utf-8")
    )
    for field_name, expected_value in expected["expected_fields"].items():
        assert extraction.fields[field_name].value == expected_value, (
            f"multipage field {field_name}"
        )