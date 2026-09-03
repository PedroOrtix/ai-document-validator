"""Deterministic offline extraction for supplier invoices."""

import re
from datetime import date, datetime
from typing import ClassVar

from docvalidator.domain.models import (
    DocumentExtraction,
    ExtractedField,
    ExtractionMetadata,
)
from docvalidator.extraction.base import Extractor
from docvalidator.extraction.input import DocumentInput


def _parse_date_candidate(candidate: str, formats: list[str]) -> date | None:
    """Parse one date string against the supported formats, or return None."""
    for date_format in formats:
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue
    return None


class OfflineExtractor(Extractor):
    """Extract invoice fields using deterministic, offline regex patterns."""

    backend = "offline"

    _iso_code_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:EUR|USD|GBP|CHF|JPY|CAD|AUD|SEK|NOK|DKK|PLN)\b"
    )

    def extract(self, document: DocumentInput) -> DocumentExtraction:
        text = document.to_text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        methods = [
            self._extract_supplier_name,
            self._extract_invoice_number,
            self._extract_invoice_date,
            self._extract_total_amount,
            self._extract_currency,
            self._extract_tax_id,
        ]
        fields = {
            method.__name__.removeprefix("_extract_"): method(text, lines)
            for method in methods
        }
        return DocumentExtraction(
            fields=fields,
            metadata=ExtractionMetadata(backend=self.backend),
        )

    def _first_match(self, text: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
        return pattern.search(text)

    def _field(
        self,
        value: str | float | date | None,
        confidence: float,
        evidence: str | None,
    ) -> ExtractedField:
        return ExtractedField(value=value, confidence=confidence, evidence=evidence)

    def _extract_supplier_name(self, text: str, lines: list[str]) -> ExtractedField:
        labeled = re.compile(
            r"(?:From|Supplier|Issued by)\s*[:#]\s*([^\n]+)",
            re.IGNORECASE,
        )
        match = self._first_match(text, labeled)
        if match:
            return self._field(match.group(1).strip(), 0.95, match.group(0).strip())
        if not lines:
            return self._field(None, 0.0, None)
        first = lines[0]
        # A first line with no letters at all ("@@@@ ####") is not a company name.
        if len(first) > 100 or not re.search(r"[A-Za-z]", first):
            return self._field(None, 0.0, None)
        return self._field(first, 0.8, first)

    def _extract_invoice_number(self, text: str, lines: list[str]) -> ExtractedField:
        del lines
        labeled = re.compile(
            r"(?:Invoice\s*(?:No\.?|Number|#)|Factura\s*N[ºo°]|Rechnungsnummer)\s*[:#]?\s*([A-Z0-9][A-Z0-9-/]{1,30})",
            re.IGNORECASE,
        )
        match = self._first_match(text, labeled)
        if match:
            return self._field(match.group(1).strip(), 0.95, match.group(0).strip())

        fallback = re.compile(r"\bINV-\d{4}-\d{3,8}\b", re.IGNORECASE)
        match = self._first_match(text, fallback)
        if match:
            return self._field(match.group(0).upper(), 0.8, match.group(0))

        return self._field(None, 0.0, None)

    def _extract_invoice_date(self, text: str, lines: list[str]) -> ExtractedField:
        del lines
        labeled_patterns = [
            (re.compile(r"\bInvoice\s*Date\s*[:#]?\s*([^\n]+)", re.IGNORECASE), 0.95),
            (re.compile(r"\bFecha\s*[:#]?\s*([^\n]+)", re.IGNORECASE), 0.95),
            (re.compile(r"\bDate\s*[:#]?\s*([^\n]+)", re.IGNORECASE), 0.9),
        ]
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d %B %Y",
            "%B %d, %Y",
            "%d.%m.%Y",
            "%b %d, %Y",
        ]
        for pattern, confidence in labeled_patterns:
            match = pattern.search(text)
            if not match:
                continue
            candidate = match.group(1).strip(" ,;|\t")
            value = _parse_date_candidate(candidate, formats)
            if value is not None:
                return self._field(value, confidence, candidate)
            # The label matched but its value was unparsable: trust the label
            # over a later unlabeled date token and report the miss honestly.
            return self._field(None, 0.0, None)

        # No labeled date: scan every date-looking token in order and accept
        # the first one that parses (D/M/Y before M/D/Y, matching our formats
        # list order). Previously re.search re-returned the same first token
        # for every format, silently discarding later valid dates.
        for token_match in re.finditer(r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}", text):
            value = _parse_date_candidate(token_match.group(0), formats)
            if value is not None:
                return self._field(value, 0.8, token_match.group(0))
        return self._field(None, 0.0, None)

    def _extract_total_amount(self, text: str, lines: list[str]) -> ExtractedField:
        del lines
        labeled_patterns = [
            re.compile(r"\bGrand\s*Total\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bAmount\s*Due\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*Due\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*Amount\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bGesamtbetrag\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
        ]
        # Prefer strong labels; among equal-strength labels (multiple "Total"
        # rows: line items, subtotals, grand total) the LAST match wins — the
        # grand total is conventionally the bottom-most total on the page.
        for pattern in labeled_patterns:
            amount = None
            evidence = None
            for match in pattern.finditer(text):
                parsed = self._parse_amount(match.group(1))
                if parsed is not None:
                    amount = parsed
                    evidence = match.group(0).strip()
            if amount is not None:
                return self._field(amount, 0.95, evidence)
        return self._field(None, 0.0, None)

    def _extract_currency(self, text: str, lines: list[str]) -> ExtractedField:
        del lines
        labeled = re.compile(r"\bCurrency\s*[:#]?\s*([A-Za-z]{3})\b", re.IGNORECASE)
        match = self._first_match(text, labeled)
        if match:
            return self._field(match.group(1).upper(), 0.95, match.group(0).strip())

        symbol_patterns = [
            (re.compile(r"[$€£]\s*\d"), {"$": "USD", "€": "EUR", "£": "GBP"}),
            (re.compile(r"\d\s*(?:[$€£])"), {"$": "USD", "€": "EUR", "£": "GBP"}),
        ]
        symbols: dict[str, str] = {}
        for pattern, mapping in symbol_patterns:
            if pattern.search(text):
                symbols = mapping
                break
        if symbols:
            symbol_match = re.search(r"([$€£])", text)
            if symbol_match:
                return self._field(symbols[symbol_match.group(1)], 0.8, symbol_match.group(1))

        code_match = self._iso_code_pattern.search(text)
        if code_match:
            return self._field(code_match.group(0).upper(), 0.9, code_match.group(0))
        return self._field(None, 0.0, None)

    def _extract_tax_id(self, text: str, lines: list[str]) -> ExtractedField:
        del lines
        pattern = re.compile(
            r"\b(?:VAT|TAX|USt[- ]?IdNr|NIF|CIF|NIE)\.?\s*(?:No\.?|Number)?\s*[:#]?\s*"
            r"((?:[A-Z]{2})?[A-Z0-9]{5,14})",
            re.IGNORECASE,
        )
        match = self._first_match(text, pattern)
        if match:
            value = match.group(1).upper()
            confidence = 0.95 if re.match(r"^[A-Z]{2}", value) else 0.9
            return self._field(value, confidence, match.group(0).strip())
        return self._field(None, 0.0, None)

    def _parse_amount(self, candidate: str) -> float | None:
        candidate = candidate.strip()
        # Optional leading sign so credit notes ("-350.00") parse as negatives.
        match = re.search(
            r"[-+]?\s*(?:[$€£]|EUR|USD|GBP)?\s*\d+(?:[.,]\d{3})*(?:[.,]\d{2})?", candidate
        )
        if not match:
            return None
        raw = match.group(0)
        negative = "-" in raw
        raw = raw.strip("+- $€£").replace("EUR", "").replace("USD", "").replace("GBP", "").strip()
        last_comma = raw.rfind(",")
        last_dot = raw.rfind(".")
        if last_comma > last_dot:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return None
        return -value if negative else value
