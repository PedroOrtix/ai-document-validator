"""Contract tests for the consolidated golden v2 dataset."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from docvalidator.domain.models import ValidationConfig
from docvalidator.extraction import DocumentInput, ExtractionError
from docvalidator.extraction.ocr import OcrExtractor
from docvalidator.rules_engine import RulesEngine

ROOT = Path(__file__).parents[2]
GOLDEN = ROOT / "fixtures" / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
TXT_MANIFEST = json.loads((GOLDEN / "manifest_txt.json").read_text(encoding="utf-8"))
PDF_MANIFEST = json.loads((GOLDEN / "manifest_pdf.json").read_text(encoding="utf-8"))
SCANNED_MANIFEST = json.loads((GOLDEN / "manifest_scanned.json").read_text(encoding="utf-8"))
AS_OF = date.fromisoformat(MANIFEST["as_of"])
CONFIG = ValidationConfig(
    max_age_days=MANIFEST["max_age_days"],
    allowed_currencies=MANIFEST["allowed_currencies"],
)

EXPECTED_COUNTS = {"txt": 43, "pdf": 23, "scanned": 12}
EXPECTED_TIERS = {
    "txt": {0: 14, 1: 16, 2: 13},
    "pdf": {0: 7, 1: 9, 2: 7},
}
EXPECTED_VERDICTS = {
    "txt": {"PASS": 27, "REVIEW": 6, "FAIL": 10},
    "pdf": {"PASS": 15, "REVIEW": 4, "FAIL": 4},
}


def _expected_file(case_id: str) -> dict:
    return json.loads((GOLDEN / f"{case_id}.expected.json").read_text(encoding="utf-8"))


def _all_cases() -> list[dict]:
    return MANIFEST["txt_cases"] + MANIFEST["pdf_cases"] + MANIFEST["scanned_cases"]


def test_lane_fragments_match_merged_manifest() -> None:
    assert TXT_MANIFEST["cases"] == MANIFEST["txt_cases"]
    assert PDF_MANIFEST["cases"] == MANIFEST["pdf_cases"]
    assert SCANNED_MANIFEST["cases"] == MANIFEST["scanned_cases"]


def test_manifest_v2_header_and_counts() -> None:
    assert MANIFEST["generator"] == "fixtures.generator v2"
    assert MANIFEST["as_of"] == "2026-09-03"
    assert MANIFEST["max_age_days"] == 90
    assert MANIFEST["allowed_currencies"] == ["EUR", "GBP"]
    assert MANIFEST["counts"] == EXPECTED_COUNTS
    assert len(MANIFEST["txt_cases"]) == 43
    assert len(MANIFEST["pdf_cases"]) == 23
    assert len(MANIFEST["scanned_cases"]) == 12


@pytest.mark.parametrize("lane", ["txt", "pdf"])
def test_tier_distributions(lane: str) -> None:
    cases = MANIFEST[f"{lane}_cases"]
    counts = {tier: 0 for tier in (0, 1, 2)}
    for case in cases:
        counts[case["tier"]] += 1
    assert counts == EXPECTED_TIERS[lane]

    languages = {language: 0 for language in ("EN", "ES")}
    for case in cases:
        languages[case["language"]] += 1
    expected_languages = {"EN": EXPECTED_COUNTS[lane] // 2 + 1, "ES": EXPECTED_COUNTS[lane] // 2}
    assert languages == expected_languages


@pytest.mark.parametrize("lane", ["txt", "pdf"])
def test_verdict_distributions(lane: str) -> None:
    verdicts = {verdict: 0 for verdict in ("PASS", "REVIEW", "FAIL")}
    for case in MANIFEST[f"{lane}_cases"]:
        verdicts[case["expected_verdict"]] += 1
    assert verdicts == EXPECTED_VERDICTS[lane]


@pytest.mark.parametrize("case", _all_cases(), ids=lambda case: case["case_id"])
def test_expected_schema_and_independently_recomputed_verdict(case: dict) -> None:
    expected = _expected_file(case["case_id"])
    assert set(expected) == {"expected_fields", "expected_verdict_status", "slices"}
    assert set(expected["expected_fields"]) == {
        "supplier_name", "invoice_number", "invoice_date", "total_amount", "currency", "tax_id",
    }
    slices = expected["slices"]
    assert slices["language"] == case["language"]
    assert slices["tier"] == case["tier"]
    assert slices["scenario"] == case["scenario"]
    assert slices["format"] == case["formats"][0]

    fields = expected["expected_fields"]
    missing_required = any(
        fields[field_name] is None
        for field_name in ("invoice_number", "invoice_date", "total_amount")
    )
    if fields["invoice_date"] is not None:
        invoice_date = date.fromisoformat(str(fields["invoice_date"]))
        stale = (AS_OF - invoice_date).days > MANIFEST["max_age_days"]
    else:
        stale = False
    if stale or (fields["total_amount"] is not None and fields["total_amount"] <= 0) or (
        fields["currency"] is not None and fields["currency"] not in MANIFEST["allowed_currencies"]
    ):
        recomputed = "FAIL"
    elif missing_required:
        recomputed = "REVIEW"
    else:
        recomputed = "PASS"
    assert expected["expected_verdict_status"] == recomputed
    assert case["expected_verdict"] == recomputed


@pytest.mark.parametrize("case", _all_cases(), ids=lambda case: case["case_id"])
def test_manifest_hash_matches_disk(case: dict) -> None:
    artifact_suffix = "pdf" if case["formats"][0] == "scanned" else case["formats"][0]
    artifact = GOLDEN / f"{case['case_id']}.{artifact_suffix}"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert digest == case[f"{artifact_suffix}_sha256"]


def test_no_orphan_golden_files() -> None:
    expected_names = {
        "manifest.json",
        "manifest_txt.json",
        "manifest_pdf.json",
        "manifest_scanned.json",
    }
    for case in _all_cases():
        artifact_suffix = "pdf" if case["formats"][0] == "scanned" else case["formats"][0]
        expected_names.update(
            {f"{case['case_id']}.{artifact_suffix}", f"{case['case_id']}.expected.json"}
        )
    assert {path.name for path in GOLDEN.iterdir()} == expected_names


@pytest.mark.parametrize(
    "case_id",
    [
        f"t0_{language}_{index}"
        for language in ("en", "es")
        for index in range(6)
    ],
)
def test_v2_tier0_txt_extracts_and_passes(case_id: str) -> None:
    expected = _expected_file(case_id)
    expected_fields = dict(expected["expected_fields"])
    expected_fields["invoice_date"] = (
        date.fromisoformat(str(expected_fields["invoice_date"]))
        if expected_fields["invoice_date"] is not None
        else None
    )
    extraction = OcrExtractor().extract(
        DocumentInput(text=(GOLDEN / f"{case_id}.txt").read_text(encoding="utf-8"))
    )
    for field_name, field_value in expected_fields.items():
        assert extraction.fields[field_name].value == field_value, f"{case_id}: {field_name}"
    verdict = RulesEngine().evaluate(extraction, CONFIG, today=AS_OF)
    assert verdict.status == expected["expected_verdict_status"]


@pytest.mark.parametrize("case_id", ["x_txt_empty", "x_txt_garbage"])
def test_degenerate_txt_has_no_extractable_fields(case_id: str) -> None:
    expected = _expected_file(case_id)
    extraction = OcrExtractor().extract(
        DocumentInput(text=(GOLDEN / f"{case_id}.txt").read_text(encoding="utf-8"))
    )
    if case_id.endswith("garbage"):
        assert extraction.fields["supplier_name"].value is not None
    else:
        assert all(field.value is None for field in extraction.fields.values())
    assert expected["expected_fields"] == {name: None for name in extraction.fields}
    assert expected["expected_verdict_status"] == "REVIEW"


def test_txt_no_vat_extracts_optional_tax_id_as_absent() -> None:
    expected = _expected_file("x_txt_no_vat")
    extraction = OcrExtractor().extract(
        DocumentInput(text=(GOLDEN / "x_txt_no_vat.txt").read_text(encoding="utf-8"))
    )
    assert extraction.fields["tax_id"].value is None
    assert expected["expected_fields"]["tax_id"] is None
    assert expected["expected_verdict_status"] == "PASS"


def test_scanned_lane_fragment_matches_merged_manifest() -> None:
    assert SCANNED_MANIFEST["lane"] == "scanned"
    assert SCANNED_MANIFEST["cases"] == MANIFEST["scanned_cases"]


def test_scanned_manifest_counts_and_distributions() -> None:
    cases = MANIFEST["scanned_cases"]
    assert MANIFEST["counts"]["scanned"] == 12
    assert len(cases) == 12
    assert {tier: sum(case["tier"] == tier for case in cases) for tier in (0, 1, 2)} == {
        0: 4,
        1: 4,
        2: 4,
    }
    language_counts = {
        language: sum(case["language"] == language for case in cases)
        for language in ("EN", "ES")
    }
    assert language_counts == {"EN": 6, "ES": 6}
    assert all(case["formats"] == ["scanned"] for case in cases)
    assert all(case["pages"] == 1 for case in cases)
    assert all(case["case_id"].startswith("scan_") for case in cases)


def test_scanned_truth_matches_pdf_twins_and_slices() -> None:
    for case in MANIFEST["scanned_cases"]:
        underlying_id = case["case_id"].removeprefix("scan_")
        twin = _expected_file(underlying_id)
        expected = _expected_file(case["case_id"])
        assert expected["expected_fields"] == twin["expected_fields"]
        assert expected["expected_verdict_status"] == twin["expected_verdict_status"]
        normalized_twin = twin["slices"] | {"format": "scanned", "degradation": "scan_v1"}
        assert expected["slices"] == normalized_twin
        assert expected["slices"]["degradation"] == "scan_v1"


@pytest.mark.parametrize("case", MANIFEST["scanned_cases"], ids=lambda case: case["case_id"])
def test_scanned_pdf_is_image_only(case: dict) -> None:
    pdf_bytes = (GOLDEN / f"{case['case_id']}.pdf").read_bytes()
    with pytest.raises(ExtractionError, match="no extractable text layer"):
        DocumentInput(pdf_bytes=pdf_bytes).to_text()


@pytest.mark.parametrize("case", MANIFEST["scanned_cases"], ids=lambda case: case["case_id"])
def test_scanned_manifest_hash_matches_disk(case: dict) -> None:
    digest = hashlib.sha256((GOLDEN / f"{case['case_id']}.pdf").read_bytes()).hexdigest()
    assert digest == case["pdf_sha256"]
