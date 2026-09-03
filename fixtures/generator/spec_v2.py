"""FROZEN spec for golden dataset v2 — single source of truth.

Hermes froze this file before delegating generation to coding agents. It defines:
the evaluation reference date and rule config, the value-formatting helpers whose
semantics both lanes MUST share, the fictional content pools (EN/ES), and the
frozen case matrix (40 txt + 20 pdf). Codex units consume this module; they do
not edit its constants. Truth is always derived from these parameters plus a
seeded RNG (``case_rng(case_id)``), never hand-written.

Lanes:
    txt: 20 EN + 20 ES  (6 easy t0 / 8 medium t1 / 6 hard t2 per language)
    pdf: 10 EN + 10 ES  (3 t0 / 4 t1 / 3 t2 per language, all single-page)
"""

from __future__ import annotations

import random
from datetime import date

# ---------------------------------------------------------------------------
# Frozen evaluation constants (mirror the config the rules engine is evaluated with)
# ---------------------------------------------------------------------------
AS_OF = date(2026, 9, 3)
MAX_AGE_DAYS = 90
ALLOWED_CURRENCIES = ["EUR", "GBP"]

LANGS = ("EN", "ES")

# Date styles by tier (t0 stays canonical; t1/t2 use the full range).
DATE_STYLES_T0 = ("iso", "dmy")
DATE_STYLES_FULL = ("iso", "dmy", "dotted", "spelled", "abbrev")
# Amount styles by tier.
AMOUNT_STYLES_T0 = ("dot_decimal", "grouped_eu")
AMOUNT_STYLES_FULL = ("dot_decimal", "comma_decimal", "grouped_eu", "grouped_en", "space_fr")

SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF", "SEK": "kr", "JPY": "¥"}
VAT_PREFIX = {"EN": "GB", "ES": "ES"}

# ---------------------------------------------------------------------------
# Frozen truth helpers (semantics shared by both lanes — do not change)
# ---------------------------------------------------------------------------


def format_amount(value: float, style: str) -> str:
    """Render an amount in a regional style (same semantics as dataset v1)."""
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
        groups: list[str] = []
        rest = str(whole)
        while len(rest) > 3:
            groups.insert(0, rest[-3:])
            rest = rest[:-3]
        groups.insert(0, rest)
        body = " ".join(groups) + f",{cents:02d}"
    else:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown amount style: {style}")
    return ("-" if negative else "") + body


