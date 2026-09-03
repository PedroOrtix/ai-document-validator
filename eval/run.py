"""Run the offline extraction and rules evaluation over invoice fixtures."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from docvalidator.domain.models import DocumentExtraction, ValidationConfig, Verdict
from docvalidator.extraction import DocumentInput, OfflineExtractor
from docvalidator.rules_engine import RulesEngine

from .metrics import field_metrics, verdict_metrics

FIELD_NAMES = (
    "supplier_name",
    "invoice_number",
    "invoice_date",
    "total_amount",
    "currency",
    "tax_id",
)
ASSESSMENT_CONFIG = {"max_age_days": 90, "allowed_currencies": ["EUR", "GBP"]}


def load_cases(fixtures_dir: Path) -> list[dict[str, Any]]:
    """Load every invoice fixture and its sibling expectation file."""
    cases: list[dict[str, Any]] = []
    for fixture_path in sorted(fixtures_dir.glob("*.txt")):
        expectation_path = fixture_path.with_suffix(".expected.json")
        cases.append(
            {
                "fixture_name": fixture_path.name,
                "text": fixture_path.read_text(encoding="utf-8"),
                "expectation": json.loads(expectation_path.read_text(encoding="utf-8")),
            }
        )
    return cases


def run_case(
    case: dict[str, Any],
    extractor: OfflineExtractor,
    engine: RulesEngine,
    config_values: dict[str, Any],
) -> dict[str, Any]:
    """Extract, evaluate, and return serializable results for one fixture."""
    extraction = extractor.extract(DocumentInput(text=case["text"], filename=case["fixture_name"]))
    verdict = engine.evaluate(extraction, config_from_values(config_values))
    return serialize_case(case["fixture_name"], case["expectation"], extraction, verdict)


def config_from_values(config_values: dict[str, Any]) -> ValidationConfig:
    return ValidationConfig(**config_values)


def field_value(extraction: DocumentExtraction, field_name: str) -> Any:
    field = extraction.get_field(field_name)
    if field is None or field.value is None:
        return None
    if isinstance(field.value, date):
        return field.value.isoformat()
    return field.value


def field_evidence(extraction: DocumentExtraction, field_name: str) -> str:
    field = extraction.get_field(field_name)
    return "" if field is None or field.evidence is None else field.evidence


def serialize_case(
    fixture_name: str,
    expectation: dict[str, Any],
    extraction: DocumentExtraction,
    verdict: Verdict,
) -> dict[str, Any]:
    expected_fields = expectation["expected_fields"]
    return {
        "fixture_name": fixture_name,
        "expected_verdict": expectation["expected_verdict_status"],
        "predicted_verdict": verdict.status,
        "expected_fields": expected_fields,
        "predicted_fields": {name: field_value(extraction, name) for name in FIELD_NAMES},
        "field_evidence": {name: field_evidence(extraction, name) for name in FIELD_NAMES},
        "rule_results": [
            {"rule_id": result.rule_id, "passed": result.passed, "message": result.message}
            for result in verdict.rule_results
        ],
    }


def build_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run all cases and assemble the complete evaluation report dictionary."""
    extractor = OfflineExtractor()
    engine = RulesEngine()
    config_values = {**ASSESSMENT_CONFIG}
    results = [run_case(case, extractor, engine, config_values) for case in cases]

    field_records: list[tuple[str, Any, Any]] = []
    for result in results:
        for field_name in FIELD_NAMES:
            expected = result["expected_fields"].get(field_name)
            predicted = result["predicted_fields"][field_name]
            field_records.append((field_name, expected, predicted))

    verdict_records = [
        (result["predicted_verdict"], result["expected_verdict"])
        for result in results
    ]
    return {
        "config": config_values,
        "fixture_count": len(results),
        "fields": field_metrics(field_records, field_names=FIELD_NAMES),
        "verdict": verdict_metrics(verdict_records),
        "results": results,
    }


def format_value(value: Any) -> str:
    return "<none>" if value is None else str(value)


def print_report(report: dict[str, Any]) -> None:
    field_accuracy = sum(report["fields"][name]["matches"] for name in FIELD_NAMES) / (
        len(FIELD_NAMES) * report["fixture_count"]
    )
    print("EVALUATION FIELDS")
    print(f"{'field':<18} {'exact':>8} {'miss-pred':>10} {'precision':>10} {'recall':>8}")
    for field_name in FIELD_NAMES:
        metrics = report["fields"][field_name]
        print(
            f"{field_name:<18} {metrics['exact_match_rate']:>8.2%} "
            f"{metrics['missing_predicted']:>10d} "
            f"{metrics['precision']:>10.2%} {metrics['recall']:>8.2%}"
        )

    print("\nVERDICT AGREEMENT")
    verdict = report["verdict"]
    print(f"{verdict['agreement_rate']:.2%} ({verdict['agreements']}/{verdict['total']})")

    field_failures = [
        (result, field_name)
        for result in report["results"]
        for field_name in FIELD_NAMES
        if result["predicted_fields"][field_name] != result["expected_fields"].get(field_name)
    ]
    verdict_failures = [
        result
        for result in report["results"]
        if result["predicted_verdict"] != result["expected_verdict"]
    ]

    print("\nFAILURES")
    if not field_failures and not verdict_failures:
        print("none")

    for result, field_name in field_failures:
        print(
            f"[field] {result['fixture_name']} :: {field_name}: "
            f"expected={format_value(result['expected_fields'].get(field_name))!r} "
            f"got={format_value(result['predicted_fields'][field_name])!r} "
            f"evidence={result['field_evidence'][field_name]!r}"
        )
    for result in verdict_failures:
        print(
            f"[verdict] {result['fixture_name']}: "
            f"expected={result['expected_verdict']} got={result['predicted_verdict']}"
        )
        for rule_result in result["rule_results"]:
            status = "PASS" if rule_result["passed"] else "FAIL"
            print(f"  {status:<5} {rule_result['rule_id']}: {rule_result['message']}")

    verdict_agreement_rate = report["verdict"]["agreement_rate"]
    print(
        f"\nOVERALL: field_accuracy={field_accuracy:.2f} "
        f"verdict_agreement={verdict_agreement_rate:.2f} ({report['fixture_count']} fixtures)"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        help="write the full metrics dictionary to this JSON file",
    )
    parser.add_argument("--fixtures-dir", type=Path, default=Path("fixtures/invoices"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(load_cases(args.fixtures_dir))
    print_report(report)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
