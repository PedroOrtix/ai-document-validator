"""Core domain models for document extraction and validation."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedField(BaseModel):
    """A single value extracted from a document.

    Confidence semantics:
    - ``0.95`` or higher: an exact labeled pattern was matched.
    - ``0.7`` through ``0.9``: a strong structural pattern was matched.
    - Below ``0.5``: a fuzzy or heuristic guess.
    - A missing field has ``value=None`` and ``confidence=0``.

    ``evidence`` is a short quote from the source and ``page_hint`` is optional
    because plain-text documents do not have page boundaries.
    """

    model_config = ConfigDict(frozen=True)

    value: str | float | date | None = None
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence: str | None = None
    page_hint: int | None = None


class ExtractionMetadata(BaseModel):
    """Execution details for one extraction."""

    model_config = ConfigDict(frozen=True)

    backend: str
    duration_ms: float | None = None
    model: str | None = None
    provider: str | None = None


class DocumentExtraction(BaseModel):
    """The canonical extraction result for a supplier invoice."""

    model_config = ConfigDict(frozen=True)

    document_type: Literal["SUPPLIER_INVOICE"] = "SUPPLIER_INVOICE"
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    metadata: ExtractionMetadata

    @field_validator("fields")
    @classmethod
    def validate_field_keys(cls, value: dict[str, ExtractedField]) -> dict[str, ExtractedField]:
        expected = {
            "supplier_name",
            "invoice_number",
            "invoice_date",
            "total_amount",
            "currency",
            "tax_id",
        }
        if set(value) != expected:
            raise ValueError(f"fields must have exactly these keys: {sorted(expected)!r}")
        return value

    def get_field(self, name: str) -> ExtractedField | None:
        """Return a stored field, or ``None`` when the key is not present."""
        return self.fields.get(name)


class RuleResult(BaseModel):
    """Outcome of one validation rule.

    ``inconclusive`` marks a rule that could not be evaluated because its
    input data is missing. An inconclusive rule does not push the verdict to
    ``FAIL`` by itself; missing required fields surface as ``REVIEW`` via the
    engine's required-field check.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    passed: bool
    message: str
    inconclusive: bool = False


class Verdict(BaseModel):
    """Final validation outcome.

    ``REVIEW`` means the validator cannot judge the document because required
    data is missing. ``FAIL`` means the validator judged the document and
    rejected it.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["PASS", "FAIL", "REVIEW"]
    rule_results: list[RuleResult]
    extraction: DocumentExtraction


class ValidationConfig(BaseModel):
    """Configuration for validating a document."""

    model_config = ConfigDict(frozen=True)

    document_type: Literal["SUPPLIER_INVOICE"] = "SUPPLIER_INVOICE"
    max_age_days: Annotated[int, Field(gt=0)] = 90
    allowed_currencies: list[str] | None = None
    required_fields: list[str] = Field(
        default_factory=lambda: [
            "supplier_name",
            "invoice_number",
            "invoice_date",
            "total_amount",
        ]
    )

    @field_validator("required_fields")
    @classmethod
    def validate_required_fields(cls, value: list[str]) -> list[str]:
        allowed = {
            "supplier_name",
            "invoice_number",
            "invoice_date",
            "total_amount",
            "currency",
            "tax_id",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown required fields: {sorted(unknown)!r}")
        return value

    @field_validator("allowed_currencies")
    @classmethod
    def validate_allowed_currencies(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        for currency in value:
            if len(currency) != 3 or not currency.isalpha() or not currency.isupper():
                raise ValueError(f"currency must be 3 uppercase letters: {currency!r}")
        return value
