"""Build the deterministic TXT lane for golden dataset v2.

The frozen case matrix and all truth helpers are imported from ``spec_v2``.
This module only renders those rows and verifies that the checked-in fixtures
still match a fresh derivation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fixtures.generator import spec_v2

GENERATOR_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = GENERATOR_DIR.parent / "golden"
MANIFEST_NAME = "manifest_txt.json"


@dataclass(frozen=True)
class RenderedCase:
    case_id: str
    text: str
    expected: dict[str, Any]
    manifest_case: dict[str, Any]


def _item_pool(lang: str) -> tuple[str, ...]:
    return spec_v2.POOLS[lang]["items"]


def _table_items(lang: str) -> tuple[tuple[str, int, int], ...]:
    return spec_v2.POOLS[lang]["table_items"]


def _format_money(value: float, style: str) -> str:
    return spec_v2.format_amount(value, style)


def _currency_display(currency: str) -> str:
    return spec_v2.SYMBOLS[currency]


def _supplier(rng, lang: str) -> tuple[str, str, str, str]:
    """Return supplier, street, city, country."""
    pool = spec_v2.POOLS[lang]
    supplier = rng.choice(pool["seeds"])
    street = pool["street"].format(
        name=rng.choice(pool["street_names"]), n=rng.randrange(1, 99)
    )
    city = rng.choice(pool["cities"])
    return supplier, street, city, pool["country_word"]


def _lines(*sections: list[str]) -> str:
    text = "\n\n".join("\n".join(section) for section in sections)
    return text + "\n"


def _render_t0(
    row: dict[str, Any], rng: random.Random
) -> tuple[str, dict[str, Any]]:
    lang = row["lang"]
    scenario = row["scenario"]
    currency = row["currency"]
    supplier, street, city, country = _supplier(rng, lang)
    phone, email = spec_v2.make_contact(rng, lang, supplier)
    pool = spec_v2.POOLS[lang]

    invoice_number, number_label = spec_v2.make_invoice_number(rng, lang, 0)
    date_style = rng.choice(spec_v2.DATE_STYLES_T0)
    amount_style = rng.choice(spec_v2.AMOUNT_STYLES_T0)
    if "age_days" in scenario:
        invoice_date = spec_v2.AS_OF - timedelta(days=scenario["age_days"])
    else:
        invoice_date = spec_v2.AS_OF - timedelta(days=rng.randrange(5, 81))
    amount = scenario.get("amount", round(rng.uniform(80, 20000) + rng.random(), 2))
    tax_id = spec_v2.make_vat(rng, lang, absent=False)

    item_count = rng.randrange(1, 3)
    item_names = rng.sample(_item_pool(lang), item_count)
    if item_count == 1:
        item_amounts = [amount]
    else:
        first = round(amount * rng.uniform(0.35, 0.65), 2)
        item_amounts = [first, round(amount - first, 2)]

    currency_label = pool["currency_labels"][0]
    date_label = pool["date_labels"][0]
    total_label = pool["total_labels"][0]
    vat_label = pool["vat_labels"][0]

    header = [supplier, street, f"{city}, {country}", phone, email]
    fields = [
        f"{number_label}: {invoice_number}",
        f"{date_label}: {spec_v2.format_date_value(invoice_date, date_style, lang)}",
        f"{currency_label}: {currency}",
        f"{vat_label}: {tax_id}",
    ]
    items = [
        f"{name}    {_format_money(value, amount_style)}"
        for name, value in zip(item_names, item_amounts, strict=True)
    ]
    totals = [f"{total_label}    {_format_money(amount, amount_style)}"]
    text = _lines(header, fields, items, totals)
    expected = {
        "supplier_name": supplier,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date.isoformat(),
        "total_amount": amount,
        "currency": currency,
        "tax_id": tax_id,
    }
    return text, expected


def _item_amounts(amount: float, count: int, rng: random.Random) -> list[float]:
    """Split a two-decimal amount into exactly ``count`` non-negative parts."""
    total_cents = round(amount * 100)
    base, remainder = divmod(total_cents, count)
    extra_positions = set(rng.sample(range(count), remainder))
    return [
        (base + (1 if position in extra_positions else 0)) / 100
        for position in range(count)
    ]


def _render_t1(
    row: dict[str, Any], rng: random.Random
) -> tuple[str, dict[str, Any]]:
    lang = row["lang"]
    tier = row["tier"]
    index = int(row["case_id"].rsplit("_", 1)[-1])
    scenario = row["scenario"]
    currency = row["currency"]
    pool = spec_v2.POOLS[lang]
    supplier, street, city, country = _supplier(rng, lang)
    phone, email = spec_v2.make_contact(rng, lang, supplier)

    if "age_days" in scenario:
        invoice_date = spec_v2.AS_OF - timedelta(days=scenario["age_days"])
    else:
        invoice_date = spec_v2.AS_OF - timedelta(days=rng.randrange(5, 81))
    amount = scenario.get("amount", round(rng.uniform(500, 24000) + rng.random(), 2))
    tax_id = spec_v2.make_vat(rng, lang, absent=False)

    if index < 5 and scenario["kind"] == "clean":
        date_style = spec_v2.DATE_STYLES_FULL[index]
        amount_style = spec_v2.AMOUNT_STYLES_FULL[index]
    else:
        date_style = "iso"
        amount_style = "dot_decimal"

    invoice_number, number_label = spec_v2.make_invoice_number(rng, lang, tier)
    date_label = pool["date_labels"][1]
    total_label = pool["total_labels"][1]
    vat_label = pool["vat_labels"][1]
    bank_name, bic = pool["banks"][index % len(pool["banks"])]
    iban = spec_v2.make_iban(rng, lang)
    terms = pool["terms"][index % len(pool["terms"])]

    currency_line: str | None = None
    marker_prefix = ""
    marker_suffix = ""
    if scenario["kind"] == "disallowed_currency":
        currency_line = f"{pool['currency_labels'][0]}: {currency}"
    else:
        marker_roll = rng.random()
        if marker_roll < 0.45:
            marker_prefix = f"{_currency_display(currency)} "
        elif marker_roll < 0.9:
            marker_suffix = f" {currency}"
        else:
            currency_line = f"{rng.choice(pool['currency_labels'])}: {currency}"

    item_count = rng.randrange(3, 7)
    item_names = rng.sample(_item_pool(lang), item_count)
    item_amounts = _item_amounts(amount, item_count, rng)
    if amount_style in {"dot_decimal", "grouped_en"}:
        item_styles = [amount_style] * item_count
    else:
        item_styles = ["dot_decimal"] * item_count

    header = [supplier, street, f"{city}, {country}", phone, email]
    fields = [
        f"{number_label}: {invoice_number}",
        f"{date_label}: {spec_v2.format_date_value(invoice_date, date_style, lang)}",
    ]
    if currency_line is not None:
        fields.append(currency_line)
    fields.append(f"{vat_label}: {tax_id}")
    items = [
        f"{name}    {_format_money(value, item_style)}"
        for name, value, item_style in zip(
            item_names, item_amounts, item_styles, strict=True
        )
    ]
    total_text = f"{total_label}    {marker_prefix}{_format_money(amount, amount_style)}"
    total_text += marker_suffix
    totals = [total_text, "VAT    0.00", "Rounding    0.00"]
    bank = [f"Bank: {bank_name}", f"IBAN: {iban}", f"BIC: {bic}"]
    text = _lines(header, fields, items, totals, bank, [terms])
    expected = {
        "supplier_name": supplier,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date.isoformat(),
        "total_amount": amount,
        "currency": currency,
        "tax_id": tax_id,
    }
    return text, expected


def _render_t2(
    row: dict[str, Any], rng: random.Random
) -> tuple[str, dict[str, Any]]:
    lang = row["lang"]
    scenario = row["scenario"]
    currency = row["currency"]
    pool = spec_v2.POOLS[lang]
    supplier, street, city, country = _supplier(rng, lang)
    phone, email = spec_v2.make_contact(rng, lang, supplier)

    if "age_days" in scenario:
        invoice_date = spec_v2.AS_OF - timedelta(days=scenario["age_days"])
    else:
        invoice_date = spec_v2.AS_OF - timedelta(days=rng.randrange(5, 81))
    amount = round(rng.uniform(900, 32000) + rng.random(), 2)
    tax_id = spec_v2.make_vat(rng, lang, absent=False)

    index = int(row["case_id"].rsplit("_", 1)[-1])
    date_styles = spec_v2.DATE_STYLES_FULL
    amount_styles = (
        "comma_decimal", "space_fr", "space_fr",
        "dot_decimal", "grouped_eu", "dot_decimal",
    )
    date_style = date_styles[index % len(date_styles)]
    amount_style = amount_styles[index]

    invoice_number, number_label = spec_v2.make_invoice_number(rng, lang, 2)
    date_label = pool["date_labels"][-1]
    total_label = pool["total_labels"][-1]
    vat_label = pool["vat_labels"][-1]
    from_label = pool["from_labels"][-1]
    supplier_words = supplier.split(" ")
    supplier_line_1 = " ".join(supplier_words[:2])
    supplier_line_2 = " ".join(supplier_words[2:])

    order_ref = f"ORD-{rng.randrange(2024, 2027)}-{rng.randrange(1000, 9999)}"
    delivery_note = f"DN-{rng.randrange(1000, 9999)}"
    subtotal = round(amount * 0.82, 2)
    vat_amount = round(amount - subtotal, 2)
    distractor_labels = pool["distractor_labels"]
    distractor_lines = [
        f"{distractor_labels[0]}    {_format_money(subtotal, amount_style)}",
        f"{distractor_labels[1]}    {_format_money(vat_amount, amount_style)}",
        f"{distractor_labels[4]}    {_format_money(subtotal, amount_style)}",
    ]

    header = [
        from_label,
        supplier_line_1,
        supplier_line_2,
        street,
        f"{city}, {country}",
    ]
    bill_to_label = "Bill to:" if lang == "EN" else "Cliente:"
    bill_city = rng.choice([c for c in pool["cities"] if c != city])
    bill_to = [bill_to_label, "Consignee Group", "12 Commerce Avenue", bill_city, country]
    fields = [
        f"{number_label}: {invoice_number}",
        f"{date_label}: {spec_v2.format_date_value(invoice_date, date_style, lang)}",
        f"Order Ref: {order_ref}",
        f"Delivery Note: {delivery_note}",
    ]
    markers = scenario.get("currency_markers")
    if markers == "none":
        total_line = f"{total_label}    {_format_money(amount, amount_style)}"
    else:
        total_line = (
            f"{total_label}    {_currency_display(currency)} "
            f"{_format_money(amount, amount_style)}"
        )
    totals = [*distractor_lines, total_line]
    footer = [f"{vat_label}: {tax_id}", phone, email]
    text = _lines(header, bill_to, fields, totals, footer)
    expected = {
        "supplier_name": supplier,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date.isoformat(),
        "total_amount": amount,
        "currency": currency if markers != "none" else None,
        "tax_id": tax_id,
    }
    return text, expected


def build_case(row: dict[str, Any]) -> RenderedCase:
    """Render one frozen TXT row and derive all truth from the spec."""
    rng = spec_v2.case_rng(row["case_id"])
    tier = row["tier"]
    if tier == 0:
        text, fields = _render_t0(row, rng)
    elif tier == 1:
        text, fields = _render_t1(row, rng)
    else:
        text, fields = _render_t2(row, rng)

    drop = row["scenario"].get("drop")
    if drop == "number":
        dropped_value = str(fields["invoice_number"])
        text = "\n".join(
            line for line in text.splitlines() if dropped_value not in line
        ) + "\n"
        fields["invoice_number"] = None
    elif drop == "date":
        dropped_value = str(fields["invoice_date"])
        text = "\n".join(
            line for line in text.splitlines() if dropped_value not in line
        ) + "\n"
        fields["invoice_date"] = None
    elif drop == "total":
        amount_style = _case_amount_style(row["case_id"], row["tier"])
        dropped_value = _format_money(float(fields["total_amount"]), amount_style)
        text = "\n".join(
            line for line in text.splitlines() if dropped_value not in line
        ) + "\n"
        fields["total_amount"] = None

    required_missing = any(
        fields[field] is None
        for field in ("supplier_name", "invoice_number", "invoice_date", "total_amount")
    )
    verdict = spec_v2.expected_verdict(
        date.fromisoformat(fields["invoice_date"]) if fields["invoice_date"] else None,
        fields["total_amount"],
        fields["currency"],
        required_missing,
    )
    scenario = row["scenario"]
    expected = {
        "expected_fields": fields,
        "expected_verdict_status": verdict,
        "slices": {
            "language": row["lang"],
            "tier": tier,
            "amount_style": _case_amount_style(row["case_id"], tier),
            "date_style": _case_date_style(row["case_id"], tier),
            "scenario": scenario["kind"],
            "format": "txt",
        },
    }
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_case = {
        "case_id": row["case_id"],
        "language": row["lang"],
        "tier": tier,
        "scenario": scenario["kind"],
        "expected_verdict": verdict,
        "txt_sha256": text_hash,
        "formats": ["txt"],
    }
    return RenderedCase(row["case_id"], text, expected, manifest_case)


def _case_amount_style(case_id: str, tier: int) -> str:
    index = int(case_id.rsplit("_", 1)[-1])
    if tier == 0:
        return spec_v2.AMOUNT_STYLES_T0[index % 2]
    if tier == 1:
        if index < 5:
            return spec_v2.AMOUNT_STYLES_FULL[index]
        return "dot_decimal"
    return (
        "comma_decimal", "space_fr", "space_fr",
        "dot_decimal", "grouped_eu", "dot_decimal",
    )[index]


def _case_date_style(case_id: str, tier: int) -> str:
    index = int(case_id.rsplit("_", 1)[-1])
    if tier == 0:
        return spec_v2.DATE_STYLES_T0[index % 2]
    if tier == 1:
        if index < 5:
            return spec_v2.DATE_STYLES_FULL[index]
        return "iso"
    return spec_v2.DATE_STYLES_FULL[index % len(spec_v2.DATE_STYLES_FULL)]


def build_all() -> list[RenderedCase]:
    return [build_case(row) for row in spec_v2.TXT_PLAN]


def _write_cases(cases: list[RenderedCase]) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for case in cases:
        (GOLDEN_DIR / f"{case.case_id}.txt").write_text(case.text, encoding="utf-8")
        (GOLDEN_DIR / f"{case.case_id}.expected.json").write_text(
            json.dumps(case.expected, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    manifest = {
        "lane": "txt",
        "cases": sorted(
            (case.manifest_case for case in cases),
            key=lambda entry: entry["case_id"],
        ),
    }
    (GOLDEN_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def verify(cases: list[RenderedCase]) -> list[str]:
    problems: list[str] = []
    for case in cases:
        txt_path = GOLDEN_DIR / f"{case.case_id}.txt"
        expected_path = GOLDEN_DIR / f"{case.case_id}.expected.json"
        if txt_path.read_text(encoding="utf-8") != case.text:
            problems.append(f"{case.case_id}: TXT drift")
        if expected_path.read_text(encoding="utf-8") != json.dumps(
            case.expected, indent=2, ensure_ascii=False
        ) + "\n":
            problems.append(f"{case.case_id}: expected JSON drift")
    manifest_path = GOLDEN_DIR / MANIFEST_NAME
    manifest = {
        "lane": "txt",
        "cases": sorted(
            (case.manifest_case for case in cases),
            key=lambda entry: entry["case_id"],
        ),
    }
    if manifest_path.read_text(encoding="utf-8") != json.dumps(
        manifest, indent=2, ensure_ascii=False
    ) + "\n":
        problems.append("manifest_txt.json: drift")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify golden v2 TXT fixtures")
    parser.add_argument("--verify", action="store_true", help="verify existing fixture bytes")
    args = parser.parse_args()
    cases = build_all()
    if args.verify:
        problems = verify(cases)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    _write_cases(cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
