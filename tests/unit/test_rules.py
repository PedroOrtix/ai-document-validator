"""Unit tests for rules and verdict aggregation."""

from datetime import date

import pytest

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
    ValidationConfig,
)
from docvalidator.rules_engine import (
    CurrencyAllowed,
    InvoiceDatePresentAndFresh,
    LowConfidenceFieldsReview,
    RulesEngine,
    SupplierNamePresent,
    TotalAmountPresentAndPositive,
)


def make_extraction(**fields: ExtractedField) -> DocumentExtraction:
    all_fields = {
        key: ExtractedField(value=None, confidence=0.0)
        for key in (
            "supplier_name",
            "invoice_number",
            "invoice_date",
            "total_amount",
            "currency",
            "tax_id",
        )
    }
    all_fields.update(fields)
    return DocumentExtraction(
        fields=all_fields,
        metadata=ExtractionMetadata(backend="test"),
    )


def make_field(value: object) -> ExtractedField:
    return ExtractedField(value=value, confidence=0.9, evidence="test")


def test_invoice_date_rule_age_boundary() -> None:
    extraction = make_extraction(invoice_date=make_field(date(2026, 1, 1)))
    config = ValidationConfig(max_age_days=90)
    rule = InvoiceDatePresentAndFresh()
    assert rule.evaluate(extraction, config, today=date(2026, 4, 1)).passed
    assert not rule.evaluate(extraction, config, today=date(2026, 4, 2)).passed


def test_invoice_date_rule_future_date() -> None:
    extraction = make_extraction(invoice_date=make_field(date(2027, 1, 1)))
    config = ValidationConfig(max_age_days=90)
    rule = InvoiceDatePresentAndFresh()
    result = rule.evaluate(extraction, config, today=date(2026, 9, 3))
    assert result.passed is False
    assert "future" in result.message


def test_invoice_date_rule_missing() -> None:
    rule = InvoiceDatePresentAndFresh()
    result = rule.evaluate(make_extraction(), ValidationConfig(), today=date(2026, 1, 1))
    assert result.passed is False
    assert "missing" in result.message


def test_total_amount_rule_pass_and_fail() -> None:
    rule = TotalAmountPresentAndPositive()
    extraction = make_extraction(total_amount=make_field(1.0))
    assert rule.evaluate(extraction, ValidationConfig()).passed
    extraction = make_extraction(total_amount=make_field(0.0))
    assert not rule.evaluate(extraction, ValidationConfig()).passed
    assert not rule.evaluate(make_extraction(), ValidationConfig()).passed


def test_supplier_name_rule_pass_and_fail() -> None:
    rule = SupplierNamePresent()
    extraction = make_extraction(supplier_name=make_field("Acme"))
    assert rule.evaluate(extraction, ValidationConfig()).passed
    extraction = make_extraction(supplier_name=make_field("   "))
    assert not rule.evaluate(extraction, ValidationConfig()).passed


@pytest.mark.parametrize(
    ("allowed", "currency_value", "expected_passed", "expected_message"),
    [
        (["EUR", "GBP"], "EUR", True, "currency is allowed"),
        (["EUR", "GBP"], "USD", False, "currency is not allowed"),
        (["EUR", "GBP"], None, False, "currency is missing — rule inconclusive"),
        (None, None, True, "not configured"),
    ],
)
def test_currency_allowed_rule(
    allowed: list[str] | None,
    currency_value: str | None,
    expected_passed: bool,
    expected_message: str,
) -> None:
    rule = CurrencyAllowed()
    config = ValidationConfig(allowed_currencies=allowed)
    field = (
        make_field(currency_value) if currency_value else ExtractedField(value=None, confidence=0)
    )
    result = rule.evaluate(make_extraction(currency=field), config)
    assert result.passed is expected_passed
    assert result.message == expected_message


def test_missing_currency_with_allowed_currencies_yields_review() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-2026-0001"),
        currency=ExtractedField(value=None, confidence=0.0),
    )
    config = ValidationConfig(allowed_currencies=["EUR", "GBP"])
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 15))
    assert verdict.status == "REVIEW"


def test_missing_currency_without_allowed_currencies_yields_pass() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-2026-0001"),
        currency=ExtractedField(value=None, confidence=0.0),
    )
    config = ValidationConfig(allowed_currencies=None)
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 15))
    assert verdict.status == "PASS"


def test_invalid_currency_code_in_config_raises() -> None:
    with pytest.raises(ValueError, match="valid ISO 4217"):
        ValidationConfig(allowed_currencies=["INVALID"])


def test_engine_passes_valid_document() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-2026-0001"),
    )
    config = ValidationConfig()
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 15))
    assert verdict.status == "PASS"


