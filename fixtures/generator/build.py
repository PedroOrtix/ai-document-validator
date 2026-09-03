"""Build or verify the consolidated golden v2 dataset.

    uv run python -m fixtures.generator.build            # regenerate + write manifests
    uv run python -m fixtures.generator.build --verify   # re-derive truth + hashes
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from fixtures.generator import pdf_build, txt_build

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "fixtures" / "golden"
MANIFEST_NAME = "manifest.json"


def _write_manifest(cases: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    txt_cases = sorted(cases["txt"], key=lambda entry: entry["case_id"])
    pdf_cases = sorted(cases["pdf"], key=lambda entry: entry["case_id"])
    manifest = {
        "generator": "fixtures.generator v2",
        "as_of": "2026-09-03",
        "max_age_days": 90,
        "allowed_currencies": ["EUR", "GBP"],
        "counts": {"txt": len(txt_cases), "pdf": len(pdf_cases)},
        "txt_cases": txt_cases,
        "pdf_cases": pdf_cases,
    }
    (GOLDEN_DIR / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def _allowed_filenames(cases: dict[str, list[dict[str, Any]]]) -> set[str]:
    allowed = {MANIFEST_NAME, "manifest_txt.json", "manifest_pdf.json"}
    for case in cases["txt"]:
        allowed.update({f"{case['case_id']}.txt", f"{case['case_id']}.expected.json"})
    for case in cases["pdf"]:
        allowed.update({f"{case['case_id']}.pdf", f"{case['case_id']}.expected.json"})
    return allowed


def _remove_orphans(cases: dict[str, list[dict[str, Any]]]) -> None:
    allowed = _allowed_filenames(cases)
    for path in GOLDEN_DIR.iterdir():
        if path.is_file() and path.name not in allowed:
            path.unlink()


def write_dataset() -> dict[str, Any]:
    txt_build._write_cases(txt_build.build_all())
    pdf_cases = pdf_build.build_all()
    txt_cases = json.loads((GOLDEN_DIR / "manifest_txt.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    cases = {"txt": txt_cases, "pdf": pdf_cases}
    _remove_orphans(cases)
    return _write_manifest(cases)


def _verify_merged_manifest(cases: dict[str, list[dict[str, Any]]]) -> list[str]:
    problems: list[str] = []
    manifest = json.loads((GOLDEN_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected = {
        "generator": "fixtures.generator v2",
        "as_of": "2026-09-03",
        "max_age_days": 90,
        "allowed_currencies": ["EUR", "GBP"],
        "counts": {"txt": len(cases["txt"]), "pdf": len(cases["pdf"])},
        "txt_cases": sorted(cases["txt"], key=lambda entry: entry["case_id"]),
        "pdf_cases": sorted(cases["pdf"], key=lambda entry: entry["case_id"]),
    }
    if manifest != expected:
        problems.append("manifest.json: drift")

    orphans = {path.name for path in GOLDEN_DIR.iterdir() if path.is_file()} - _allowed_filenames(
        cases
    )
    if orphans:
        problems.append(f"orphan files: {', '.join(sorted(orphans))}")

    for case in cases["txt"]:
        path = GOLDEN_DIR / f"{case['case_id']}.txt"
        if hashlib.sha256(path.read_bytes()).hexdigest() != case["txt_sha256"]:
            problems.append(f"{case['case_id']}: merged manifest hash drift")
    for case in cases["pdf"]:
        path = GOLDEN_DIR / f"{case['case_id']}.pdf"
        if hashlib.sha256(path.read_bytes()).hexdigest() != case["pdf_sha256"]:
            problems.append(f"{case['case_id']}: merged manifest hash drift")
        expected = GOLDEN_DIR / f"{case['case_id']}.expected.json"
        if not expected.is_file():
            problems.append(f"{case['case_id']}: expected.json missing")
    return problems


def verify_dataset() -> int:
    txt_cases = txt_build.build_all()
    txt_problems = txt_build.verify(txt_cases)
    pdf_cases: list[dict[str, Any]] = []
    pdf_problems: list[str] = []
    manifest_path = GOLDEN_DIR / "manifest_pdf.json"
    if not manifest_path.is_file():
        pdf_problems.append("manifest_pdf.json: missing")
    else:
        try:
            pdf_problems.extend(
                problem
                for problem in [f"{pdf_build.verify_all()}"] if problem != "0"
            )
            pdf_cases = json.loads(manifest_path.read_text(encoding="utf-8"))["cases"]
        except (json.JSONDecodeError, KeyError):
            pdf_problems.append("manifest_pdf.json: invalid")

    cases = {"txt": [case.manifest_case for case in txt_cases], "pdf": pdf_cases}
    problems = txt_problems + pdf_problems + _verify_merged_manifest(cases)
    for problem in problems:
        print(problem)
    print(
        f"verify: {len(problems)} problems over "
        f"{len(cases['txt'])} txt + {len(cases['pdf'])} pdf cases"
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
