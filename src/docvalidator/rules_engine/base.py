"""Rules engine interfaces."""

from collections.abc import Callable
from datetime import date
from typing import Protocol, runtime_checkable

from docvalidator.domain.models import DocumentExtraction, RuleResult, ValidationConfig


@runtime_checkable
class Rule(Protocol):
    """Protocol for pluggable validation rules."""

    rule_id: str

    def evaluate(
        self,
        extraction: DocumentExtraction,
        config: ValidationConfig,
        *,
        today: date | None = None,
    ) -> RuleResult:
        """Evaluate this rule against an extraction."""
        ...


class RuleRegistry:
    """Registry that allows new rules to be added without editing existing rules."""

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    @property
    def rules(self) -> dict[str, Rule]:
        """Return registered rules."""
        return self._rules

    def register(self, rule: Rule) -> None:
        """Register one rule."""
        self._rules[rule.rule_id] = rule

    def register_decorator(self, rule_factory: Callable[..., Rule]) -> Callable[..., Rule]:
        """Return a decorator that registers the rule produced by a factory."""

        def decorator(*args: object, **kwargs: object) -> Rule:
            rule = rule_factory(*args, **kwargs)
            self.register(rule)
            return rule

        return decorator