def test_missing_data_is_inconclusive_not_failed() -> None:
    """Rules without their input data are inconclusive; missing required
    fields drive the verdict to REVIEW, never to FAIL on their own."""
    verdict = RulesEngine().evaluate(make_extraction(), ValidationConfig(), today=date(2026, 1, 15))
    assert verdict.status == "REVIEW"
    failed = [r for r in verdict.rule_results if not r.passed]
    assert failed, "required-field synthetic results must be present"
    assert all(r.rule_id == "required_field_present" or r.inconclusive for r in failed)


def test_present_data_violation_is_fail_even_with_other_missing_fields() -> None:
    """A judged-and-rejected rule (data present) forces FAIL regardless."""
    extraction = make_extraction(
        total_amount=make_field(-5.0),
        invoice_date=make_field(date(2026, 1, 1)),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-2026-0001"),
    )
    verdict = RulesEngine().evaluate(extraction, ValidationConfig(), today=date(2026, 1, 15))
    assert verdict.status == "FAIL"


def test_engine_reviews_when_required_fields_are_missing() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        invoice_number=make_field("INV-2026-0001"),
        supplier_name=ExtractedField(value=None, confidence=0.0),
    )
    config = ValidationConfig()
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 2))
    assert verdict.status == "REVIEW"
    supplier_rule = next(
        result for result in verdict.rule_results if result.rule_id == "supplier_name_present"
    )
    assert supplier_rule.passed is False
    assert supplier_rule.inconclusive is True
    assert any(
        result.rule_id == "required_field_present" and "supplier_name" in result.message
        for result in verdict.rule_results
    )


def test_engine_fails_when_a_rule_rejects_document() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2025, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
    )
    config = ValidationConfig()
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 1))
    assert verdict.status == "FAIL"


def test_engine_rule_fail_takes_priority_over_review() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2025, 1, 1)),
        supplier_name=make_field("Acme"),
    )
    config = ValidationConfig()
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 1))
    assert verdict.status == "FAIL"


def test_pass_verdict_confidence_is_min_required_field_confidence() -> None:
    """PASS carries the weakest evidence among the deciding fields."""
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=ExtractedField(value="INV-1", confidence=0.6, evidence="x"),
    )
    verdict = RulesEngine().evaluate(extraction, ValidationConfig(), today=date(2026, 1, 15))
    assert verdict.status == "PASS"
    assert verdict.verdict_confidence == pytest.approx(0.6)


def test_fail_and_review_verdicts_pin_confidence_to_zero() -> None:
    """FAIL/REVIEW carry rule evidence, not decision confidence."""
    fail_extraction = make_extraction(total_amount=make_field(-5.0))
    verdict = RulesEngine().evaluate(fail_extraction, ValidationConfig(), today=date(2026, 1, 15))
    assert verdict.status == "FAIL"
    assert verdict.verdict_confidence == 0.0
    review_verdict = RulesEngine().evaluate(make_extraction(), ValidationConfig())
    assert review_verdict.status == "REVIEW"
    assert review_verdict.verdict_confidence == 0.0


def test_low_confidence_rule_flags_below_threshold_and_reviews() -> None:
    """Low confidence is a review signal, never an auto-reject."""
    rule = LowConfidenceFieldsReview()
    extraction = make_extraction(
        total_amount=ExtractedField(value=10.0, confidence=0.3, evidence="x"),
    )
    result = rule.evaluate(extraction, ValidationConfig())
    assert result.passed is False
    assert result.severity == "review"
    assert result.deciding_fields == ("total_amount",)
    verdict = RulesEngine().evaluate(extraction, ValidationConfig())
    assert verdict.status == "REVIEW"
    flagged = next(r for r in verdict.rule_results if r.rule_id == rule.rule_id)
    assert flagged.passed is False and flagged.severity == "review"


def test_strong_fields_pass_confidence_rule_and_keep_pass_verdict() -> None:
    rule = LowConfidenceFieldsReview()
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-1"),
    )
    result = rule.evaluate(extraction, ValidationConfig())
    assert result.passed is True
    assert result.deciding_fields == ()
    verdict = RulesEngine().evaluate(extraction, ValidationConfig(), today=date(2026, 1, 15))
    assert verdict.status == "PASS"
    assert verdict.verdict_confidence == pytest.approx(0.9)


def test_missing_fields_do_not_trigger_confidence_rule() -> None:
    """Absent fields are the required-field check's job, not this rule's."""
    rule = LowConfidenceFieldsReview()
    result = rule.evaluate(make_extraction(), ValidationConfig())
    assert result.passed is True
    assert result.deciding_fields == ()
