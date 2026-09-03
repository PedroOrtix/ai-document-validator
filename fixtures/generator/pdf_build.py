"""Build or verify the deterministic golden-v2 PDF lane.

uv run python -m fixtures.generator.pdf_build            # build all 20 cases
uv run python -m fixtures.generator.pdf_build --verify   # verify hashes and truth
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from fixtures.generator.pdf_render import (
    RenderCase,
    RenderItem,
    assert_smoke,
    pdf_smoke_report,
    render_case_pdf,
)
from fixtures.generator.spec_v2 import (
    AS_OF,
    PDF_PLAN,
    POOLS,
    case_rng,
    expected_verdict,
    format_date_value,
    make_contact,
    make_iban,
    make_invoice_number,
    make_vat,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "fixtures" / "golden"
DATE_STYLES_T0 = ("iso", "dmy")
DATE_STYLES_FULL = ("iso", "dmy", "dotted", "spelled", "abbrev")
AMOUNT_STYLES_T0 = ("dot_decimal", "grouped_eu")
AMOUNT_STYLES_FULL = ("dot_decimal", "comma_decimal", "grouped_eu", "grouped_en", "space_fr")


def build_case(plan_row: dict[str, Any]) -> tuple[RenderCase, dict[str, Any]]:
    """Derive one PDF case and its truth solely from the frozen plan and RNG."""
    case_id = plan_row["case_id"]
    language = plan_row["lang"]
    tier = plan_row["tier"]
    layout = plan_row["layout"]
    scenario = plan_row["scenario"]
    rng = case_rng(case_id)
    pool = POOLS[language]

    supplier = rng.choice(pool["seeds"])
    street_template = pool["street"]
    street = street_template.format(name=rng.choice(pool["street_names"]), n=rng.randrange(1, 99))
    city = rng.choice(pool["cities"])
    city_line = f"{city}, {pool['country_word']}"
    phone, email = make_contact(rng, language, supplier)
    bank_name, bic = rng.choice(pool["banks"])
    del bank_name
    iban = make_iban(rng, language)
    invoice_number, number_label = make_invoice_number(rng, language, tier)
    tax_id = make_vat(rng, language, absent=False)

    item_count = rng.randrange(1, 3) if layout == "basic" else rng.randrange(3, 7)
    item_pool = pool["table_items"]
    indices = rng.sample(range(len(item_pool)), item_count)
    items = tuple(
        RenderItem(
            description=item_pool[index][0],
            quantity=item_pool[index][1],
            unit_price=float(item_pool[index][2]),
            amount=float(item_pool[index][1] * item_pool[index][2]),
        )
        for index in indices
    )
    subtotal = sum(item.amount for item in items)
    vat_rate = pool["vat_rate"]
    vat_amount = round(subtotal * vat_rate, 2)
    total_amount = round(subtotal + vat_amount, 2)

    date_style_pool = DATE_STYLES_T0 if tier == 0 else DATE_STYLES_FULL
    date_style = rng.choice(date_style_pool)
    amount_style_pool = AMOUNT_STYLES_T0 if tier == 0 else AMOUNT_STYLES_FULL
    amount_style = rng.choice(amount_style_pool)

    invoice_date = None
    invoice_date_value = None
    if scenario.get("drop") != "date":
        invoice_date_value = AS_OF - timedelta(days=scenario["age_days"])
        invoice_date = format_date_value(invoice_date_value, date_style, language)

    currency = plan_row["currency"]
    currency_markers = scenario.get("currency_markers")
    currency_truth = None if currency_markers == "none" else currency
    total_label = pool["total_labels"][0] if tier == 0 else rng.choice(pool["total_labels"])
    date_label = pool["date_labels"][0] if tier == 0 else rng.choice(pool["date_labels"])
    number_label = pool["number_labels"][0] if tier == 0 else number_label
    tax_label = pool["vat_labels"][0]
    currency_label = pool["currency_labels"][0]
    terms = rng.choice(pool["terms"])
    stamp = rng.choice(pool["stamps"])

    bill_to = f"Alex Fisher, {rng.randrange(1, 99)} Riverside Road"
    ship_to = f"Operations Depot, {rng.randrange(1, 99)} Port Lane"
    order_ref = f"ORD-{rng.randrange(2026, 2027)}-{rng.randrange(1000, 10000)}"
    delivery_note = f"DN-{rng.randrange(100000, 1000000)}"

    render_case = RenderCase(
        case_id=case_id,
        language=language,
        layout=layout,
        supplier=supplier,
        street=street,
        city_line=city_line,
        contact_phone=phone,
        contact_email=email,
        iban=iban,
        bic=bic,
        number_label=number_label,
        invoice_number=invoice_number,
        date_label=date_label,
        invoice_date=invoice_date,
        currency_label=currency_label,
        currency=currency,
        currency_markers=currency_markers,
        tax_label=tax_label,
        tax_id=tax_id,
        items=items,
        subtotal=subtotal,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total_label=total_label,
        total_amount=total_amount,
        amount_style=amount_style,
        terms=terms,
        stamp=stamp,
        order_ref=order_ref,
        delivery_note=delivery_note,
        bill_to=bill_to,
        ship_to=ship_to,
    )

    required_missing = scenario.get("drop") in {"date", "total"}
    expected_fields = {
        "supplier_name": supplier,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date_value.isoformat() if invoice_date_value is not None else None,
        "total_amount": total_amount,
        "currency": currency_truth,
        "tax_id": tax_id,
    }
    verdict = expected_verdict(
        invoice_date_value,
        total_amount,
        currency_truth,
        required_missing,
    )
    expected = {
        "expected_fields": expected_fields,
        "expected_verdict_status": verdict,
        "slices": {
            "language": language,
            "tier": tier,
            "amount_style": amount_style,
            "date_style": date_style,
            "scenario": scenario.get("scenario", scenario.get("kind", "random")),
            "format": "pdf",
            "pages": 1,
        },
    }
    return render_case, expected


def build_all() -> list[dict[str, Any]]:
    """Render every PDF_PLAN row, write truth files, and return manifest cases."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    manifest_cases: list[dict[str, Any]] = []
    for plan_row in PDF_PLAN:
        case, expected = build_case(plan_row)
        pdf_bytes = render_case_pdf(case)
        smoke = pdf_smoke_report(pdf_bytes, case)
        assert_smoke(smoke, case)

        pdf_path = GOLDEN_DIR / f"{case.case_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        (GOLDEN_DIR / f"{case.case_id}.expected.json").write_text(
            json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest_cases.append(
            {
                "case_id": case.case_id,
                "language": case.language,
                "tier": case.layout and plan_row["tier"],
                "scenario": expected["slices"]["scenario"],
                "expected_verdict": expected["expected_verdict_status"],
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "pages": 1,
                "formats": ["pdf"],
            }
        )
    manifest_cases.sort(key=lambda entry: entry["case_id"])
    manifest = {"lane": "pdf", "cases": manifest_cases}
    (GOLDEN_DIR / "manifest_pdf.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest_cases


def verify_all() -> int:
    """Re-derive hashes and truth, returning the number of drift problems."""
    manifest_path = GOLDEN_DIR / "manifest_pdf.json"
    if not manifest_path.is_file():
        print("manifest_pdf.json is missing")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(entry["case_id"] for entry in manifest["cases"]) != {row["case_id"] for row in PDF_PLAN}:
        print("manifest_pdf.json case set differs from PDF_PLAN")
        return 1

    problems: list[str] = []
    plan_by_id = {row["case_id"]: row for row in PDF_PLAN}
    for entry in manifest["cases"]:
        case, expected = build_case(plan_by_id[entry["case_id"]])
        pdf_path = GOLDEN_DIR / f"{entry['case_id']}.pdf"
        expected_path = GOLDEN_DIR / f"{entry['case_id']}.expected.json"
        if not pdf_path.is_file() or not expected_path.is_file():
            problems.append(f"{entry['case_id']}: artifact missing")
            continue
        pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if pdf_hash != entry["pdf_sha256"]:
            problems.append(f"{entry['case_id']}: pdf hash drift")
        on_disk = json.loads(expected_path.read_text(encoding="utf-8"))
        if on_disk != expected:
            problems.append(f"{entry['case_id']}: expected truth drift")
        smoke = pdf_smoke_report(pdf_path.read_bytes(), case)
        try:
            assert_smoke(smoke, case)
        except RuntimeError as exc:
            problems.append(str(exc))
    for problem in problems:
        print(problem)
    print(f"verify: {len(problems)} problems over {len(manifest['cases'])} pdf cases")
    return len(problems)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check drift instead of writing")
    args = parser.parse_args()
    if args.verify:
        raise SystemExit(1 if verify_all() else 0)
    cases = build_all()
    print(f"built {len(cases)} pdf cases")


if __name__ == "__main__":
    main()
