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
        "lanes": {"ocr": lane("ocr", 0.64, 0.51), "slm": lane("slm", 0.70, 0.48)},
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
    assert "[INFO] ocr overall" in out
    assert "[INFO] slm overall" in out
    assert "field_accuracy=0.6400" in out


def test_print_gates_legacy_mode_keeps_hard_gate_rows(capsys) -> None:
    report = _legacy_report()

    print_gates(report)

    out = capsys.readouterr().out
    assert "[PASS] txt tier:0" in out
    assert "[PASS] pdf tier:0" in out


def test_print_report_multi_lane_emits_lane_and_slices(capsys) -> None:
    from eval.run import print_report

    report = _multi_lane_report()
    print_report(report)

    out = capsys.readouterr().out
    assert "EVALUATION (as-of 2026-09-03)" in out
    assert "LANE ocr: 2 cases" in out
    assert "LANE slm: 2 cases" in out
    assert "SLICES" in out
    assert "[ocr] tier:0" in out
    assert "OVERALL" in out
    assert "ocr        field_accuracy=0.6400" in out


def test_print_report_prints_field_failures(capsys) -> None:
    from eval.run import print_report

    report = {
        "as_of": "2026-09-03",
        "lanes": {
            "ocr": {
                "lane": "ocr",
                "case_count": 1,
                "fields": {
                    name: {"exact_match_rate": 1.0, "precision": 1.0, "recall": 1.0}
                    for name in FIELD_NAMES
                },
                "verdict": {"agreement_rate": 0.0, "agreements": 0, "total": 1},
                "slices": {
                    "tier:0": {"cases": 1, "field_accuracy": 0.83, "verdict_agreement": 0.0}
                },
                "results": [
                    {
                        "case_id": "inv_001",
                        "expected_verdict": "PASS",
                        "predicted_verdict": "FAIL",
                        "expected_fields": {"supplier_name": "ACME"},
                        "predicted_fields": {"supplier_name": "OTHER"},
                        "field_evidence": {"supplier_name": "From: OTHER"},
                    }
                ],
                "aggregate": {"field_accuracy": 0.83, "verdict_agreement": 0.0},
            }
        },
    }
    print_report(report)

    out = capsys.readouterr().out
    assert "LANE ocr: 1 cases" in out
    assert "supplier_name" in out
    assert "FAILURES" in out
    assert "[ocr][field] inv_001 :: supplier_name: expected='ACME' got='OTHER'" in out
    assert "[ocr][verdict] inv_001: expected=PASS got=FAIL" in out
