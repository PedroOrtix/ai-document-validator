"""Build (or verify) the deterministic golden dataset from generator pools.

    uv run python -m fixtures.generator.build            # regenerate + write manifest
    uv run python -m fixtures.generator.build --verify   # re-derive truth + hashes, fail on drift

Dataset layout (fixed evaluation set, ~58 txt + 12 pdf):
    fixtures/golden/<case_id>.txt / .pdf / .expected.json + manifest.json

Tiers (difficulty, no exotic edge cases):
    0 canonical labels, iso/numeric dates, plain amounts
    1 label variants, all date styles, grouped/symbol amounts
    2 rare label variants, unlabeled currencies, mixed formats
    3 forced business-rule scenarios (stale, zero, negative, missing fields,
      disallowed currency) rotated across the 5 languages
PDF cases render a subset through fpdf2 (text layer), incl. two multi-page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fixtures.generator.generator import (  # noqa: E402
    ALLOWED_CURRENCIES,
    AS_OF,
    COUNTRIES,
    DESCRIPTIONS,
    ITEMS,
    MAX_AGE_DAYS,
    currency_tokens,
    format_amount,
    format_date_value,
    make_invoice_number,
    make_vat,
    pick_date,
)

GOLDEN_DIR = ROOT / "fixtures" / "golden"
LANGS = ("EN", "ES", "DE", "FR", "IT")

# Forced tier-3 scenarios: parameter overrides + the verdict they imply.
RULE_SCENARIOS: tuple[dict[str, Any], ...] = (
    {"scenario": "stale_just_over", "age_days": 91},
    {"scenario": "stale_old", "age_days": 240},
    {"scenario": "future_date", "age_days": -20, "currency": "EUR"},
    {"scenario": "zero_amount", "amount": 0.0, "age_days": 10, "currency": "EUR"},
    {"scenario": "negative_amount", "amount": -350.0, "age_days": 10, "currency": "EUR"},
    {"scenario": "missing_number", "drop": "number", "currency": "EUR", "age_days": 10},
    {"scenario": "missing_date", "drop": "date", "currency": "EUR"},
    {"scenario": "missing_total", "drop": "total", "currency": "EUR", "age_days": 10},
    {"scenario": "missing_vat_optional", "no_vat": True, "currency": "EUR", "age_days": 10},
    {"scenario": "disallowed_currency", "currency": "USD", "age_days": 10},
    {"scenario": "unlabeled_currency", "currency_markers": "none", "age_days": 10},
    {"scenario": "all_present_pass", "age_days": 10},
    {"scenario": "everything_ok_eur", "currency": "EUR", "age_days": 30},
)


def expected_verdict(
    invoice_date: date | None,
    amount: float | None,
    currency: str | None,
    required_missing: bool,
) -> str:
    """Derive the truth verdict from the same semantics the engine documents."""
    if invoice_date is not None and (AS_OF - invoice_date).days > MAX_AGE_DAYS:
        return "FAIL"
    if amount is not None and amount <= 0:
        return "FAIL"
    if currency is not None and currency not in ALLOWED_CURRENCIES:
        return "FAIL"
    if required_missing:
        return "REVIEW"
    return "PASS"


def build_case(case_id: str, lang: str, tier: int, scenario: dict[str, Any] | None) -> dict[str, Any]:
    rng = random.Random(f"golden-v1:{case_id}")
    pool = COUNTRIES[lang]
    scenario = scenario or {}

    supplier = rng.choice(pool["seeds"])
    street = pool["street"].format(
        name=rng.choice(pool["street_names"]), n=rng.randrange(1, 99)
    )
    city = rng.choice(pool["cities"])
    item_desc = rng.choice(ITEMS if lang == "EN" else DESCRIPTIONS[lang])

    # --- fields ---------------------------------------------------------
    if "age_days" in scenario:
        invoice_date = AS_OF - timedelta(days=scenario["age_days"])
    else:
        invoice_date, _ = pick_date(rng, tier)
    date_style = rng.choice(("iso", "dmy")) if tier == 0 else rng.choice(
        ("iso", "dmy", "dotted", "spelled", "abbrev")
    )
    if scenario:
        # Rule scenarios must isolate the rule: canonical formats everywhere,
        # so the slice tests the rule, not the parser.
        date_style = "iso"

    currency = scenario.get("currency") if "currency" in scenario else rng.choice(
        ("EUR", "GBP", "EUR", "GBP", "USD", "CHF", "SEK")
    )  # EUR/GBP doublets keep a mostly-allowed mix
    if "amount" in scenario:
        amount = scenario["amount"]  # 0.0 is falsy: never use `or` here
    else:
        amount = round(rng.uniform(80, 20000) + rng.random(), 2)

    drop = scenario.get("drop")
    number: str | None = None
    number_label = ""
    if drop != "number":
        number, number_label = make_invoice_number(rng, lang, tier)
    tax_id = make_vat(rng, lang, bool(scenario.get("no_vat")) or rng.random() < 0.15)

    # --- rendering pieces ----------------------------------------------
    amount_style = rng.choice(("dot_decimal", "grouped_eu")) if tier == 0 else rng.choice(
        ("dot_decimal", "comma_decimal", "grouped_eu", "grouped_en", "space_fr")
    )
    if scenario:
        amount_style = "dot_decimal"  # isolate the rule from amount-format parsing
    if scenario.get("scenario") in {"zero_amount", "negative_amount"}:
        amount_style = rng.choice(("dot_decimal", "grouped_en"))

    markers = scenario.get("currency_markers")
    currency_line: str | None = None
    currency_truth: str | None
    if scenario.get("scenario") == "disallowed_currency":
        # The disallowed currency must be visibly present, or the truth is
        # unextractable rather than disallowed.
        currency_line = f"{rng.choice(pool['currency_labels'])}: {currency}"
        currency_truth = currency
        cur_prefix, cur_suffix = "", ""
    elif markers == "none":
        currency_truth = None
        cur_prefix, cur_suffix = "", ""
    else:
        cur_prefix, cur_suffix, currency_truth = currency_tokens(rng, currency, tier)
        if currency_truth is not None and rng.random() < 0.5:
            cur_prefix, cur_suffix = "", ""  # move it to the labeled line instead
            label = rng.choice(pool["currency_labels"])
            currency_line = f"{label}: {currency}"
    if scenario.get("scenario") == "unlabeled_currency":
        currency_line = None

    date_label = pool["date_labels"][0] if tier == 0 else rng.choice(pool["date_labels"])
    total_labels = pool["total_labels"]
    total_label = total_labels[0] if tier == 0 else rng.choice(total_labels)
    from_label = pool["from_labels"][0] if tier == 0 else rng.choice(pool["from_labels"])
    vat_labels = pool["vat_labels"]
    vat_label = vat_labels[0] if tier == 0 else rng.choice(vat_labels)
    if scenario:
        # Rule scenarios must isolate the rule: canonical labels everywhere,
        # so the slice tests the rule, not label parsing.
        date_label = pool["date_labels"][0]
        total_label = total_labels[0]
        vat_label = vat_labels[0]
        if number is not None:
            number_label = pool["number_labels"][0]
        assert isinstance(currency, str)  # scenarios always pin a currency

    date_str = format_date_value(invoice_date, date_style, lang)

    # --- assemble text ---------------------------------------------------
    lines: list[str] = [supplier, street, f"{city}, {pool['country_word']}", ""]
    if number is not None:
        lines.append(f"{number_label}: {number}")
    if scenario.get("drop") != "date":
        lines.append(f"{date_label}: {date_str}")
    if currency_line:
        lines.append(currency_line)
    if tier >= 2:
        lines.append(f"{from_label}: {supplier}")
    if tax_id is not None:
        lines.append(f"{vat_label}: {tax_id}")
    lines.append("")
    lines.append(item_desc)
    if scenario.get("drop") != "total":
        total_str = format_amount(amount, amount_style)
        lines.append(f"{total_label}    {cur_prefix}{total_str}{cur_suffix}")
    text = "\n".join(lines).strip() + "\n"

    required_missing = (
        number is None
        or scenario.get("drop") in {"date", "total"}
    )
    expected_fields = {
        "supplier_name": supplier,
        "invoice_number": number,
        "invoice_date": invoice_date.isoformat() if scenario.get("drop") != "date" else None,
        "total_amount": None if scenario.get("drop") == "total" else amount,
        "currency": currency_truth if scenario.get("scenario") != "disallowed_currency" else currency,
        "tax_id": tax_id,
    }
    verdict = expected_verdict(
        None if scenario.get("drop") == "date" else invoice_date,
        expected_fields["total_amount"],
        expected_fields["currency"],
        required_missing,
    )

    return {
        "case_id": case_id,
        "text": text,
        "expected_fields": expected_fields,
        "expected_verdict_status": verdict,
        "slices": {
            "language": lang,
            "tier": tier,
            "amount_style": amount_style,
            "date_style": date_style,
            "scenario": scenario.get("scenario", "random") if scenario else "random",
            "format": "txt",
        },
    }


def render_pdf(text: str, *, pages: int = 1) -> bytes:
    """Render text to a single- or multi-page PDF using the text layer.

    Multi-page layout: page 1 carries the supplier block and field labels,
    the last page carries line items and the total (like real invoices).
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    chunks = [c for c in text.split("\n\n") if c]
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        if pages == 1 or len(chunks) < 2:
            pdf.multi_cell(0, 6, text)
        elif i == 0:
            pdf.multi_cell(0, 6, "\n\n".join(chunks[:-1]))
        else:
            pdf.multi_cell(0, 6, chunks[-1])
    return bytes(pdf.output())


