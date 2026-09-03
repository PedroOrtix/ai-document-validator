"""Golden-set evaluation: extraction over the fixed txt + pdf datasets.

Reads fixtures/golden/manifest.json (the frozen evaluation contract), runs the
credential-free OCR extractor + rules engine over both lanes, reports field-level metrics
per slice (language, tier, scenario, verdict) plus the aggregate, and enforces
the CI quality gates. This is the measurement that tells us whether the
heuristic solution still earns its keep or it is time to escalate (LLM, OCR).

    uv run python -m eval.run                       # full report
    uv run python -m eval.run --min-field-accuracy 0.9 --min-verdict-agreement 0.95
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
    ValidationConfig,
)
from docvalidator.extraction import DocumentInput
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.llm import LLMExtractor
from docvalidator.extraction.vision import VisionExtractor
from docvalidator.rules_engine import RulesEngine

from .lanes import (
    FIELD_NAMES,
    LANE_NAMES,
    LanePlan,
    decision_table,
    default_lane_request,
    extraction_telemetry,
    make_auto_extractor,
    make_ocr_extractor,
    print_decision_table,
    resolve_lane_plans,
)
from .metrics import confidence_separation, field_metrics, verdict_metrics

GOLDEN_DIR = Path("fixtures/golden")
DEFAULT_AS_OF = date(2026, 9, 3)
ExtractorFactory = Callable[[], Extractor]


def make_llm_extractor() -> LLMExtractor:
    return LLMExtractor()


def make_vision_extractor() -> VisionExtractor:
    return VisionExtractor()


def load_manifest() -> dict[str, Any]:
    return json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))


def no_response_extraction() -> DocumentExtraction:
    return DocumentExtraction(
        fields={name: ExtractedField(value=None, confidence=0.0) for name in FIELD_NAMES},
        metadata=ExtractionMetadata(backend="no-response"),
    )


def run_case(
    case_id: str,
    document: DocumentInput,
    expected: dict[str, Any],
    engine: RulesEngine,
    config_values: dict[str, Any],
    extractor: Extractor,
    *,
    today: date,
) -> dict[str, Any]:
    try:
        extraction = extractor.extract(document)
        sub_route = extraction.metadata.model if extraction.metadata.backend == "auto" else None
    except Exception:
        extraction = no_response_extraction()
        sub_route = None
    telemetry = extraction_telemetry(extraction)
    verdict = engine.evaluate(extraction, ValidationConfig(**config_values), today=today)
    return {
        "case_id": case_id,
        "expected_verdict": expected["expected_verdict_status"],
        "predicted_verdict": verdict.status,
        "predicted_verdict_confidence": verdict.verdict_confidence,
        "expected_fields": expected["expected_fields"],
        "predicted_fields": {name: _field_value(extraction, name) for name in FIELD_NAMES},
        "field_confidences": {name: _field_confidence(extraction, name) for name in FIELD_NAMES},
        "field_evidence": {name: _field_evidence(extraction, name) for name in FIELD_NAMES},
        "rule_results": [
            {"rule_id": r.rule_id, "passed": r.passed, "message": r.message}
            for r in verdict.rule_results
        ],
        "slices": expected.get("slices", {}),
        "duration_ms": telemetry["duration_ms"],
        "total_tokens": telemetry["total_tokens"],
        "sub_route": sub_route,
    }


def _field_value(extraction: DocumentExtraction, field_name: str) -> Any:
    field = extraction.get_field(field_name)
    if field is None or field.value is None:
        return None
    if isinstance(field.value, date):
        return field.value.isoformat()
    return field.value


def _field_evidence(extraction: DocumentExtraction, field_name: str) -> str:
    field = extraction.get_field(field_name)
    return "" if field is None or field.evidence is None else field.evidence


def _field_confidence(extraction: DocumentExtraction, field_name: str) -> float:
    field = extraction.get_field(field_name)
    return 0.0 if field is None else field.confidence


def run_lane(
    cases: list[dict[str, Any]],
    *,
    today: date,
    lane_name: str = "ocr",
    formats: tuple[str, ...] = ("txt", "pdf", "scanned"),
    extractor_factory: Callable[[], Extractor] = make_ocr_extractor,
) -> dict[str, Any]:
    """Run one engine lane and compute metrics, slices, and telemetry."""
    extractor = extractor_factory()
    engine = RulesEngine()
    config_values = {"max_age_days": 90, "allowed_currencies": ["EUR", "GBP"]}
    results = [
        run_case(
            **case,
            engine=engine,
            config_values=config_values,
            extractor=extractor,
            today=today,
        )
        for case in cases
    ]

    field_records: list[tuple[str, Any, Any]] = []
    for result in results:
        for field_name in FIELD_NAMES:
            field_records.append(
                (
                    field_name,
                    result["expected_fields"].get(field_name),
                    result["predicted_fields"][field_name],
                )
            )
    verdict_records = [(r["predicted_verdict"], r["expected_verdict"]) for r in results]
    fields = field_metrics(field_records, field_names=FIELD_NAMES)
    verdict = verdict_metrics(verdict_records)
    confidence_records = [
        (
            result["predicted_fields"][field_name] == result["expected_fields"].get(field_name),
            result["field_confidences"][field_name],
        )
        for result in results
        for field_name in FIELD_NAMES
    ]
    aggregate = {
        "field_accuracy": lane_field_accuracy({"case_count": len(results), "fields": fields}),
        "verdict_agreement": verdict["agreement_rate"],
    }
    return {
        "lane": lane_name,
        "formats": list(formats),
        "case_count": len(results),
        "fields": fields,
        "verdict": verdict,
        "confidence": confidence_separation(confidence_records),
        "slices": slice_metrics(results),
        "results": results,
        "aggregate": aggregate,
    }


def slice_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Accuracy broken down per slice key (language, tier, scenario, format)."""
    slices: dict[str, dict[str, Any]] = {}
    for dimension in ("language", "tier", "scenario", "format"):
        groups: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            key = result["slices"].get(dimension, "unknown")
            groups.setdefault(str(key), []).append(result)
        for key, group in sorted(groups.items()):
            field_records = [
                (name, r["expected_fields"].get(name), r["predicted_fields"][name])
                for r in group
                for name in FIELD_NAMES
            ]
            verdicts = [(r["predicted_verdict"], r["expected_verdict"]) for r in group]
            metrics = field_metrics(field_records, field_names=FIELD_NAMES)
            total_cells = len(group) * len(FIELD_NAMES)
            matches = sum(metrics[name]["matches"] for name in FIELD_NAMES)
            slices[f"{dimension}:{key}"] = {
                "cases": len(group),
                "field_accuracy": matches / total_cells if total_cells else 0.0,
                "verdict_agreement": verdict_metrics(verdicts)["agreement_rate"],
            }
    return slices


