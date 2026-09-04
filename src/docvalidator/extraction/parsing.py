"""Deterministic regex field parsing over plain invoice text.

This is the text-analysis core shared by every local extraction path: the OCR
floor feeds RapidOCR text into it, and it is unit-tested directly without any
model dependency. It is not an ``Extractor`` by itself — a text source plus
timing/metadata is always supplied by the extractor that uses it.
"""

import re
from datetime import date, datetime
from typing import ClassVar

from docvalidator.domain.models import ISO_4217_CURRENCIES, ExtractedField

_SPANISH_MONTHS: dict[str, int] = {
    "ene": 1,
    "enero": 1,
    "feb": 2,
    "febrero": 2,
    "mar": 3,
    "marzo": 3,
    "abr": 4,
    "abril": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "junio": 6,
    "jul": 7,
    "julio": 7,
    "ago": 8,
    "agosto": 8,
    "sep": 9,
    "set": 9,
    "sept": 9,
    "septiembre": 9,
    "setiembre": 9,
    "oct": 10,
    "octubre": 10,
    "nov": 11,
    "noviembre": 11,
    "dic": 12,
    "diciembre": 12,
}

_SPANISH_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?([A-Za-z]{3,10})\.?\s+(?:de\s+)?(\d{4})\b",
    re.IGNORECASE,
)


def _parse_date_candidate(candidate: str, formats: list[str]) -> date | None:
    """Parse one date string against supported formats and locale-independent Spanish dates."""
    spanish_match = _SPANISH_DATE_PATTERN.search(candidate)
    if spanish_match:
        month_key = spanish_match.group(2).lower()
        if month_key in _SPANISH_MONTHS:
            try:
                day = int(spanish_match.group(1))
                year = int(spanish_match.group(3))
                return date(year, _SPANISH_MONTHS[month_key], day)
            except ValueError:
                pass

    for date_format in formats:
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue
    return None