def build_plan() -> list[dict[str, Any]]:
    """The fixed case plan: tiers by language plus forced rule scenarios."""
    plan: list[dict[str, Any]] = []
    for tier in (0, 1, 2):
        for lang in LANGS:
            for i in range(3):
                plan.append(
                    {
                        "case_id": f"t{tier}_{lang.lower()}_{i}",
                        "lang": lang,
                        "tier": tier,
                        "scenario": None,
                    }
                )
    for i, scenario in enumerate(RULE_SCENARIOS):
        lang = LANGS[i % len(LANGS)]
        plan.append(
            {
                "case_id": f"t3_{scenario['scenario']}",
                "lang": lang,
                "tier": 3,
                "scenario": scenario,
            }
        )
    return plan


def write_dataset() -> dict[str, Any]:
    plan = build_plan()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []

    for spec in plan:
        case = build_case(spec["case_id"], spec["lang"], spec["tier"], spec["scenario"])
        case_id = case["case_id"]
        txt_path = GOLDEN_DIR / f"{case_id}.txt"
        txt_path.write_text(case["text"], encoding="utf-8")
        (GOLDEN_DIR / f"{case_id}.expected.json").write_text(
            json.dumps(
                {
                    "expected_fields": case["expected_fields"],
                    "expected_verdict_status": case["expected_verdict_status"],
                    "slices": case["slices"],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        entry = {
            "case_id": case_id,
            "language": spec["lang"],
            "tier": spec["tier"],
            "scenario": case["slices"]["scenario"],
            "expected_verdict": case["expected_verdict_status"],
            "txt_sha256": hashlib.sha256(txt_path.read_bytes()).hexdigest(),
            "formats": ["txt"],
        }
        entries.append(entry)

    # --- PDF subset: one representative per language (tier 0/1) + 2 multi-page
    pdf_specs = [
        ("pdf_en_basic", "EN", 0, 1),
        ("pdf_es_basic", "ES", 0, 1),
        ("pdf_de_basic", "DE", 0, 1),
        ("pdf_fr_basic", "FR", 0, 1),
        ("pdf_it_basic", "IT", 0, 1),
        ("pdf_en_styled", "EN", 1, 1),
        ("pdf_es_styled", "ES", 1, 1),
        ("pdf_de_styled", "DE", 1, 1),
        ("pdf_fr_styled", "FR", 1, 1),
        ("pdf_it_styled", "IT", 1, 1),
        ("pdf_multipage_a", "EN", 0, 2),
        ("pdf_multipage_b", "DE", 1, 2),
    ]
    pdf_entries: list[dict[str, Any]] = []
    for case_id, lang, tier, pages in pdf_specs:
        scenario = {"scenario": "multipage", "currency": "EUR", "age_days": 15} if pages > 1 else {
            "currency": "EUR", "age_days": 15
        }
        case = build_case(case_id, lang, tier, scenario)
        pdf_bytes = render_pdf(case["text"], pages=pages)
        (GOLDEN_DIR / f"{case_id}.pdf").write_bytes(pdf_bytes)
        slices = dict(case["slices"])
        slices["format"] = "pdf"
        slices["pages"] = pages
        (GOLDEN_DIR / f"{case_id}.expected.json").write_text(
            json.dumps(
                {
                    "expected_fields": case["expected_fields"],
                    "expected_verdict_status": case["expected_verdict_status"],
                    "slices": slices,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        pdf_entries.append(
            {
                "case_id": case_id,
                "language": lang,
                "tier": tier,
                "scenario": case["slices"]["scenario"],
                "expected_verdict": case["expected_verdict_status"],
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "pages": pages,
                "formats": ["pdf"],
            }
        )

    manifest = {
        "generator": "fixtures.generator v1",
        "as_of": AS_OF.isoformat(),
        "max_age_days": MAX_AGE_DAYS,
        "allowed_currencies": ALLOWED_CURRENCIES,
        "counts": {"txt": len(entries), "pdf": len(pdf_entries)},
        "txt_cases": entries,
        "pdf_cases": pdf_entries,
    }
    (GOLDEN_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def verify_dataset() -> int:
    """Re-derive truth and hashes; return the number of drift problems."""
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in manifest["txt_cases"]:
        scenario = None
        if entry["tier"] == 3:
            scenario = next(s for s in RULE_SCENARIOS if s["scenario"] == entry["scenario"])
        case = build_case(entry["case_id"], entry["language"], entry["tier"], scenario)
        current = hashlib.sha256((GOLDEN_DIR / f"{entry['case_id']}.txt").read_bytes()).hexdigest()
        if current != entry["txt_sha256"]:
            problems.append(f"{entry['case_id']}: txt hash drift")
        on_disk = json.loads(
            (GOLDEN_DIR / f"{entry['case_id']}.expected.json").read_text(encoding="utf-8")
        )
        if on_disk["expected_fields"] != case["expected_fields"]:
            problems.append(f"{entry['case_id']}: expected_fields drift")
        if on_disk["expected_verdict_status"] != case["expected_verdict_status"]:
            problems.append(f"{entry['case_id']}: verdict drift")
    for entry in manifest["pdf_cases"]:
        current = hashlib.sha256((GOLDEN_DIR / f"{entry['case_id']}.pdf").read_bytes()).hexdigest()
        if current != entry["pdf_sha256"]:
            problems.append(f"{entry['case_id']}: pdf hash drift")
    for problem in problems:
        print(problem)
    print(
        f"verify: {len(problems)} problems over "
        f"{len(manifest['txt_cases'])} txt + {len(manifest['pdf_cases'])} pdf cases"
    )
    return len(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check drift instead of writing")
    args = parser.parse_args()
    if args.verify:
        raise SystemExit(1 if verify_dataset() else 0)
    manifest = write_dataset()
    print(f"built {manifest['counts']['txt']} txt + {manifest['counts']['pdf']} pdf cases")


if __name__ == "__main__":
    main()