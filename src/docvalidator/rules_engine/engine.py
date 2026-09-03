"""Validation engine and verdict aggregation."""

from datetime import date

from docvalidator.domain.models import (
    DocumentExtraction,
    RuleResult,
    ValidationConfig,
    Verdict,
)
from docvalidator.rules_engine.base import RuleRegistry
from docvalidator.rules_engine.rules import registry as default_registry


class RulesEngine:
    """Evaluate registered rules and aggregate a final verdict.

    A rule failure produces ``FAIL`` because the document was judged and
    rejected. If no rule fails but a required field is missing, the outcome is
    ``REVIEW`` because the validator cannot judge the document.
    """

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self.registry = registry or default_registry

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> Verdict:
        """Evaluate all registered rules and return the aggregated verdict."""
        synthetic_results: list[RuleResult] = []
        rule_results: list[RuleResult] = []
        missing_required = False

        for field_name in config.required_fields:
            field = extraction.fields.get(field_name)
            if field is None or field.value is None:
                missing_required = True
                synthetic_results.append(
                    RuleResult(
                        rule_id="required_field_present",
                        passed=False,
                        message=f"required field is missing: {field_name}",
                    )
                )

        for rule in self.registry.rules.values():
            rule_results.append(rule.evaluate(extraction, config, today=today))

        all_results = synthetic_results + rule_results
        if any(not result.passed for result in rule_results):
            status = "FAIL"
        elif missing_required:
            status = "REVIEW"
        else:
            status = "PASS"

        return Verdict(status=status, rule_results=all_results, extraction=extraction)
