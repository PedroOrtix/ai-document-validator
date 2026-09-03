"""Deterministic synthetic invoice generator: pools, formatting, case building.

Truth is derived from generation parameters (never hand-written) and the RNG
is seeded per case id, so the same case always renders to the same bytes.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

AS_OF = date(2026, 9, 3)  # evaluation reference date
ALLOWED_CURRENCIES = ["EUR", "GBP"]
MAX_AGE_DAYS = 90

CURRENCIES = ("EUR", "GBP", "USD", "CHF", "SEK", "PLN", "JPY")
INVOICE_PREFIXES = ("INV", "FAC", "RE", "FATT", "FT", "AC")

# Curated fictional pools per language. Label pools are ordered: index 0 is
# the canonical (tier-0) label; later entries are progressively rarer.
COUNTRIES: dict[str, dict[str, Any]] = {
    "EN": {
        "company_suffixes": ("Ltd", "GmbH", "B.V.", "Inc."),
        "cities": ("Manchester", "Austin", "Rotterdam", "Dublin", "Cork"),
        "street": "{n} {name} Street",
        "street_names": ("Oak", "Harbor", "Station", "Market", "Victoria"),
        "date_labels": ("Invoice Date", "Date", "Issue Date"),
        "number_labels": ("Invoice No", "Invoice Number", "Invoice #", "Reference"),
        "total_labels": ("Total Amount", "Total Due", "Amount Due", "Grand Total", "Total"),
        "currency_labels": ("Currency", "Currency Code"),
        "vat_labels": ("VAT", "Tax ID", "TAX"),
        "from_labels": ("Supplier", "From", "Issued by", "Vendor", "Seller"),
        "country_word": "United Kingdom",
        "seeds": (
            "Northwind Supplies Ltd",
            "Harbor Analytics Inc",
            "Victoria Timber Ltd",
            "Station Foods B.V.",
            "Market Optics GmbH",
        ),
    },
    "ES": {
        "company_suffixes": ("S.L.", "S.A."),
        "cities": ("Valladolid", "Sevilla", "Zaragoza", "Bilbao", "Murcia"),
        "street": "Calle {name} {n}",
        "street_names": ("Mayor", "Alfonso", "Industria", "Sol", "Norte"),
        "date_labels": ("Fecha", "Fecha de Factura", "Fecha Factura"),
        "number_labels": ("Factura Nº", "Número de Factura", "Factura #"),
        "total_labels": ("Total", "Total Factura", "Importe Total"),
        "currency_labels": ("Moneda", "Divisa"),
        "vat_labels": ("NIF", "CIF", "VAT"),
        "from_labels": ("Proveedor", "Emisor"),
        "country_word": "España",
        "seeds": (
            "Talleres Vega S.L.",
            "Distribuciones Sol S.A.",
            "Cerámica Alfonso S.L.",
            "Logística Bilbao S.L.",
            "Textiles Industria S.A.",
        ),
    },
    "DE": {
        "company_suffixes": ("GmbH", "GmbH & Co. KG", "AG"),
        "cities": ("Düsseldorf", "Stuttgart", "Nürnberg", "Leipzig", "Bonn"),
        "street": "{name}straße {n}",
        "street_names": ("Industrie", "Bahnhof", "Berg", "Rhein", "Linden"),
        "date_labels": ("Datum", "Rechnungsdatum"),
        "number_labels": ("Rechnungsnummer", "Rechnungs-Nr.", "Rechnung #", "Belegnummer"),
        "total_labels": ("Gesamtbetrag", "Rechnungsbetrag", "Gesamt", "Summe"),
        "currency_labels": ("Währung",),
        "vat_labels": ("USt-IdNr.", "VAT"),
        "from_labels": ("Lieferant", "Aussteller"),
        "country_word": "Deutschland",
        "seeds": (
            "Kraft & Söhne Maschinenbau GmbH",
            "Rhein Logistik AG",
            "Linden Elektronik GmbH",
            "Berg Data Service GmbH",
            "Bahnhof Media GmbH & Co. KG",
        ),
    },
    "FR": {
        "company_suffixes": ("SARL", "SAS"),
        "cities": ("Lyon", "Nantes", "Lille", "Bordeaux", "Toulouse"),
        "street": "{n} rue {name}",
        "street_names": (
            "de la République", "Victor Hugo", "Lafayette", "du Marché", "Jean Jaurès",
        ),
        "date_labels": ("Date", "Date de Facture"),
        "number_labels": ("Facture N°", "N° de Facture", "Facture #", "Pièce N°"),
        "total_labels": ("Total", "Total TTC", "Montant Total", "Total Général"),
        "currency_labels": ("Devise",),
        "vat_labels": ("TVA", "N° TVA"),
        "from_labels": ("Fournisseur", "Émetteur"),
        "country_word": "France",
        "seeds": (
            "Boulangerie Lafayette SARL",
            "Constructions Victor SAS",
            "Marché du Nord SARL",
            "Optique Jean Jaurès SAS",
            "Textile Hugo SARL",
        ),
    },
    "IT": {
        "company_suffixes": ("S.r.l.", "S.p.A."),
        "cities": ("Torino", "Bologna", "Verona", "Bari", "Genova"),
        "street": "Via {name} {n}",
        "street_names": ("Roma", "Dante", "Garibaldi", "Verdi", "Mazzini"),
        "date_labels": ("Data", "Data Fattura"),
        "number_labels": ("Fattura N°", "Numero Fattura", "Fattura #", "Documento N°"),
        "total_labels": ("Totale", "Totale Fattura", "Importo Totale"),
        "currency_labels": ("Valuta",),
        "vat_labels": ("P.IVA", "Partita IVA", "VAT"),
        "from_labels": ("Fornitore", "Emittente"),
        "country_word": "Italia",
        "seeds": (
            "Falegnameria Roma S.r.l.",
            "Cartografia Dante S.r.l.",
            "Meccanica Garibaldi S.p.A.",
            "Ottica Mazzini S.r.l.",
            "Editoriale Verdi S.p.A.",
        ),
    },
}

VAT_PREFIX = {"ES": "ES", "DE": "DE", "FR": "FR", "IT": "IT", "EN": "GB"}

MONTH_NAMES = {
    "EN": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
    "ES": ("enero", "febrero", "marzo", "abril", "mayo", "junio",
           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
    "DE": ("Januar", "Februar", "März", "April", "Mai", "Juni",
           "Juli", "August", "September", "Oktober", "November", "Dezember"),
    "FR": ("janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"),
    "IT": ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
           "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"),
}
MONTH_ABBREV = {
    "EN": (
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ),
    "ES": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
    "DE": ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"),
    "FR": (
        "janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc",
    ),
    "IT": ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"),
}

ITEMS = (
    "Consulting services", "Support package", "Software license", "Hardware maintenance",
    "Freight and handling", "Office supplies", "Training session", "Maintenance contract",
    "Installation fee", "Data platform subscription", "Spare parts", "Inspection report",
)
DESCRIPTIONS = {
    "ES": ("Servicios de consultoría", "Formación", "Reparación", "Suministros", "Transporte"),
    "DE": ("Wartung", "Schulung", "Ersatzteile", "Beratung", "Montage"),
    "FR": ("Prestation", "Formation", "Pièces détachées", "Conseil", "Installation"),
    "IT": ("Consulenza", "Formazione", "Ricambi", "Manutenzione", "Installazione"),
}

SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF", "SEK": "kr", "PLN": "zł", "JPY": "¥"}


# --------------------------------------------------------------------------
# Value formatting
# --------------------------------------------------------------------------

def format_amount(value: float, style: str) -> str:
    """Render an amount in a regional style. Styles:

    dot_decimal ``1234.56`` · comma_decimal ``1234,56`` · grouped_eu ``1.234,56``
    grouped_en ``1,234.56`` · space_fr ``12 345,00``
    """
    negative = value < 0
    value = abs(value)
    whole = int(value)
    cents = round((value - whole) * 100)
    if cents == 100:
        whole, cents = whole + 1, 0
    if style == "dot_decimal":
        body = f"{whole}.{cents:02d}"
    elif style == "comma_decimal":
        body = f"{whole},{cents:02d}"
    elif style == "grouped_eu":
        body = f"{whole:,}".replace(",", ".") + f",{cents:02d}"
    elif style == "grouped_en":
        body = f"{whole:,}.{cents:02d}"
    elif style == "space_fr":
        groups = []
        rest = str(whole)
        while len(rest) > 3:
            groups.insert(0, rest[-3:])
            rest = rest[:-3]
        groups.insert(0, rest)
        body = " ".join(groups) + f",{cents:02d}"
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown amount style: {style}")
    return ("-" if negative else "") + body


def currency_tokens(rng: random.Random, currency: str, tier: int) -> tuple[str, str, str | None]:
    """Return (prefix, suffix) around an amount and the canonical truth value.

    Tier 0 keeps a labeled ``Currency: XXX`` line, so the amount itself needs
    no marker. Tier >= 1 may instead carry the currency as symbol/code around
    the amount (truth still the ISO code when a marker exists, else None).
    """
    if tier == 0:
        return "", "", None
    roll = rng.random()
    symbol = SYMBOLS[currency]
    if roll < 0.4:  # symbol next to the amount
        return f"{symbol} ", "", currency
    if roll < 0.8:  # code suffix / prefix
        if rng.random() < 0.5:
            return "", f" {currency}", currency
        return f"{currency} ", "", currency
    return "", "", None  # unlabeled: currency truth stays None


def make_vat(rng: random.Random, lang: str, absent: bool) -> str | None:
    if absent:
        return None
    prefix = VAT_PREFIX[lang]
    if lang == "EN":
        return f"GB{rng.randrange(100, 999)}{rng.randrange(10**6, 10**7 - 1)}"
    return f"{prefix}{rng.randrange(10**7, 10**8 - 1)}"


def make_invoice_number(rng: random.Random, lang: str, tier: int) -> tuple[str, str]:
    """Return (number, label). Label rarity grows with the tier."""
    prefix = rng.choice(INVOICE_PREFIXES)
    roll = rng.random()
    if roll < 0.4:
        number = f"{prefix}-{rng.randrange(2024, 2027)}-{rng.randrange(1, 9999):04d}"
    elif roll < 0.7:
        number = f"{rng.randrange(2024, 2027)}/{rng.randrange(1, 9999):04d}"
    else:
        number = f"{prefix}{rng.randrange(1000, 9999)}"
    labels = COUNTRIES[lang]["number_labels"]
    if tier == 0:
        label = labels[0]
    elif tier == 1:
        label = labels[1] if len(labels) > 1 else labels[0]
    elif tier == 2:
        label = labels[-1]
    else:
        label = labels[0] if len(labels) < 4 else labels[1]
    return number, label


def format_date_value(d: date, style: str, lang: str) -> str:
    """Render a date in one of the dataset styles (day-first for numeric EU)."""
    if style == "iso":
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    if style == "dmy":
        return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"
    if style == "dotted":
        return f"{d.day:02d}.{d.month:02d}.{d.year:04d}"
    if style == "spelled":
        return f"{d.day} {MONTH_NAMES[lang][d.month - 1]} {d.year}"
    if style == "abbrev":
        return f"{d.day:02d} {MONTH_ABBREV[lang][d.month - 1]} {d.year}"
    raise ValueError(f"unknown date style: {style}")  # pragma: no cover


DATE_STYLES = ("iso", "dmy", "dotted", "spelled", "abbrev")


def pick_date(rng: random.Random, tier: int) -> tuple[date, str]:
    """Pick an invoice date and its format style by tier; freshness drives verdict."""
    if tier <= 1:
        # Fresh (PASS-eligible): 5..80 days old.
        d = AS_OF - timedelta(days=rng.randrange(5, 81))
    else:
        roll = rng.random()
        if roll < 0.25:
            d = AS_OF - timedelta(days=rng.randrange(MAX_AGE_DAYS + 10, 300))  # stale -> FAIL
        else:
            d = AS_OF - timedelta(days=rng.randrange(5, 80))
    style = (
        DATE_STYLES[rng.randrange(len(DATE_STYLES))]
        if tier >= 1
        else rng.choice(("iso", "dmy"))
    )
    return d, style
