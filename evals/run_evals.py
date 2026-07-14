from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analysis.contracts import DocumentIdentity
from app.analysis.domain.retrieval import ParameterRetrievalEngine
from app.database import Database


ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "cases" / "synthetic_smoke.jsonl"


def load_cases(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def run(cases_path: Path, top_k: int) -> dict:
    cases = load_cases(cases_path)
    with TemporaryDirectory() as temp_dir:
        db = Database(str(Path(temp_dir) / "eval.db"))
        db.ensure_mapping()
        db.ensure_parameters()
        from app.analysis.repository import AnalysisRepository

        parameters = AnalysisRepository(db).parameter_index()
        engine = ParameterRetrievalEngine()
        identity = DocumentIdentity("eval.txt", "text/plain", 0, "eval", "text")
        results = []
        hits = 0
        for index, case in enumerate(cases, start=1):
            facts = [
                {
                    "id": index,
                    "fact_key": case["id"],
                    "claim": case["claim"],
                    "fact_type": case.get("fact_type") or "unknown",
                }
            ]
            candidates, _ = engine.run(identity, facts, parameters, limit=top_k)
            predicted = [f"{item['kk_id']}:{item['detail_kode']}" for item in candidates]
            expected = set(case.get("expected_any_of") or [])
            matched = bool(expected & set(predicted))
            hits += int(matched)
            results.append(
                {
                    "id": case["id"],
                    "matched": matched,
                    "expected_any_of": sorted(expected),
                    "predicted": predicted,
                }
            )
    return {
        "dataset": str(cases_path),
        "case_count": len(cases),
        "top_k": top_k,
        "recall_at_k": round(hits / max(1, len(cases)), 4),
        "results": results,
        "dataset_status": "synthetic_smoke_not_domain_gold",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SPIP parameter retrieval evals.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--minimum-recall", type=float, default=0.95)
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.cases, max(1, args.top_k))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.enforce and report["recall_at_k"] < args.minimum_recall:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
