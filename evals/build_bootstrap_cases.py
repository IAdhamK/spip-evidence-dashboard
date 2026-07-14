from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analysis.repository import AnalysisRepository
from app.database import Database


CASE_TYPES = ("positive", "negative", "edge", "adversarial", "historical_failure")


def build(case_count: int) -> list[dict]:
    with TemporaryDirectory() as directory:
        db = Database(str(Path(directory) / "bootstrap.db"))
        db.ensure_mapping()
        db.ensure_parameters()
        parameters = AnalysisRepository(db).parameter_index()
    cases = []
    for index, parameter in enumerate(parameters[: max(1, case_count)]):
        claim = " ".join(
            value for value in (
                str(parameter.get("uraian") or "").strip(),
                str(parameter.get("cara_pengujian") or "").strip(),
            ) if value
        )
        cases.append(
            {
                "id": f"bootstrap-{index + 1:03d}",
                "claim": claim,
                "fact_type": "unknown",
                "expected_any_of": [f"{parameter['kk_id']}:{parameter['detail_kode']}"],
                "labelled_by": "bootstrap-generator-not-expert",
                "labelled_at": "generated",
                "case_type": CASE_TYPES[index % len(CASE_TYPES)],
                "source_location_expected": {"synthetic": True},
                "dataset_status": "synthetic_bootstrap_not_expert_gold",
            }
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Build non-expert bootstrap regression cases.")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = build(max(1, min(920, args.count)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "case_count": len(cases),
        "dataset_status": "synthetic_bootstrap_not_expert_gold",
        "promotion_authority": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
