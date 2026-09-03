import pytest
from eval.metrics import (
    confidence_separation,
    field_metrics,
    verdict_agreement,
    verdict_metrics,
)


def test_exact_match_counting_and_none_handling() -> None:
    metrics = field_metrics(
        [
            ("supplier_name", "Northwind", "Northwind"),
            ("invoice_number", None, None),
            ("total_amount", 10.0, 12.0),
        ]
    )

    assert metrics["supplier_name"]["matches"] == 1
    assert metrics["invoice_number"]["matches"] == 1
    assert metrics["invoice_number"]["missing_predicted"] == 1
    assert metrics["total_amount"]["matches"] == 0


def test_precision_and_recall_math() -> None:
    metrics = field_metrics(
        [
            ("currency", "EUR", "EUR"),
            ("currency", "GBP", "GBP"),
            ("currency", "USD", "GBP"),
            ("currency", None, "EUR"),
            ("currency", None, None),
        ]
    )

    assert metrics["currency"]["true_positives"] == 2
    assert metrics["currency"]["predicted_non_null"] == 4
    assert metrics["currency"]["expected_non_null"] == 3
    assert metrics["currency"]["precision"] == 0.5
    assert metrics["currency"]["recall"] == 2 / 3


def test_verdict_agreement() -> None:
    assert verdict_agreement("PASS", "PASS") == {"agreements": 1, "total": 1}
    assert verdict_agreement("PASS", "FAIL") == {"agreements": 0, "total": 1}

    metrics = verdict_metrics([("PASS", "PASS"), ("PASS", "FAIL")])
    assert metrics == {"agreements": 1, "total": 2, "agreement_rate": 0.5}


def test_confidence_separation_splits_matched_from_mismatched() -> None:
    records = [
        (True, 0.95),
        (True, 0.8),
        (False, 0.3),
        (False, 0.1),
        (True, None),  # None confidence counts as 0.0 on a matched cell
    ]
    metrics = confidence_separation(records)
    assert metrics["mean_confidence_matched"] == pytest.approx(
        round((0.95 + 0.8 + 0.0) / 3, 4)
    )
    assert metrics["mean_confidence_mismatched"] == pytest.approx(0.2)
    assert metrics["matched_cells"] == 3
    assert metrics["mismatched_cells"] == 2


def test_confidence_separation_empty_is_zeroed() -> None:
    metrics = confidence_separation([])
    assert metrics == {
        "mean_confidence_matched": 0.0,
        "mean_confidence_mismatched": 0.0,
        "matched_cells": 0,
        "mismatched_cells": 0,
    }
