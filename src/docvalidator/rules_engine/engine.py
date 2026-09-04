"""Validation engine and verdict aggregation."""

from datetime import date
from typing import Literal

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
    ``REVIEW`` because the validator cannot judge the document. A failed rule
    with ``severity="review"`` also produces ``REVIEW``: the rule flags a
    quality concern for a human without rejecting the document.

    ``verdict_confidence`` is the engine's confidence in a ``PASS`` verdict:
    the minimum evidence strength among the fields that participated in the
    decision. When the verdict is ``PASS`` every required field decided it;
    when a rule fails or is inconclusive, only that rule's ``deciding_fields``
    participated. ``FAIL``/``REVIEW`` are pinned to ``0.0``: they carry their
    own rule evidence and are not confidence-bearing decisions.
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
        status: Literal["PASS", "FAIL", "REVIEW"]
        if any(result.rejects_verdict for result in rule_results):
            status = "FAIL"
        elif missing_required or any(result.requests_review for result in rule_results):
            status = "REVIEW"
        else:
            status = "PASS"

        verdict_confidence = self._verdict_confidence(status, config, all_results, extraction)
        return Verdict(
            status=status,
            verdict_confidence=verdict_confidence,
            rule_results=all_results,
            extraction=extraction,
        )

    @staticmethod
    def _verdict_confidence(
        status: str,
        config: ValidationConfig,
        results: list[RuleResult],
        extraction: DocumentExtraction,
    ) -> float:
        """Minimum confidence among the fields that decided the verdict."""
        if status != "PASS":
            return 0.0
        deciding = set(config.required_fields)
        for result in results:
            if not result.inconclusive:
                continue
            deciding.update(result.deciding_fields)
        confidences = [
            extraction.fields[name].confidence
            for name in sorted(deciding)
            if name in extraction.fields
        ]
        return min(confidences) if confidences else 0.0
