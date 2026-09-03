from eval.metrics import field_metrics, verdict_agreement, verdict_metrics


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
