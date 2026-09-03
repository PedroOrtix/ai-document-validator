"""Run extraction + rules evaluation over invoice fixtures, with optional LLM comparison lane.

The evaluation is deterministic: every run is anchored to a fixed reference date
(``--as-of``, default 2026-09-03) so golden expectations never rot as wall-clock
time moves. Quality gates (``--min-field-accuracy`` / ``--min-verdict-agreement``)
make the harness usable as a CI regression gate: the command exits non-zero when
a lane drops below its threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
    ValidationConfig,
    Verdict,
)
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
DEFAULT_AS_OF = date(2026, 9, 3)


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


def no_response_extraction() -> DocumentExtraction:
    """Build the extraction used when a lane produced no response at all."""
    return DocumentExtraction(
        fields={name: ExtractedField(value=None, confidence=0.0) for name in FIELD_NAMES},
        metadata=ExtractionMetadata(backend="no-response"),
    )


def run_case(
    case: dict[str, Any],
    extractor: Any,
    engine: RulesEngine,
    config_values: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    """Extract, evaluate, and return serializable results for one fixture."""
    document = DocumentInput(text=case["text"], filename=case["fixture_name"])
    try:
        extraction = extractor.extract(document)
    except Exception:
        extraction = no_response_extraction()
    verdict = engine.evaluate(extraction, config_from_values(config_values), today=today)
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


def build_lane_report(
    cases: list[dict[str, Any]],
    extractor: Any,
    engine: RulesEngine,
    config_values: dict[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    """Run all cases through one extractor and compute that lane's metrics."""
    results = [
        run_case(case, extractor, engine, config_values, today=today) for case in cases
    ]

    field_records: list[tuple[str, Any, Any]] = []
    for result in results:
        for field_name in FIELD_NAMES:
            expected = result["expected_fields"].get(field_name)
            predicted = result["predicted_fields"][field_name]
            field_records.append((field_name, expected, predicted))

    verdict_records = [
        (result["predicted_verdict"], result["expected_verdict"]) for result in results
    ]
    return {
        "fixture_count": len(results),
        "fields": field_metrics(field_records, field_names=FIELD_NAMES),
        "verdict": verdict_metrics(verdict_records),
        "results": results,
    }


def lane_field_accuracy(report: dict[str, Any]) -> float:
    fields = report["fields"]
    total_cells = report["fixture_count"] * len(FIELD_NAMES)
    matches = sum(fields[name]["matches"] for name in FIELD_NAMES)
    return matches / total_cells if total_cells else 0.0


def build_report(
    cases: list[dict[str, Any]],
    *,
    today: date,
    include_llm_lane: bool,
) -> dict[str, Any]:
    """Run all lanes and assemble the complete evaluation report dictionary."""
    engine = RulesEngine()
    config_values = {**ASSESSMENT_CONFIG}
    lanes: dict[str, Any] = {
        "offline": build_lane_report(cases, OfflineExtractor(), engine, config_values, today=today)
    }
    if include_llm_lane:
        from docvalidator.extraction.llm_stub import RecordedLLMExtractor

        lanes["llm_recorded"] = build_lane_report(
            cases, RecordedLLMExtractor(), engine, config_values, today=today
        )
    return {
        "as_of": today.isoformat(),
        "config": config_values,
        "fixture_count": len(cases),
        "lanes": lanes,
    }


def format_value(value: Any) -> str:
    return "<none>" if value is None else str(value)


def print_lane(report: dict[str, Any]) -> None:
    print(f"{'field':<18} {'exact':>8} {'miss-pred':>10} {'precision':>10} {'recall':>8}")
    for field_name in FIELD_NAMES:
        metrics = report["fields"][field_name]
        print(
            f"{field_name:<18} {metrics['exact_match_rate']:>8.2%} "
            f"{metrics['missing_predicted']:>10d} "
            f"{metrics['precision']:>10.2%} {metrics['recall']:>8.2%}"
        )
    verdict = report["verdict"]
    print(
        f"verdict agreement: {verdict['agreement_rate']:.2%} "
        f"({verdict['agreements']}/{verdict['total']})"
    )


