"""Built-in supplier invoice rules."""

from datetime import date

from docvalidator.domain.models import DocumentExtraction, RuleResult, ValidationConfig
from docvalidator.rules_engine.base import RuleRegistry


class InvoiceDatePresentAndFresh:
    """Require an invoice date and reject stale invoices.

    Missing data makes the rule inconclusive (cannot judge), not failed.
    """

    rule_id = "invoice_date_present_and_fresh"

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> RuleResult:
        if today is None:
            today = date.today()
        field = extraction.fields.get("invoice_date")
        if field is None or field.value is None:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="invoice date is missing — rule inconclusive",
                inconclusive=True,
            )
        invoice_date = field.value
        if not isinstance(invoice_date, date):
            return RuleResult(rule_id=self.rule_id, passed=False, message="invoice date is invalid")
        age_days = (today - invoice_date).days
        if age_days > config.max_age_days:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message=f"invoice date is older than {config.max_age_days} days",
            )
        return RuleResult(
            rule_id=self.rule_id,
            passed=True,
            message="invoice date is present and fresh",
        )


class TotalAmountPresentAndPositive:
    """Require a total amount greater than zero."""

    rule_id = "total_amount_present_and_positive"

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> RuleResult:
        del config, today
        field = extraction.fields.get("total_amount")
        if field is None or field.value is None:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="total amount is missing — rule inconclusive",
                inconclusive=True,
            )
        if not isinstance(field.value, float) or field.value <= 0:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="total amount must be positive",
            )
        return RuleResult(
            rule_id=self.rule_id,
            passed=True,
            message="total amount is present and positive",
        )


class SupplierNamePresent:
    """Require a non-blank supplier name."""

    rule_id = "supplier_name_present"

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> RuleResult:
        del config, today
        field = extraction.fields.get("supplier_name")
        if field is None or field.value is None:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="supplier name is missing — rule inconclusive",
                inconclusive=True,
            )
        if not str(field.value).strip():
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="supplier name is missing",
            )
        return RuleResult(
            rule_id=self.rule_id,
            passed=True,
            message="supplier name is present",
        )


class CurrencyAllowed:
    """Require an allowed currency when a whitelist is configured."""

    rule_id = "currency_allowed"

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> RuleResult:
        del today
        if config.allowed_currencies is None:
            return RuleResult(rule_id=self.rule_id, passed=True, message="not configured")
        field = extraction.fields.get("currency")
        if field is None or field.value is None:
            return RuleResult(
                rule_id=self.rule_id,
                passed=False,
                message="currency is missing — rule inconclusive",
                inconclusive=True,
            )
        if field.value not in config.allowed_currencies:
            return RuleResult(rule_id=self.rule_id, passed=False, message="currency is not allowed")
        return RuleResult(rule_id=self.rule_id, passed=True, message="currency is allowed")


DEFAULT_RULES: tuple[object, ...] = (
    InvoiceDatePresentAndFresh(),
    TotalAmountPresentAndPositive(),
    SupplierNamePresent(),
    CurrencyAllowed(),
)

registry = RuleRegistry()
for rule in DEFAULT_RULES:
    registry.register(rule)  # type: ignore[arg-type]
