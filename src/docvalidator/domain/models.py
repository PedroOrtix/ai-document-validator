"""Core domain models for document extraction and validation."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedField(BaseModel):
    """A single value extracted from a document.

    Confidence is *evidence strength*, not a model probability:

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
    total_tokens: int | None = None
    fallback_reason: str | None = None


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

    ``severity`` is the consequence the rule prescribes when it fails:
    ``reject`` sends the verdict to ``FAIL``, ``review`` sends it to ``REVIEW``
    (a human should look at the document; the validator is not asserting the
    document is bad). ``deciding_fields`` names the extraction fields whose
    confidence participates in the verdict confidence when the rule is
    inconclusive.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    passed: bool
    message: str
    inconclusive: bool = False
    severity: Literal["reject", "review"] = "reject"
    deciding_fields: tuple[str, ...] = ()

    @property
    def rejects_verdict(self) -> bool:
        """True when this failed rule should push the verdict to FAIL."""
        return not self.passed and not self.inconclusive and self.severity == "reject"

    @property
    def requests_review(self) -> bool:
        """True when this failed rule should push the verdict to REVIEW."""
        return not self.passed and self.severity == "review"


class Verdict(BaseModel):
    """Final validation outcome.

    ``REVIEW`` means the validator cannot judge the document because required
    data is missing. ``FAIL`` means the validator judged the document and
    rejected it.

    ``verdict_confidence`` is the confidence the engine has in a ``PASS``: the
    minimum evidence strength among the fields that decided the verdict (all
    ``required_fields`` when passing, the failed/inconclusive rules' input
    fields otherwise). A missing field contributes ``0.0``. ``FAIL`` and
    ``REVIEW`` do not carry decision confidence — they carry their rule
    evidence — so the field is fixed at ``0.0`` for them.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["PASS", "FAIL", "REVIEW"]
    verdict_confidence: Annotated[float, Field(ge=0, le=1)] = 0.0
    rule_results: list[RuleResult]
    extraction: DocumentExtraction


ISO_4217_CURRENCIES: frozenset[str] = frozenset(
    {
        "AED",
        "AFN",
        "ALL",
        "AMD",
        "ANG",
        "AOA",
        "ARS",
        "AUD",
        "AWG",
        "AZN",
        "BAM",
        "BBD",
        "BDT",
        "BGN",
        "BHD",
        "BIF",
        "BMD",
        "BND",
        "BOB",
        "BRL",
        "BSD",
        "BTN",
        "BWP",
        "BYN",
        "BZD",
        "CAD",
        "CDF",
        "CHF",
        "CLP",
        "CNY",
        "COP",
        "CRC",
        "CUC",
        "CUP",
        "CVE",
        "CZK",
        "DJF",
        "DKK",
        "DOP",
        "DZD",
        "EGP",
        "ERN",
        "ETB",
        "EUR",
        "FJD",
        "FKP",
        "GBP",
        "GEL",
        "GHS",
        "GIP",
        "GMD",
        "GNF",
        "GTQ",
        "GYD",
        "HKD",
        "HNL",
        "HRK",
        "HTG",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "IQD",
        "IRR",
        "ISK",
        "JMD",
        "JOD",
        "JPY",
        "KES",
        "KGS",
        "KHR",
        "KMF",
        "KPW",
        "KRW",
        "KWD",
        "KYD",
        "KZT",
        "LAK",
        "LBP",
        "LKR",
        "LRD",
        "LSL",
        "LYD",
        "MAD",
        "MDL",
        "MGA",
        "MKD",
        "MMK",
        "MNT",
        "MOP",
        "MRU",
        "MUR",
        "MVR",
        "MWK",
        "MXN",
        "MYR",
        "MZN",
        "NAD",
        "NGN",
        "NIO",
        "NOK",
        "NPR",
        "NZD",
        "OMR",
        "PAB",
        "PEN",
        "PGK",
        "PHP",
        "PKR",
        "PLN",
        "PYG",
        "QAR",
        "RON",
        "RSD",
        "RUB",
        "RWF",
        "SAR",
        "SBD",
        "SCR",
        "SDG",
        "SEK",
        "SGD",
        "SHP",
        "SLL",
        "SOS",
        "SRD",
        "SSP",
        "STN",
        "SVC",
        "SYP",
        "SZL",
        "THB",
        "TJS",
        "TMT",
        "TND",
        "TOP",
        "TRY",
        "TTD",
        "TWD",
        "TZS",
        "UAH",
        "UGX",
        "USD",
        "UYU",
        "UZS",
        "VES",
        "VND",
        "VUV",
        "WST",
        "XAF",
        "XCD",
        "XOF",
        "XPF",
        "YER",
        "ZAR",
        "ZMW",
        "ZWL",
    }
)


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
            if currency not in ISO_4217_CURRENCIES:
                raise ValueError(f"currency must be a valid ISO 4217 code: {currency!r}")
        return value
