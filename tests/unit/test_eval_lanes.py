"""Network-free tests for the multi-lane evaluation harness."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from eval.lanes import (
    GLM_FLASH_PRICE_PER_TOKEN,
    LANE_FORMATS,
    decision_table,
    default_lane_request,
    estimate_cost_usd,
    extraction_telemetry,
    resolve_lane_plans,
)
from eval.run import FIELD_NAMES, run_case, run_lane

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
)
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput
from docvalidator.rules_engine import RulesEngine


class FakeExtractor(Extractor):
    """Deterministic extractor double with controllable telemetry."""

    backend_name = "fake"

    def __init__(
        self,
        values: dict[str, Any] | None = None,
        duration_ms: float = 1.0,
        total_tokens: int | None = None,
        fail: bool = False,
    ) -> None:
        self.values = values or {}
        self.duration_ms = duration_ms
        self.total_tokens = total_tokens
        self.fail = fail

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        if self.fail:
            raise ValueError("deliberate extraction failure")
        metadata = ExtractionMetadata(
            backend=self.backend_name,
            duration_ms=self.duration_ms,
            total_tokens=self.total_tokens,
        )
        return DocumentExtraction(
            fields={
                name: ExtractedField(value=self.values.get(name), confidence=1.0)
                for name in FIELD_NAMES
            },
            metadata=metadata,
        )


def _case(case_id: str = "case", format_name: str = "txt", tier: int = 0) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "document": DocumentInput(text="invoice"),
        "expected": {
            "expected_fields": {},
            "expected_verdict_status": "REJECTED",
            "slices": {"format": format_name, "tier": tier},
        },
    }


@pytest.mark.parametrize(
    ("requested", "live", "has_key", "expected"),
    [
        (("offline",), False, False, ["offline"]),
        (("ocr",), False, False, ["ocr"]),
        (("offline", "ocr"), False, False, ["offline", "ocr"]),
        (("slm",), False, True, []),
        (("vlm",), True, False, []),
        (("slm",), True, True, ["slm"]),
        (("vlm",), True, True, ["vlm"]),
        (("all",), False, False, ["offline", "ocr"]),
        (("all",), True, True, ["offline", "slm", "vlm", "ocr"]),
    ],
)
def test_lane_plans_availability(
    requested: tuple[str, ...],
    live: bool,
    has_key: bool,
    expected: list[str],
) -> None:
    plans = resolve_lane_plans(requested, live=live, has_api_key=has_key)
    available = [plan.name for plan in plans if plan.available]
    assert available == expected


def test_lane_plan_eligibility_matrix() -> None:
    assert LANE_FORMATS["offline"] == ("txt", "pdf", "scanned")
    assert LANE_FORMATS["slm"] == ("txt", "pdf")
    assert LANE_FORMATS["vlm"] == ("scanned", "pdf")
    assert LANE_FORMATS["ocr"] == ("scanned", "pdf")


def test_resolve_lane_plans_rejects_unknown_lane() -> None:
    with pytest.raises(ValueError, match="unknown lanes"):
        resolve_lane_plans(("nope",), live=False, has_api_key=False)


def test_cost_estimation_constants_and_local_lanes() -> None:
    assert pytest.approx(0.000000325) == GLM_FLASH_PRICE_PER_TOKEN
    assert estimate_cost_usd(1000, lane="offline") == 0.0
    assert estimate_cost_usd(1000, lane="ocr") == 0.0
    assert estimate_cost_usd(1000, lane="slm") == pytest.approx(0.000325)
    assert estimate_cost_usd(2825, lane="vlm") == pytest.approx(0.000918125)


def test_extraction_telemetry_reads_metadata() -> None:
    extraction = FakeExtractor(duration_ms=12.5, total_tokens=345).extract(
        DocumentInput(text="invoice")
    )
    assert extraction_telemetry(extraction) == {
        "duration_ms": 12.5,
        "total_tokens": 345,
    }


def test_run_case_captures_telemetry_and_errors_as_misses() -> None:
    case = _case()
    result = run_case(
        case["case_id"],
        case["document"],
        case["expected"],
        RulesEngine(),
        {"max_age_days": 90, "allowed_currencies": ["EUR", "GBP"]},
        FakeExtractor(duration_ms=2.5, total_tokens=42),
        today=date(2026, 9, 3),
    )
    assert result["duration_ms"] == 2.5
    assert result["total_tokens"] == 42

    failing = run_case(
        case["case_id"],
        case["document"],
        case["expected"],
        RulesEngine(),
        {"max_age_days": 90, "allowed_currencies": ["EUR", "GBP"]},
        FakeExtractor(fail=True),
        today=date(2026, 9, 3),
    )
    assert failing["duration_ms"] is None
    assert failing["total_tokens"] is None


def _lane_report() -> dict[str, Any]:
    return {
        "lanes": {
            "slm": run_lane(
                [_case("one", "txt", 0), _case("two", "txt", 0), _case("three", "pdf", 1)],
                today=date(2026, 9, 3),
                lane_name="slm",
                formats=("txt", "pdf"),
                extractor_factory=lambda: FakeExtractor(
                    values={"supplier_name": "ACME"},
                    duration_ms=10.0,
                    total_tokens=100,
                ),
            )
        }
    }


def test_run_lane_report_shape_with_extractor_factory() -> None:
    lane = _lane_report()["lanes"]["slm"]
    assert lane["lane"] == "slm"
    assert lane["formats"] == ["txt", "pdf"]
    assert lane["case_count"] == 3
    assert set(lane) >= {"fields", "verdict", "slices", "results", "aggregate"}
    assert lane["aggregate"]["field_accuracy"] == pytest.approx(10 / 12)
    assert [result["total_tokens"] for result in lane["results"]] == [100, 100, 100]


def test_decision_table_row_shape_and_costs() -> None:
    report = _lane_report()
    rows = decision_table(report)
    assert [(row["lane"], row["format"], row["tier"]) for row in rows] == [
        ("slm", "txt", 0),
        ("slm", "txt", 1),
        ("slm", "pdf", 0),
        ("slm", "pdf", 1),
    ]
    first = rows[0]
    assert set(first) == {
        "lane",
        "format",
        "tier",
        "field_accuracy",
        "verdict_agreement",
        "avg_ms",
        "avg_tokens",
        "est_cost_per_doc",
    }
    assert first["avg_ms"] == 10.0
    assert first["avg_tokens"] == 100
    assert first["est_cost_per_doc"] == pytest.approx(0.0000325)


def test_default_lane_request_includes_ocr_when_importable() -> None:
    with_ocr = default_lane_request(has_ocr_extra=True)
    without_ocr = default_lane_request(has_ocr_extra=False)
    assert with_ocr == ("offline", "ocr")
    assert without_ocr == ("offline",)
