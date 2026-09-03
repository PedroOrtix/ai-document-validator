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
        make_field(currency_value)
        if currency_value
        else ExtractedField(value=None, confidence=0)
    )
    result = rule.evaluate(make_extraction(currency=field), config)
    assert result.passed is expected_passed
    assert result.message == expected_message


def test_engine_passes_valid_document() -> None:
    extraction = make_extraction(
        invoice_date=make_field(date(2026, 1, 1)),
        total_amount=make_field(100.0),
        supplier_name=make_field("Acme"),
        invoice_number=make_field("INV-2026-0001"),
    )
    config = ValidationConfig()
    verdict = RulesEngine().evaluate(extraction, config, today=date(2026, 1, 15))
    print(verdict.rule_results)
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