def print_report(report: dict[str, Any]) -> None:
    print(f"EVALUATION (as-of {report['as_of']}, {report['fixture_count']} fixtures)")
    for lane_name, lane in report["lanes"].items():
        print(f"\nLANE: {lane_name}")
        print_lane(lane)

    print("\nFAILURES")
    any_failures = False
    for lane_name, lane in report["lanes"].items():
        field_failures = [
            (result, field_name)
            for result in lane["results"]
            for field_name in FIELD_NAMES
            if result["predicted_fields"][field_name] != result["expected_fields"].get(field_name)
        ]
        verdict_failures = [
            result
            for result in lane["results"]
            if result["predicted_verdict"] != result["expected_verdict"]
        ]
        for result, field_name in field_failures:
            any_failures = True
            print(
                f"[{lane_name}][field] {result['fixture_name']} :: {field_name}: "
                f"expected={format_value(result['expected_fields'].get(field_name))!r} "
                f"got={format_value(result['predicted_fields'][field_name])!r} "
                f"evidence={result['field_evidence'][field_name]!r}"
            )
        for result in verdict_failures:
            any_failures = True
            print(
                f"[{lane_name}][verdict] {result['fixture_name']}: "
                f"expected={result['expected_verdict']} got={result['predicted_verdict']}"
            )
            for rule_result in result["rule_results"]:
                status = "PASS" if rule_result["passed"] else "FAIL"
                print(f"  {status:<5} {rule_result['rule_id']}: {rule_result['message']}")
    if not any_failures:
        print("none")

    print("\nOVERALL")
    for lane_name, lane in report["lanes"].items():
        print(
            f"{lane_name:<14} field_accuracy={lane_field_accuracy(lane):.4f} "
            f"verdict_agreement={lane['verdict']['agreement_rate']:.4f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        help="write the full metrics dictionary to this JSON file",
    )
    parser.add_argument("--fixtures-dir", type=Path, default=Path("fixtures/invoices"))
    parser.add_argument(
        "--as-of",
        type=str,
        default=DEFAULT_AS_OF.isoformat(),
        help="reference date for age rules, ISO YYYY-MM-DD (default keeps eval deterministic)",
    )
    parser.add_argument(
        "--no-llm-lane",
        action="store_true",
        help="skip the recorded-LLM comparison lane",
    )
    parser.add_argument(
        "--min-field-accuracy",
        type=float,
        default=None,
        help="exit non-zero when the offline lane's field accuracy falls below this",
    )
    parser.add_argument(
        "--min-verdict-agreement",
        type=float,
        default=None,
        help="exit non-zero when the offline lane's verdict agreement falls below this",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        today = date.fromisoformat(args.as_of)
    except ValueError as exc:
        raise SystemExit(f"invalid --as-of (expected YYYY-MM-DD): {args.as_of}") from exc

    cases = load_cases(args.fixtures_dir)
    report = build_report(cases, today=today, include_llm_lane=not args.no_llm_lane)
    print_report(report)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    offline = report["lanes"]["offline"]
    failed_gates: list[str] = []
    if args.min_field_accuracy is not None:
        accuracy = lane_field_accuracy(offline)
        if accuracy < args.min_field_accuracy:
            failed_gates.append(
                f"field_accuracy {accuracy:.4f} < {args.min_field_accuracy:.4f}"
            )
    if args.min_verdict_agreement is not None:
        agreement = offline["verdict"]["agreement_rate"]
        if agreement < args.min_verdict_agreement:
            failed_gates.append(
                f"verdict_agreement {agreement:.4f} < {args.min_verdict_agreement:.4f}"
            )
    if failed_gates:
        print(f"\nGATE FAILED: {'; '.join(failed_gates)}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()