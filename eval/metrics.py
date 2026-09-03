"""Pure evaluation metrics for extraction and verdict predictions."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def field_metrics(
    records: Iterable[tuple[str, Any, Any]],
    *,
    field_names: Iterable[str] | None = None,
) -> dict[str, dict[str, float | int]]:
    """Calculate exact-match, precision, and recall metrics per field.

    ``None == None`` counts as an exact match and is also reported as
    ``missing_predicted``. Precision and recall use non-null predictions and
    non-null expectations respectively.
    """
    counts: dict[str, dict[str, int]] = {}
    for field_name, expected, predicted in records:
        values = counts.setdefault(
            field_name,
            {
                "matches": 0,
                "missing_predicted": 0,
                "total": 0,
                "expected_non_null": 0,
                "predicted_non_null": 0,
                "true_positives": 0,
            },
        )
        values["total"] += 1
        values["matches"] += 1 if predicted == expected else 0
        values["missing_predicted"] += 1 if expected is None and predicted is None else 0
        values["expected_non_null"] += 1 if expected is not None else 0
        values["predicted_non_null"] += 1 if predicted is not None else 0
        values["true_positives"] += 1 if predicted == expected and predicted is not None else 0

    result: dict[str, dict[str, float | int]] = {}
    names = tuple(field_names) if field_names is not None else tuple(counts)
    for field_name in names:
        values = counts.get(field_name, {})
        exact_match_rate = _rate(values.get("matches", 0), values.get("total", 0))
        precision = _rate(values.get("true_positives", 0), values.get("predicted_non_null", 0))
        recall = _rate(values.get("true_positives", 0), values.get("expected_non_null", 0))
        result[field_name] = {
            **values,
            "exact_match_rate": exact_match_rate,
            "precision": precision,
            "recall": recall,
        }
    return result


def verdict_agreement(
    predicted: str,
    expected: str,
    *,
    total: int = 1,
) -> dict[str, int]:
    """Count one predicted/expected verdict pair for aggregation."""
    return {"agreements": 1 if predicted == expected else 0, "total": total}


def verdict_metrics(
    records: Iterable[tuple[str, str]],
) -> dict[str, float | int]:
    """Calculate agreement rate over predicted/expected verdict records."""
    agreements = 0
    total = 0
    for predicted, expected in records:
        agreements += 1 if predicted == expected else 0
        total += 1
    return {"agreements": agreements, "total": total, "agreement_rate": _rate(agreements, total)}
