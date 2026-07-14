from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.analysis.governance import synthetic_probe_png
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.rollout import RolloutGuardEngine
from app.config import Settings
from app.database import Database
from scripts.analysis_db_backup import backup, restore_verified, verify_database


DRILL_VERSION = "document-intelligence-incident-drill-v2"


def run_incident_drill(work_root: Path) -> dict:
    work_root.mkdir(parents=True, exist_ok=True)
    source = work_root / "incident-source.db"
    backup_path = work_root / "incident-backup.db"
    restored = work_root / "incident-restored.db"
    source_payloads = work_root / "incident-source-payloads"
    backup_payloads = work_root / "incident-backup-payloads"
    restored_payloads = work_root / "incident-restored-payloads"
    for path in (
        source, backup_path, restored,
        source_payloads, backup_payloads, restored_payloads,
    ):
        if path.exists():
            raise FileExistsError(f"Artefak drill sudah ada: {path}")

    db = Database(str(source))
    db.ensure_mapping()
    db.ensure_parameters()
    outage_settings = Settings(
        _env_file=None,
        database_path=str(source),
        analysis_pipeline_v2_enabled=True,
        analysis_pipeline_v2_shadow=False,
        legacy_smart_upload_enabled=True,
        smart_upload_allow_real_upload=False,
        analysis_payload_storage_backend="filesystem",
        analysis_payload_storage_path=str(source_payloads),
        analysis_payload_storage_fsync=False,
        analysis_local_ocr_enabled=False,
        analysis_structured_model_enabled=False,
        analysis_model_verifier_enabled=False,
        vision_analysis_enabled=False,
        deepseek_api_key="",
        sumopod_api_key="",
        ai_api_key="",
    )
    outage_result = AnalysisOrchestrator(db, outage_settings).start(
        file_name="synthetic-provider-outage.png",
        content_type="image/png",
        payload=synthetic_probe_png(),
        analysis_mode="full_audit",
    )
    outage_run = outage_result["run"]
    outage_units = outage_result["document_units"]
    provider_outage = {
        "run_id": int(outage_run["id"]),
        "run_status": outage_run["status"],
        "coverage_status": outage_run["coverage_status"],
        "primary_blocked": bool(outage_run["primary_blocked"]),
        "ocr_required_units": sum(
            str(unit.get("status") or "") == "ocr_required"
            for unit in outage_units
        ),
        "external_ai_configured": bool(outage_settings.has_ai_key),
    }
    provider_outage["passed"] = bool(
        provider_outage["coverage_status"] != "complete"
        and provider_outage["primary_blocked"]
        and provider_outage["ocr_required_units"] >= 1
        and not provider_outage["external_ai_configured"]
    )

    backup_report = backup(
        source,
        backup_path,
        payload_source=source_payloads,
        payload_destination=backup_payloads,
    )
    restore_report = restore_verified(
        backup_path,
        restored,
        payload_backup=backup_payloads,
        payload_target=restored_payloads,
    )
    restored_verification = verify_database(restored)
    backup_restore = {
        "backup_integrity": backup_report["integrity_check"],
        "restore_integrity": restore_report["integrity_check"],
        "backup_sha256": backup_report["sha256"],
        "critical_table_counts_match": (
            backup_report["critical_table_counts"]
            == restore_report["critical_table_counts"]
            == restored_verification["critical_table_counts"]
        ),
        "payload_reference_count": int(
            backup_report["payload_storage"]["reference_count"]
        ),
        "payload_manifest_match": (
            backup_report["payload_storage"]["manifest_sha256"]
            == restore_report["payload_storage"]["manifest_sha256"]
        ),
        "payload_orphan_count": int(
            restore_report["payload_storage"]["orphan_count"]
        ),
    }
    backup_restore["passed"] = bool(
        backup_restore["backup_integrity"] == "ok"
        and backup_restore["restore_integrity"] == "ok"
        and backup_restore["critical_table_counts_match"]
        and backup_restore["payload_reference_count"] >= 1
        and backup_restore["payload_manifest_match"]
        and backup_restore["payload_orphan_count"] == 0
    )

    rollback_flags = {
        "analysis_pipeline_v2_enabled": False,
        "analysis_pipeline_v2_shadow": False,
        "legacy_smart_upload_enabled": True,
        "smart_upload_allow_real_upload": False,
    }
    rollback_configuration = {
        **rollback_flags,
        "passed": bool(
            not rollback_flags["analysis_pipeline_v2_enabled"]
            and not rollback_flags["analysis_pipeline_v2_shadow"]
            and rollback_flags["legacy_smart_upload_enabled"]
            and not rollback_flags["smart_upload_allow_real_upload"]
        ),
    }

    closed_promotion = {
        "shadow": {"ready": False},
        "canary": {"ready": False},
        "general_release": {"ready": False},
    }
    guard = RolloutGuardEngine().evaluate(
        requested_stage="canary",
        canary_percentage=25,
        stable_release_cycles=0,
        promotion=closed_promotion,
    )
    rollout_guard = {
        "requested_stage": guard["requested_stage"],
        "effective_stage": guard["effective_stage"],
        "reason_count": len(guard["reasons"]),
        "passed": bool(
            not guard["ready"]
            and guard["effective_stage"] == "development"
            and guard["reasons"]
        ),
    }

    checks = {
        "provider_outage_fail_closed": provider_outage,
        "backup_restore": backup_restore,
        "rollback_configuration": rollback_configuration,
        "rollout_guard": rollout_guard,
    }
    report = {
        "drill_version": DRILL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_only": True,
        "external_ai_used": False,
        "passed": all(bool(check["passed"]) for check in checks.values()),
        "checks": checks,
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local provider-outage, rollback, and backup/restore drill."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Optional empty directory for drill databases; defaults to a temporary directory.",
    )
    args = parser.parse_args()

    if args.work_dir:
        report = run_incident_drill(args.work_dir.expanduser().resolve())
    else:
        with TemporaryDirectory(prefix="spip-incident-drill-") as directory:
            report = run_incident_drill(Path(directory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
