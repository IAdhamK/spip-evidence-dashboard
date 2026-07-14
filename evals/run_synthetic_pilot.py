from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.analysis.orchestrator import AnalysisOrchestrator
from app.config import Settings
from app.database import Database


CASES = (
    {
        "id": "complete-context",
        "file_name": "complete.txt",
        "content_type": "text/plain",
        "payload": b"Kebijakan indikator Ditjen PDP ditetapkan tahun 2026. Pelaksanaan indikator Ditjen PDP dilaksanakan tahun 2026. Evaluasi indikator Ditjen PDP dilakukan berkala tahun 2026.",
    },
    {
        "id": "missing-organization",
        "file_name": "missing-org.txt",
        "content_type": "text/plain",
        "payload": b"Kebijakan indikator ditetapkan tahun 2026 dan evaluasi dilakukan berkala tahun 2026.",
    },
    {
        "id": "template-only",
        "file_name": "template.txt",
        "content_type": "text/plain",
        "payload": b"Petunjuk pengisian: isi nama kegiatan, periode, dan hasil evaluasi pada kolom yang tersedia.",
    },
    {
        "id": "mixed-period",
        "file_name": "mixed-period.txt",
        "content_type": "text/plain",
        "payload": b"Kebijakan Ditjen PDP ditetapkan tahun 2025. Evaluasi Ditjen PDP dilaksanakan berkala tahun 2026.",
    },
    {
        "id": "invalid-pdf-signature",
        "file_name": "invalid.pdf",
        "content_type": "application/pdf",
        "payload": b"not-a-real-pdf",
    },
)


def run() -> dict:
    with TemporaryDirectory() as directory:
        path = str(Path(directory) / "pilot.db")
        db = Database(path)
        db.ensure_mapping()
        db.ensure_parameters()
        settings = Settings(
            _env_file=None,
            database_path=path,
            analysis_structured_model_enabled=False,
            analysis_model_verifier_enabled=False,
            vision_analysis_enabled=False,
            smart_upload_allow_real_upload=False,
        )
        orchestrator = AnalysisOrchestrator(db, settings)
        results = []
        for case in CASES:
            snapshot = orchestrator.start(
                file_name=case["file_name"],
                content_type=case["content_type"],
                payload=case["payload"],
                analysis_mode="full_audit",
            )
            run_record = snapshot["run"]
            results.append(
                {
                    "id": case["id"],
                    "status": run_record["status"],
                    "coverage_status": run_record["coverage_status"],
                    "primary_blocked": bool(run_record["primary_blocked"]),
                    "mapping_count": len(snapshot.get("mappings") or []),
                    "security_finding_count": len(snapshot.get("security_findings") or []),
                    "block_reasons": run_record.get("block_reasons") or [],
                }
            )
    unsafe_open = [item["id"] for item in results if not item["primary_blocked"]]
    return {
        "dataset_status": "synthetic_pilot_not_production_signoff",
        "promotion_authority": False,
        "case_count": len(results),
        "all_primary_blocked": not unsafe_open,
        "unsafe_open_cases": unsafe_open,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a fail-closed synthetic pilot harness.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()
    report = run()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if args.enforce and not report["all_primary_blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