class RegexFieldParser:
    """Extract invoice fields from text using deterministic regex patterns."""

    _iso_code_pattern: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:EUR|USD|GBP|CHF|JPY|CAD|AUD|SEK|NOK|DKK|PLN)\b"
    )

    def extract_fields(self, text: str) -> dict[str, ExtractedField]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        methods = [
            self._extract_supplier_name,
            self._extract_invoice_number,
            self._extract_invoice_date,
            self._extract_total_amount,
            self._extract_currency,
            self._extract_tax_id,
        ]
        return {
            method.__name__.removeprefix("_extract_"): method(text, lines) for method in methods
        }

    def _field(
        self,
        value: str | float | date | None,
        confidence: float,
        evidence: str | None,
    ) -> ExtractedField:
        return ExtractedField(value=value, confidence=confidence, evidence=evidence)

    def _extract_supplier_name(self, text: str, lines: list[str]) -> ExtractedField:
        labeled = re.compile(
            r"(?:From|Supplier|Issued\s*by|Proveedor|Emisor|Emitido\s*por)\s*[:#]\s*([^\n]+)",
            re.IGNORECASE,
        )
        match = labeled.search(text)
        if match:
            return self._field(match.group(1).strip(), 0.95, match.group(0).strip())
        if not lines:
            return self._field(None, 0.0, None)

        candidate_idx = 0
        header_pat = re.compile(
            r"^(?:Factura|Invoice|Tax\s*Invoice|Rechnung|Recibo)\b",
            re.IGNORECASE,
        )
        if (
            candidate_idx < len(lines)
            and header_pat.search(lines[candidate_idx])
            and len(lines) > 1
        ):
            candidate_idx = 1
        first = lines[candidate_idx]
        meta_split = re.compile(
            r"\b(?:Invoice|Factura|N[úu]mero|Numero|Reference|Referencia|N[ºo°9\.]+|NumerodeFactura|Fecha|Date)\b",
            re.IGNORECASE,
        )
        first_clean = meta_split.split(first)[0].strip(" -:,|")
        if first_clean and re.search(r"[A-Za-z]", first_clean) and len(first_clean) <= 100:
            first = first_clean
        elif len(first) > 100 or not re.search(r"[A-Za-z]", first):
            return self._field(None, 0.0, None)
        return self._field(first, 0.8, first)

    def _extract_invoice_number(self, text: str, lines: list[str]) -> ExtractedField:
        labeled_line_patterns = [
            re.compile(
                r"(?:Invoice\s*(?:No\.?|Number|#)|InvoiceNo|"
                r"(?:N[ºo°9\.]+|N[úu]mero|Num\.)\s*(?:de\s*)?Factura|NumerodeFactura|"
                r"Factura\s*N[ºo°9\.]+|FacturaN9|"
                r"Factura\s*(?:N[úu]mero|Num\.)|"
                r"Factura|"
                r"Rechnungsnummer)"
                r"\s*[:#]?\s*(.*)$",
                re.IGNORECASE,
            )
        ]
        id_token_pattern = re.compile(r"^[A-Z0-9][A-Z0-9-/]{1,30}$", re.IGNORECASE)
        for i, line in enumerate(lines):
            for pat in labeled_line_patterns:
                m = pat.search(line)
                if not m:
                    continue
                rest = m.group(1).strip(" :#,")
                if rest:
                    token_match = re.search(r"\b([A-Z0-9][A-Z0-9-/]{1,30})\b", rest)
                    if token_match:
                        return self._field(token_match.group(1).strip(), 0.95, line)
                    return self._field(None, 0.0, None)
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip(" :#,")
                    if id_token_pattern.match(next_line):
                        return self._field(next_line, 0.95, f"{line}\n{lines[i + 1]}")

        fallback = re.compile(r"\bINV-\d{4}-\d{3,8}\b", re.IGNORECASE)
        match = fallback.search(text)
        if match:
            return self._field(match.group(0).upper(), 0.8, match.group(0))

        return self._field(None, 0.0, None)

    def _extract_invoice_date(self, text: str, lines: list[str]) -> ExtractedField:
        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d %B %Y",
            "%B %d, %Y",
            "%d.%m.%Y",
            "%b %d, %Y",
        ]
        labeled_line_patterns = [
            re.compile(
                r"\b(?:Invoice\s*Date|Date|Fecha\s*(?:de\s*)?(?:factura|emisi[oó]n|expedici[oó]n)?|Fecha)"
                r"\s*[:#]?\s*(.*)$",
                re.IGNORECASE,
            )
        ]
        for i, line in enumerate(lines):
            for pat in labeled_line_patterns:
                m = pat.search(line)
                if not m:
                    continue
                rest = m.group(1).strip(" ,;|\t")
                if rest:
                    val = _parse_date_candidate(rest, formats)
                    if val is not None:
                        return self._field(val, 0.95, line)
                    # Label matched with unparsable text on the same line:
                    # trust the label over subsequent lines or tokens.
                    return self._field(None, 0.0, None)
                if i + 1 < len(lines):
                    next_val = _parse_date_candidate(lines[i + 1].strip(" ,;|\t"), formats)
                    if next_val is not None:
                        return self._field(next_val, 0.95, f"{line}\n{lines[i + 1]}")

        # Scan full text tokens if no labeled date matched
        for token_match in re.finditer(r"\d{1,4}[./-]\d{1,2}[./-]\d{2,4}", text):
            value = _parse_date_candidate(token_match.group(0), formats)
            if value is not None:
                return self._field(value, 0.8, token_match.group(0))
        return self._field(None, 0.0, None)

    def _extract_total_amount(self, text: str, lines: list[str]) -> ExtractedField:
        labeled_prefixes = [
            re.compile(
                r"^(?:Grand\s*Total|Amount\s*Due|Total\s*Due|Total\s*Amount|Importe\s*Total|Gesamtbetrag|Total)"
                r"\s*[:#]?\s*(.*)$",
                re.IGNORECASE,
            ),
        ]
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            for pat in labeled_prefixes:
                m = pat.search(line)
                if not m:
                    continue
                rest = m.group(1).strip()
                if rest:
                    parsed = self._parse_amount(rest)
                    if parsed is not None:
                        return self._field(parsed, 0.95, line)
                elif i + 1 < len(lines):
                    parsed_next = self._parse_amount(lines[i + 1])
                    if parsed_next is not None:
                        return self._field(parsed_next, 0.95, f"{line}\n{lines[i + 1]}")

        labeled_patterns = [
            re.compile(r"\bGrand\s*Total\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bAmount\s*Due\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*Due\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*Amount\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bImporte\s*Total\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bGesamtbetrag\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
            re.compile(r"\bTotal\s*[:#]?\s*([^\n]+)", re.IGNORECASE),
        ]
        amount = None
        evidence = None
        for pattern in labeled_patterns:
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
        match = labeled.search(text)
        if match:
            code = match.group(1).upper()
            if code in ISO_4217_CURRENCIES:
                return self._field(code, 0.95, match.group(0).strip())

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
        match = pattern.search(text)
        if match:
            value = match.group(1).upper()
            confidence = 0.95 if re.match(r"^[A-Z]{2}", value) else 0.9
            return self._field(value, confidence, match.group(0).strip())
        return self._field(None, 0.0, None)

    def _parse_amount(self, candidate: str) -> float | None:
        candidate = candidate.strip()
        # Optional leading sign so credit notes ("-350.00") parse as negatives.
        # Support European thousand separators with spaces: e.g. "680 867,00"
        match = re.search(
            r"[-+]?\s*(?:[$€£]|EUR|USD|GBP)?\s*"
            r"(?:\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)",
            candidate,
        )
        if not match:
            return None
        raw = match.group(0)
        negative = "-" in raw
        raw = (
            raw.strip("+- $€£")
            .replace("EUR", "")
            .replace("USD", "")
            .replace("GBP", "")
            .replace(" ", "")
            .strip()
        )
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