MONTH_NAMES = {
    "EN": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
    "ES": ("enero", "febrero", "marzo", "abril", "mayo", "junio",
           "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
}
MONTH_ABBREV = {
    "EN": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ES": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
}


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


def make_vat(rng: random.Random, lang: str, absent: bool) -> str | None:
    if absent:
        return None
    if lang == "EN":
        return f"GB{rng.randrange(100, 999)}{rng.randrange(10**6, 10**7 - 1)}"
    return f"ES{rng.randrange(10**7, 10**8 - 1)}"


def make_invoice_number(rng: random.Random, lang: str, tier: int) -> tuple[str, str]:
    """Return (number, label). Label rarity grows with the tier."""
    prefix = rng.choice(POOLS[lang]["invoice_prefixes"])
    roll = rng.random()
    if roll < 0.4:
        number = f"{prefix}-{rng.randrange(2024, 2027)}-{rng.randrange(1, 9999):04d}"
    elif roll < 0.7:
        number = f"{rng.randrange(2024, 2027)}/{rng.randrange(1, 9999):04d}"
    else:
        number = f"{prefix}{rng.randrange(1000, 9999)}"
    labels = POOLS[lang]["number_labels"]
    if tier == 0:
        label = labels[0]
    elif tier == 1:
        label = labels[1]
    else:
        label = labels[-1]
    return number, label


def case_rng(case_id: str) -> random.Random:
    """Deterministic per-case RNG: same case id always renders identical bytes."""
    return random.Random(f"golden-v2:{case_id}")


def expected_verdict(
    invoice_date: date | None,
    amount: float | None,
    currency: str | None,
    required_missing: bool,
) -> str:
    """Derive the truth verdict from the documented rule semantics."""
    if invoice_date is not None and (AS_OF - invoice_date).days > MAX_AGE_DAYS:
        return "FAIL"
    if amount is not None and amount <= 0:
        return "FAIL"
    if currency is not None and currency not in ALLOWED_CURRENCIES:
        return "FAIL"
    if required_missing:
        return "REVIEW"
    return "PASS"


# ---------------------------------------------------------------------------
# Fictional pools (EN/ES) — enriched for v2 (boilerplate, tables, distractors)
# ---------------------------------------------------------------------------

POOLS: dict[str, dict] = {
    "EN": {
        "company_suffixes": ("Ltd", "GmbH", "B.V.", "Inc."),
        "seeds": (
            "Northwind Supplies Ltd", "Harbor Analytics Inc", "Victoria Timber Ltd",
            "Station Foods B.V.", "Market Optics GmbH", "Oakbridge Logistics Ltd",
            "Beacon Manufacturing Inc", "Crestline Software Ltd", "Union Print Works Ltd",
            "Falcon Freight B.V.",
        ),
        "cities": (
            "Manchester", "Austin", "Rotterdam", "Dublin", "Cork", "Bristol", "Leeds", "Utrecht",
        ),
        "street": "{n} {name} Street",
        "street_names": (
            "Oak", "Harbor", "Station", "Market", "Victoria", "Bridge", "Queen", "Park",
        ),
        "date_labels": ("Invoice Date", "Date", "Issue Date"),
        "number_labels": ("Invoice No", "Invoice Number", "Invoice #", "Reference"),
        "total_labels": ("Total Amount", "Total Due", "Amount Due", "Grand Total", "Total"),
        "currency_labels": ("Currency", "Currency Code"),
        "vat_labels": ("VAT", "Tax ID", "TAX"),
        "from_labels": ("Supplier", "From", "Issued by", "Vendor", "Seller"),
        "invoice_prefixes": ("INV", "FT", "AC", "CR"),
        "items": (
            "Consulting services", "Support package", "Software license", "Hardware maintenance",
            "Freight and handling", "Office supplies", "Training session", "Maintenance contract",
            "Installation fee", "Data platform subscription", "Spare parts", "Inspection report",
        ),
        "table_items": (
            ("Consulting (hours)", 60, 140), ("Support retainer (month)", 200, 900),
            ("License seat (year)", 90, 400), ("Workshop day", 350, 800),
            ("Hardware unit", 120, 1500), ("Freight consignment", 45, 260),
            ("Training seat", 120, 480), ("Inspection visit", 150, 600),
            ("Spare part set", 30, 700), ("Data export (batch)", 80, 300),
        ),
        "banks": (
            ("Barclays", "BUKGB22"), ("NatWest", "NWBKGB2L"), ("HSBC", "HBUKGB4B"),
            ("Santander UK", "ABNAGB210"), ("Revolut", "REVOGB21"),
        ),
        "country_word": "United Kingdom",
        "phone": "+44 20 {d1} {d2}",
        "terms": (
            "Payment due within 30 days of the invoice date.",
            "Bank transfers only; state the invoice number as reference.",
            "Late payments accrue interest at 2% per month.",
        ),
        "distractor_labels": ("Subtotal", "VAT (20%)", "Discount", "Shipping", "Total excl. VAT"),
        "stamps": ("PAID", "COPY", "DUPLICATE", "VOID"),
        "vat_rate": 0.20,
    },
    "ES": {
        "company_suffixes": ("S.L.", "S.A."),
        "seeds": (
            "Talleres Vega S.L.", "Distribuciones Sol S.A.", "Cerámica Alfonso S.L.",
            "Logística Bilbao S.L.", "Textiles Industria S.A.", "Ferretería Montes S.L.",
            "Transportes Ribera S.A.", "Panadería Miralvalle S.L.", "Estudio Jurídico Alcázar S.L.",
            "Viveros Guadiana S.L.",
        ),
        "cities": (
            "Valladolid", "Sevilla", "Zaragoza", "Bilbao", "Murcia", "Toledo", "Cáceres", "Logroño",
        ),
        "street": "Calle {name} {n}",
        "street_names": (
            "Mayor", "Alfonso", "Industria", "Sol", "Norte", "Atocha", "Rioja", "Huerta",
        ),
        "date_labels": ("Fecha", "Fecha de Factura", "Fecha Factura"),
        "number_labels": ("Factura Nº", "Número de Factura", "Factura #", "Referencia"),
        "total_labels": (
            "Total", "Total Factura", "Importe Total", "Total a Pagar", "Importe Total",
        ),
        "currency_labels": ("Moneda", "Divisa"),
        "vat_labels": ("NIF", "CIF", "VAT"),
        "from_labels": ("Proveedor", "Emisor"),
        "invoice_prefixes": ("FAC", "RE", "FT", "AB"),
        "items": (
            "Servicios de consultoría", "Formación", "Reparación", "Suministros", "Transporte",
            "Mantenimiento", "Instalación", "Licencias de software", "Piezas de repuesto",
            "Inspección técnica", "Alquiler de maquinaria", "Suscripción de datos",
        ),
        "table_items": (
            ("Consultoría (horas)", 35, 90), ("Mantenimiento (mes)", 180, 800),
            ("Licencia (año)", 95, 420), ("Jornada de formación", 300, 750),
            ("Equipo hardware", 140, 1400), ("Transporte envío", 40, 240),
            ("Plaza de curso", 110, 460), ("Visita técnica", 130, 550),
            ("Kit de repuestos", 25, 650), ("Exportación de datos", 70, 280),
        ),
        "banks": (
            ("Santander", "SANTESMM"), ("BBVA", "BBVAESMM"), ("CaixaBank", "CAIXESBB"),
            ("Banco Sabadell", "BSABESBB"), ("Unicaja", "UNESMM1A"),
        ),
        "country_word": "España",
        "phone": "+34 9{d1} {d2} {d3}",
        "terms": (
            "Pago a 30 días desde la fecha de factura.",
            "Transferencia bancaria indicando el número de factura.",
            "Demora de pago: 2 % de interés mensual.",
        ),
        "distractor_labels": (
            "Subtotal", "IVA (21 %)", "Base imponible", "Cuota IVA",
            "Total sin IVA", "Descuento", "Portes",
        ),
        "stamps": ("COPIA", "PAGADO", "DUPLICADO", "ANULADO"),
        "vat_rate": 0.21,
    },
}


def make_iban(rng: random.Random, lang: str) -> str:
    """Fictional-but-shape-plausible IBAN, grouped in blocks of 4."""
    if lang == "EN":
        body = f"{rng.randrange(10, 99)}{POOLS['EN']['banks'][0][1][:4]}"
        digits = "".join(str(rng.randrange(10)) for _ in range(14))
        body = body + digits
    else:
        digits = "".join(str(rng.randrange(10)) for _ in range(20))
        body = "ES" + f"{rng.randrange(10, 99)}" + digits
    blocks = [body[i:i + 4] for i in range(0, len(body), 4)]
    return " ".join(blocks)


def make_contact(rng: random.Random, lang: str, company: str) -> tuple[str, str]:
    """Deterministic (phone, email) boilerplate from the company slug."""
    slug = "".join(
        ch for ch in company.lower().replace(" ", "").replace(".", "") if ch.isalpha()
    )
    if lang == "EN":
        phone = POOLS["EN"]["phone"].format(
            d1=rng.randrange(1000, 9999), d2=rng.randrange(1000, 9999)
        )
    else:
        phone = POOLS["ES"]["phone"].format(
            d1=rng.randrange(100, 999), d2=rng.randrange(100, 999), d3=rng.randrange(100, 999)
        )
    email = f"billing@{slug[:24]}.example.com"
    return phone, email


# ---------------------------------------------------------------------------
# Frozen case matrix
# ---------------------------------------------------------------------------
# Scenario kinds (verdict derived by expected_verdict()):
#   clean            PASS-eligible (fresh date, positive amount, allowed currency)
#   stale            FAIL: age_days > MAX_AGE_DAYS
#   stale_just_over  FAIL: age_days == 91 (boundary)
#   zero_amount      FAIL: amount == 0.0
#   disallowed_currency FAIL: visible currency not in ALLOWED_CURRENCIES
#   missing_number   REVIEW: required field invoice_number absent
#   missing_date     REVIEW: required field invoice_date absent
#   missing_total    REVIEW: required field total_amount absent
# Extra knobs: currency_markers="none" (no marker anywhere -> currency truth None),
# currency pinned per row for determinism; drop-field scenarios isolate rules by
# using canonical labels/formats (tier-0 style) so the slice tests the rule.

TXT_PLAN: list[dict] = [
    # --- EN: 6 t0 / 8 t1 / 6 t2 -------------------------------------------------
    *[{"case_id": f"t0_en_{i}", "lang": "EN", "tier": 0, "scenario": {"kind": "clean"},
       "currency": "EUR"} for i in range(5)],
    {"case_id": "t0_en_5", "lang": "EN", "tier": 0,
     "scenario": {"kind": "stale", "age_days": 150}, "currency": "GBP"},
    *[{"case_id": f"t1_en_{i}", "lang": "EN", "tier": 1, "scenario": {"kind": "clean"},
       "currency": c} for i, c in enumerate(("EUR", "GBP", "EUR", "GBP", "EUR"))],
    {"case_id": "t1_en_5", "lang": "EN", "tier": 1,
     "scenario": {"kind": "zero_amount", "amount": 0.0, "age_days": 10}, "currency": "EUR"},
    {"case_id": "t1_en_6", "lang": "EN", "tier": 1,
     "scenario": {"kind": "stale_just_over", "age_days": 91}, "currency": "EUR"},
    {"case_id": "t1_en_7", "lang": "EN", "tier": 1,
     "scenario": {"kind": "missing_number", "drop": "number", "currency": "EUR", "age_days": 10},
     "currency": "EUR"},
    {"case_id": "t2_en_0", "lang": "EN", "tier": 2,
     "scenario": {"kind": "clean", "currency_markers": "none"}, "currency": "EUR"},
    *[{"case_id": f"t2_en_{i}", "lang": "EN", "tier": 2, "scenario": {"kind": "clean"},
       "currency": "GBP"} for i in (1, 2)],
    {"case_id": "t2_en_3", "lang": "EN", "tier": 2,
     "scenario": {"kind": "disallowed_currency", "currency": "USD", "age_days": 10},
     "currency": "USD"},
    {"case_id": "t2_en_4", "lang": "EN", "tier": 2,
     "scenario": {"kind": "stale_old", "age_days": 240}, "currency": "EUR"},
    {"case_id": "t2_en_5", "lang": "EN", "tier": 2,
     "scenario": {"kind": "missing_total", "drop": "total", "currency": "EUR", "age_days": 10},
     "currency": "EUR"},
    # --- ES: 6 t0 / 8 t1 / 6 t2 -------------------------------------------------
    *[{"case_id": f"t0_es_{i}", "lang": "ES", "tier": 0, "scenario": {"kind": "clean"},
       "currency": "EUR"} for i in range(5)],
    {"case_id": "t0_es_5", "lang": "ES", "tier": 0,
     "scenario": {"kind": "stale", "age_days": 150}, "currency": "GBP"},
    *[{"case_id": f"t1_es_{i}", "lang": "ES", "tier": 1, "scenario": {"kind": "clean"},
       "currency": c} for i, c in enumerate(("EUR", "GBP", "EUR", "GBP", "EUR"))],
    {"case_id": "t1_es_5", "lang": "ES", "tier": 1,
     "scenario": {"kind": "zero_amount", "amount": 0.0, "age_days": 10}, "currency": "EUR"},
    {"case_id": "t1_es_6", "lang": "ES", "tier": 1,
     "scenario": {"kind": "stale_just_over", "age_days": 91}, "currency": "EUR"},
    {"case_id": "t1_es_7", "lang": "ES", "tier": 1,
     "scenario": {"kind": "missing_number", "drop": "number", "currency": "EUR", "age_days": 10},
     "currency": "EUR"},
    {"case_id": "t2_es_0", "lang": "ES", "tier": 2,
     "scenario": {"kind": "clean", "currency_markers": "none"}, "currency": "EUR"},
    *[{"case_id": f"t2_es_{i}", "lang": "ES", "tier": 2, "scenario": {"kind": "clean"},
       "currency": "GBP"} for i in (1, 2)],
    {"case_id": "t2_es_3", "lang": "ES", "tier": 2,
     "scenario": {"kind": "disallowed_currency", "currency": "USD", "age_days": 10},
     "currency": "USD"},
    {"case_id": "t2_es_4", "lang": "ES", "tier": 2,
     "scenario": {"kind": "stale_old", "age_days": 240}, "currency": "EUR"},
    {"case_id": "t2_es_5", "lang": "ES", "tier": 2,
     "scenario": {"kind": "missing_total", "drop": "total", "currency": "EUR", "age_days": 10},
     "currency": "EUR"},
]

PDF_PLAN: list[dict] = [
    # --- EN: 3 t0 / 4 t1 / 3 t2 (single page) -----------------------------------
    {"case_id": "pdf_en_t0_0", "lang": "EN", "tier": 0, "layout": "basic",
     "scenario": {"kind": "clean", "age_days": 15}, "currency": "EUR"},
    {"case_id": "pdf_en_t0_1", "lang": "EN", "tier": 0, "layout": "basic",
     "scenario": {"kind": "clean", "age_days": 40}, "currency": "GBP"},
    {"case_id": "pdf_en_t0_2", "lang": "EN", "tier": 0, "layout": "basic",
     "scenario": {"kind": "stale", "age_days": 180}, "currency": "EUR"},
    {"case_id": "pdf_en_t1_0", "lang": "EN", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 20}, "currency": "EUR"},
    {"case_id": "pdf_en_t1_1", "lang": "EN", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 60}, "currency": "GBP"},
    {"case_id": "pdf_en_t1_2", "lang": "EN", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 25}, "currency": "EUR"},
    {"case_id": "pdf_en_t1_3", "lang": "EN", "tier": 1, "layout": "styled",
     "scenario": {"kind": "missing_date", "drop": "date", "age_days": 10}, "currency": "EUR"},
    {"case_id": "pdf_en_t2_0", "lang": "EN", "tier": 2, "layout": "complex",
     "scenario": {"kind": "clean", "age_days": 30}, "currency": "EUR"},
    {"case_id": "pdf_en_t2_1", "lang": "EN", "tier": 2, "layout": "complex",
     "scenario": {"kind": "clean", "currency_markers": "none", "age_days": 25}, "currency": "GBP"},
    {"case_id": "pdf_en_t2_2", "lang": "EN", "tier": 2, "layout": "complex",
     "scenario": {"kind": "disallowed_currency", "currency": "USD", "age_days": 10},
     "currency": "USD"},
    # --- ES: 3 t0 / 4 t1 / 3 t2 (single page) -----------------------------------
    {"case_id": "pdf_es_t0_0", "lang": "ES", "tier": 0, "layout": "basic",
     "scenario": {"kind": "clean", "age_days": 12}, "currency": "EUR"},
    {"case_id": "pdf_es_t0_1", "lang": "ES", "tier": 0, "layout": "basic",
     "scenario": {"kind": "clean", "age_days": 55}, "currency": "GBP"},
    {"case_id": "pdf_es_t0_2", "lang": "ES", "tier": 0, "layout": "basic",
     "scenario": {"kind": "stale", "age_days": 210}, "currency": "EUR"},
    {"case_id": "pdf_es_t1_0", "lang": "ES", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 20}, "currency": "EUR"},
    {"case_id": "pdf_es_t1_1", "lang": "ES", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 70}, "currency": "GBP"},
    {"case_id": "pdf_es_t1_2", "lang": "ES", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "age_days": 33}, "currency": "EUR"},
    {"case_id": "pdf_es_t1_3", "lang": "ES", "tier": 1, "layout": "styled",
     "scenario": {"kind": "missing_date", "drop": "date", "age_days": 10}, "currency": "EUR"},
    {"case_id": "pdf_es_t2_0", "lang": "ES", "tier": 2, "layout": "complex",
     "scenario": {"kind": "clean", "age_days": 18}, "currency": "EUR"},
    {"case_id": "pdf_es_t2_1", "lang": "ES", "tier": 2, "layout": "complex",
     "scenario": {"kind": "clean", "currency_markers": "none", "age_days": 45}, "currency": "GBP"},
    {"case_id": "pdf_es_t2_2", "lang": "ES", "tier": 2, "layout": "complex",
     "scenario": {"kind": "disallowed_currency", "currency": "USD", "age_days": 10},
     "currency": "USD"},
]

