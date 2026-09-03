"""Unit tests for eval gate printing in both legacy and multi-lane modes."""

from eval.run import FIELD_NAMES, print_gates


def _per_field_matches(matches: int, total: int) -> dict:
    return {name: {"matches": matches, "total": total} for name in FIELD_NAMES}


def _multi_lane_report() -> dict:
    """Synthetic multi-lane report: engine lanes only (no legacy txt/pdf lanes)."""
    def lane(name: str, acc: float, agreement: float) -> dict:
        return {
            "lane": name,
            "formats": ["scanned"],
            "case_count": 2,
            "fields": {},
            "verdict": {"agreement_rate": agreement},
            "slices": {
                "tier:0": {"cases": 2, "field_accuracy": acc, "verdict_agreement": agreement}
            },
            "results": [],
            "aggregate": {"field_accuracy": acc, "verdict_agreement": agreement},
        }

    return {
        "as_of": "2026-09-03",
        "lanes": {"offline": lane("offline", 0.64, 0.51), "ocr": lane("ocr", 0.70, 0.48)},
    }


def _legacy_report() -> dict:
    """Legacy report shape: dataset lanes txt/pdf (the no---lane mode)."""
    def dataset_lane(name: str, acc: float, agreement: float) -> dict:
        return {
            "lane": name,
            "case_count": 4,
            "fields": _per_field_matches(3, 4),
            "verdict": {"agreement_rate": agreement},
            "slices": {
                "tier:0": {"cases": 2, "field_accuracy": 1.0, "verdict_agreement": 1.0},
                "tier:1": {"cases": 1, "field_accuracy": 0.61, "verdict_agreement": 0.26},
                "tier:2": {"cases": 1, "field_accuracy": acc, "verdict_agreement": agreement},
            },
            "results": [],
        }

    return {
        "as_of": "2026-09-03",
        "lanes": {"txt": dataset_lane("txt", 0.9, 0.9), "pdf": dataset_lane("pdf", 0.9, 0.9)},
    }


def test_print_gates_multi_lane_emits_info_rows_per_engine_lane(capsys) -> None:
    report = _multi_lane_report()

    print_gates(report)  # must not raise SystemExit

    out = capsys.readouterr().out
    assert "GATES" in out
    assert "[INFO] offline overall" in out
    assert "[INFO] ocr overall" in out
    assert "field_accuracy=0.6400" in out


def test_print_gates_legacy_mode_keeps_hard_gate_rows(capsys) -> None:
    report = _legacy_report()

    print_gates(report)

    out = capsys.readouterr().out
    assert "[PASS] txt tier:0" in out
    assert "[PASS] pdf tier:0" in out
