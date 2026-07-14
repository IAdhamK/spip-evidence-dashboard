from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_CASE_FIELDS = {
    "id", "claim", "expected_any_of", "labelled_by", "labelled_at",
    "case_type", "source_location_expected",
}
ALLOWED_CASE_TYPES = {"positive", "negative", "edge", "adversarial", "historical_failure"}


def validate(path: Path, minimum_cases: int = 50) -> list[str]:
    errors: list[str] = []
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: JSON tidak valid: {exc}")
            continue
        missing = REQUIRED_CASE_FIELDS - set(case)
        if missing:
            errors.append(f"line {line_number}: field wajib hilang: {sorted(missing)}")
        if case.get("case_type") not in ALLOWED_CASE_TYPES:
            errors.append(f"line {line_number}: case_type tidak valid")
        if not str(case.get("labelled_by") or "").strip():
            errors.append(f"line {line_number}: labelled_by wajib diisi expert reviewer")
        elif any(
            marker in str(case.get("labelled_by") or "").strip().lower()
            for marker in ("synthetic", "bootstrap", "generator", "automated")
        ):
            errors.append(f"line {line_number}: label otomatis tidak dapat dianggap expert gold")
        if not isinstance(case.get("expected_any_of"), list):
            errors.append(f"line {line_number}: expected_any_of harus list")
        cases.append(case)
    if len(cases) < minimum_cases:
        errors.append(f"gold dataset baru {len(cases)} kasus; minimum {minimum_cases}")
    identifiers = [str(case.get("id") or "") for case in cases]
    if len(identifiers) != len(set(identifiers)):
        errors.append("id kasus gold harus unik")
    present_types = {case.get("case_type") for case in cases}
    missing_types = ALLOWED_CASE_TYPES - present_types
    if missing_types:
        errors.append(f"gold dataset belum mencakup tipe: {sorted(missing_types)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate expert-labelled SPIP gold eval dataset.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--minimum-cases", type=int, default=50)
    args = parser.parse_args()
    errors = validate(args.path, max(1, args.minimum_cases))
    print(json.dumps({"path": str(args.path), "valid": not errors, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