# Manifest fragment contract (written by each wave; merged by the integration wave):
#   manifest_txt.json  -> {"lane": "txt",  "cases": [{case_id, language, tier, scenario,
#                          expected_verdict, txt_sha256, formats: ["txt"]}]}
#   manifest_pdf.json  -> {"lane": "pdf",  "cases": [{case_id, language, tier, scenario,
#                          expected_verdict, pdf_sha256, pages: 1, formats: ["pdf"]}]}
# expected.json contract (unchanged from v1):
#   {"expected_fields": {6 fields, null when absent}, "expected_verdict_status": ...,
#    "slices": {language, tier, amount_style, date_style, scenario, format, pages?}}
MANIFEST_FRAGMENT_FILES = ("manifest_txt.json", "manifest_pdf.json")

# ---------------------------------------------------------------------------
# Degenerate / failure-mode fixtures (dataset v2.1)
# ---------------------------------------------------------------------------
# No business content: they probe failure modes (empty input, garbage text) and
# the "when present" semantics of optional fields (tax_id absent -> PASS).
# Truth contract: empty/garbage -> all 6 fields None, verdict REVIEW;
# no_vat -> all business fields present, tax_id None, verdict PASS.
DEGENERATE_TXT_PLAN: list[dict] = [
    {"case_id": "x_txt_empty", "lang": "EN", "tier": 0, "scenario": {"kind": "empty"}},
    {"case_id": "x_txt_garbage", "lang": "EN", "tier": 2, "scenario": {"kind": "garbage"}},
    {"case_id": "x_txt_no_vat", "lang": "ES", "tier": 0,
     "scenario": {"kind": "clean", "no_vat": True, "age_days": 12}, "currency": "EUR"},
]
DEGENERATE_PDF_PLAN: list[dict] = [
    {"case_id": "x_pdf_empty", "lang": "EN", "tier": 0, "layout": "basic",
     "scenario": {"kind": "empty"}},
    {"case_id": "x_pdf_garbage", "lang": "ES", "tier": 2, "layout": "basic",
     "scenario": {"kind": "garbage"}},
    {"case_id": "x_pdf_no_vat", "lang": "EN", "tier": 1, "layout": "styled",
     "scenario": {"kind": "clean", "no_vat": True, "age_days": 20}, "currency": "EUR"},
]
# Garbage corpus (noise lines WITHOUT any canonical label, currency marker, or
# parseable date token; digits may appear but never next to money labels):
GARBAGE_TOKENS = (
    "zzx qvort", "@@@ ####", "n/a ---", "###", "lorem ipsum dolor", "%%%",
    "ref 0042-aux", ".....", "blue red green", "??",
)