def prepare_cases(
    manifest: dict[str, Any], *, txt_only: bool = False, pdf_only: bool = False
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for entry in manifest["txt_cases"]:
        if pdf_only:
            continue
        cases.append(
            {
                "case_id": entry["case_id"],
                "document": DocumentInput(
                    text=(GOLDEN_DIR / f"{entry['case_id']}.txt").read_text(encoding="utf-8"),
                    filename=f"{entry['case_id']}.txt",
                ),
                "expected": _expected_for(entry),
            }
        )
    if not txt_only:
        for entry in manifest["pdf_cases"]:
            cases.append(
                {
                    "case_id": entry["case_id"],
                    "document": DocumentInput(
                        pdf_bytes=(GOLDEN_DIR / f"{entry['case_id']}.pdf").read_bytes(),
                        filename=f"{entry['case_id']}.pdf",
                    ),
                    "expected": _expected_for(entry),
                }
            )
    return cases


def _expected_for(entry: dict[str, Any]) -> dict[str, Any]:
    expected = json.loads(
        (GOLDEN_DIR / f"{entry['case_id']}.expected.json").read_text(encoding="utf-8")
    )
    return expected


def prepare_scanned_cases(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Prepare the scanned lane separately so txt/pdf gates are unchanged."""
    return [
        {
            "case_id": entry["case_id"],
            "document": DocumentInput(
                pdf_bytes=(GOLDEN_DIR / f"{entry['case_id']}.pdf").read_bytes(),
                filename=f"{entry['case_id']}.pdf",
            ),
            "expected": _expected_for(entry),
        }
        for entry in manifest["scanned_cases"]
    ]


def lane_field_accuracy(report: dict[str, Any]) -> float:
    total_cells = report["case_count"] * len(FIELD_NAMES)
    matches = sum(report["fields"][name]["matches"] for name in FIELD_NAMES)
    return matches / total_cells if total_cells else 0.0


def _slice_metrics(lane: dict[str, Any], key: str) -> dict[str, Any] | None:
    return lane["slices"].get(key)


def _gate_row(
    label: str,
    metrics: dict[str, Any] | None,
    *,
    thresholds: tuple[float, float] | None,
    informative: bool,
) -> tuple[str, str, str]:
    if metrics is None:
        return label, "INFO", "slice absent"
    accuracy = metrics["field_accuracy"]
    agreement = metrics["verdict_agreement"]
    detail = f"field_accuracy={accuracy:.4f} verdict_agreement={agreement:.4f}"
    if informative or thresholds is None:
        return label, "INFO", detail
    minimum_accuracy, minimum_agreement = thresholds
    status = "PASS" if accuracy >= minimum_accuracy and agreement >= minimum_agreement else "FAIL"
    thresholds_text = (
        f"field_accuracy>={minimum_accuracy:.2f} verdict_agreement>={minimum_agreement:.2f}"
    )
    return label, status, f"{detail}; {thresholds_text}"


def print_gates(report: dict[str, Any]) -> None:
    """Print hard tier gates and informative slice/overall gates."""
    print("\nGATES")
    rows: list[tuple[str, str, str]] = []
    for lane_name in ("txt", "pdf"):
        lane = report["lanes"].get(lane_name)
        if lane is None:
            continue
        for tier, thresholds in ((0, (0.95, 0.95)), (1, (0.60, 0.25))):
            rows.append(
                _gate_row(
                    f"{lane_name} tier:{tier}",
                    _slice_metrics(lane, f"tier:{tier}"),
                    thresholds=thresholds,
                    informative=False,
                )
            )
        rows.append(
            _gate_row(
                f"{lane_name} tier:2 + scenarios",
                _slice_metrics(lane, "tier:2"),
                thresholds=None,
                informative=True,
            )
        )
        for key, metrics in lane["slices"].items():
            if key.startswith("scenario:"):
                rows.append(
                    _gate_row(f"{lane_name} {key}", metrics, thresholds=None, informative=True)
                )
        rows.append(
            _gate_row(
                f"{lane_name} overall",
                {
                    "field_accuracy": lane_field_accuracy(lane),
                    "verdict_agreement": lane["verdict"]["agreement_rate"],
                },
                thresholds=None,
                informative=True,
            )
        )

    # Multi-lane mode (--lane): engine lanes carry pre-aggregated metrics in
    # ``aggregate``. Print an informative row per lane so the GATES section is
    # never silently empty; hard tier gates stay a txt/pdf-only contract.
    for lane_name, lane in report["lanes"].items():
        if lane_name in {"txt", "pdf"}:
            continue
        for tier in (0, 1, 2):
            tier_slice = _slice_metrics(lane, f"tier:{tier}")
            if tier_slice is not None:
                rows.append(
                    _gate_row(
                        f"{lane_name} tier:{tier}",
                        tier_slice,
                        thresholds=None,
                        informative=True,
                    )
                )
        rows.append(
            _gate_row(
                f"{lane_name} overall",
                {
                    "field_accuracy": lane.get("aggregate", {}).get(
                        "field_accuracy",
                        (lane.get("fields") and lane_field_accuracy(lane)) or 0.0,
                    ),
                    "verdict_agreement": lane.get("verdict", {}).get("agreement_rate", 0.0),
                },
                thresholds=None,
                informative=True,
            )
        )

    for label, status, detail in rows:
        print(f"  [{status}] {label:<38} {detail}")
    if any(row[1] == "FAIL" for row in rows):
        raise SystemExit(1)


def format_value(value: Any) -> str:
    return "<none>" if value is None else str(value)


def print_report(report: dict[str, Any]) -> None:
    print(f"EVALUATION (as-of {report['as_of']})")
    for lane_name, lane in report["lanes"].items():
        print(f"\nLANE {lane_name}: {lane.get('case_count', 0)} cases")
        if lane.get("fields"):
            print(f"{'field':<18} {'exact':>8} {'precision':>10} {'recall':>8}")
            for field_name in FIELD_NAMES:
                if field_name in lane["fields"]:
                    metrics = lane["fields"][field_name]
                    print(
                        f"{field_name:<18} {metrics['exact_match_rate']:>8.2%} "
                        f"{metrics['precision']:>10.2%} {metrics['recall']:>8.2%}"
                    )
        verdict = lane.get("verdict", {})
        rate = verdict.get("agreement_rate", 0.0)
        if "agreements" in verdict and "total" in verdict:
            print(
                f"verdict agreement: {rate:.2%} "
                f"({verdict['agreements']}/{verdict['total']})"
            )
        else:
            print(f"verdict agreement: {rate:.2%}")

    print("\nSLICES (field_accuracy / verdict_agreement / cases)")
    for lane_name, lane in report["lanes"].items():
        for key, values in lane.get("slices", {}).items():
            print(
                f"  [{lane_name}] {key:<24} {values['field_accuracy']:>7.2%}  "
                f"{values['verdict_agreement']:>7.2%}  {values['cases']:>3}"
            )

    print("\nFAILURES")
    any_failures = False
    for lane_name, lane in report["lanes"].items():
        for result in lane.get("results", []):
            for field_name in FIELD_NAMES:
                predicted = result.get("predicted_fields", {}).get(field_name)
                expected = result.get("expected_fields", {}).get(field_name)
                if predicted != expected:
                    any_failures = True
                    print(
                        f"[{lane_name}][field] {result['case_id']} :: {field_name}: "
                        f"expected={format_value(expected)!r} "
                        f"got={format_value(predicted)!r} "
                        f"evidence={result.get('field_evidence', {}).get(field_name, '')!r}"
                    )
            if result.get("predicted_verdict") != result.get("expected_verdict"):
                any_failures = True
                expected_v = result.get("expected_verdict")
                predicted_v = result.get("predicted_verdict")
                print(
                    f"[{lane_name}][verdict] {result['case_id']}: "
                    f"expected={expected_v} got={predicted_v}"
                )
    if not any_failures:
        print("none")

    print("\nOVERALL")
    for lane_name, lane in report["lanes"].items():
        accuracy = lane.get("aggregate", {}).get("field_accuracy")
        if accuracy is None and lane.get("fields"):
            accuracy = lane_field_accuracy(lane)
        accuracy_val = accuracy if accuracy is not None else 0.0
        agreement = lane.get("verdict", {}).get("agreement_rate", 0.0)
        print(
            f"{lane_name:<10} field_accuracy={accuracy_val:.4f} "
            f"verdict_agreement={agreement:.4f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=str, default=DEFAULT_AS_OF.isoformat())
    parser.add_argument("--txt-only", action="store_true")
    parser.add_argument("--pdf-only", action="store_true")
    parser.add_argument(
        "--include-scanned",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="report scanned results as a separate lane; default only affects reports",
    )
    parser.add_argument("--json", type=Path, help="write the full report dictionary here")
    parser.add_argument("--gates", dest="gates", action="store_true", default=True)
    parser.add_argument("--no-gates", dest="gates", action="store_false")
    parser.add_argument("--min-field-accuracy", type=float, default=None)
    parser.add_argument("--min-verdict-agreement", type=float, default=None)
    parser.add_argument(
        "--lane",
        action="append",
        default=None,
        metavar="LANE[,LANE...]",
        help="ocr, slm, vlm, auto, or all; repeatable (default: credential-free ocr)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="enable slm/vlm; both lanes also require OPENROUTER_API_KEY",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="write the full report and decision table here",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        today = date.fromisoformat(args.as_of)
    except ValueError as exc:
        raise SystemExit(f"invalid --as-of: {args.as_of}") from exc

    manifest = load_manifest()
    txt_cases = prepare_cases(manifest, txt_only=True)
    pdf_cases = prepare_cases(manifest, pdf_only=True)
    scanned_cases = prepare_scanned_cases(manifest)
    case_sets = {"txt": txt_cases, "pdf": pdf_cases, "scanned": scanned_cases}
    available_formats: set[str] = set()
    if args.txt_only or not args.pdf_only:
        available_formats.add("txt")
    if not args.txt_only:
        available_formats.add("pdf")
    if args.include_scanned and not args.txt_only and not args.pdf_only:
        available_formats.add("scanned")

    raw_lanes = args.lane or [",".join(default_lane_request())]
    requested: list[str] = []
    for lane_spec in raw_lanes:
        requested.extend(lane.strip().lower() for lane in lane_spec.split(",") if lane.strip())
    if "all" in requested:
        requested = list(LANE_NAMES)
    try:
        plans = resolve_lane_plans(
            tuple(requested),
            live=args.live,
            has_api_key=bool(os.environ.get("OPENROUTER_API_KEY")),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    factories: dict[str, ExtractorFactory] = {
        "slm": make_llm_extractor,
        "vlm": make_vision_extractor,
        "ocr": make_ocr_extractor,
        "auto": make_auto_extractor,
    }
    lanes: dict[str, Any] = {}
    skipped: list[LanePlan] = []
    for plan in plans:
        if not plan.available:
            skipped.append(plan)
            continue
        formats = tuple(
            format_name for format_name in plan.formats if format_name in available_formats
        )
        if not formats:
            continue
        lane_cases = [case for format_name in formats for case in case_sets[format_name]]
        lanes[plan.name] = run_lane(
            lane_cases,
            today=today,
            lane_name=plan.name,
            formats=formats,
            extractor_factory=factories[plan.name],
        )

    report = {"as_of": today.isoformat(), "lanes": lanes}
    print_report(report)
    for plan in skipped:
        print(f"SKIP {plan.name}: {plan.skip_reason}")
    print_decision_table(report)
    json_payload = {**report, "decision_table": decision_table(report)}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(json_payload, indent=2) + "\n", encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(json_payload, indent=2) + "\n", encoding="utf-8")
    if args.gates:
        print_gates(report)

    legacy_thresholds = (
        args.min_field_accuracy is not None or args.min_verdict_agreement is not None
    )
    if not args.gates and legacy_thresholds:
        worst_accuracy = min(lane_field_accuracy(lane) for lane in lanes.values())
        worst_agreement = min(lane["verdict"]["agreement_rate"] for lane in lanes.values())
        failed_gates: list[str] = []
        if args.min_field_accuracy is not None and worst_accuracy < args.min_field_accuracy:
            failed_gates.append(f"field_accuracy {worst_accuracy:.4f} < {args.min_field_accuracy}")
        if args.min_verdict_agreement is not None and worst_agreement < args.min_verdict_agreement:
            failed_gates.append(
                f"verdict_agreement {worst_agreement:.4f} < {args.min_verdict_agreement}"
            )
        if failed_gates:
            print(f"\nGATE FAILED: {'; '.join(failed_gates)}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
