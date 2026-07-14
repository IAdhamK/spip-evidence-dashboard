from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.analysis.payload_storage import FilesystemPayloadStore


CRITICAL_TABLES = (
    "documents", "analysis_runs", "analysis_jobs", "engine_results",
    "document_units", "extracted_facts", "mapping_candidates",
    "grade_assessments", "verification_results", "human_review_decisions",
    "controlled_upload_actions",
    "controlled_upload_reconciliation_events",
    "expert_review_labels", "domain_rule_approval_events",
    "visual_review_decisions", "evaluation_reports",
    "analysis_release_events", "analysis_shadow_pairs",
    "legacy_pipeline_usage_daily",
    "retrieval_feedback_snapshots", "retrieval_feedback_terms",
)


def payload_references(database_path: Path) -> list[dict]:
    with closing(sqlite3.connect(database_path)) as conn:
        conn.row_factory = sqlite3.Row
        document_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        job_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(analysis_jobs)").fetchall()
        }
        references: dict[str, dict] = {}
        if "payload_storage_key" in document_columns:
            rows = conn.execute(
                """
                SELECT payload_storage_key AS storage_key,
                       payload_storage_sha256 AS sha256,
                       payload_storage_size_bytes AS size_bytes
                FROM documents
                WHERE payload_storage_backend = 'filesystem'
                  AND payload_storage_key IS NOT NULL
                  AND storage_status != 'purged'
                """
            ).fetchall()
            for row in rows:
                references[str(row["storage_key"])] = dict(row)
        if "payload_storage_key" in job_columns:
            rows = conn.execute(
                """
                SELECT payload_storage_key AS storage_key,
                       payload_storage_sha256 AS sha256,
                       payload_storage_size_bytes AS size_bytes
                FROM analysis_jobs
                WHERE payload_storage_backend = 'filesystem'
                  AND payload_storage_key IS NOT NULL
                  AND status IN ('queued', 'running', 'cancel_requested')
                """
            ).fetchall()
            for row in rows:
                key = str(row["storage_key"])
                existing = references.get(key)
                if existing and (
                    existing.get("sha256") != row["sha256"]
                    or int(existing.get("size_bytes") or -1) != int(row["size_bytes"] or -1)
                ):
                    raise RuntimeError("Metadata payload storage bertentangan untuk key yang sama.")
                references[key] = dict(row)
    return [references[key] for key in sorted(references)]


def verify_payload_storage(database_path: Path, payload_root: Path) -> dict:
    references = payload_references(database_path)
    store = FilesystemPayloadStore(payload_root, fsync=False)
    canonical = []
    total_size = 0
    for reference in references:
        key = str(reference["storage_key"])
        sha256 = str(reference.get("sha256") or "")
        size_bytes = int(reference.get("size_bytes") or 0)
        store.get(
            key,
            expected_sha256=sha256,
            expected_size_bytes=size_bytes,
        )
        canonical.append({"key": key, "sha256": sha256, "size_bytes": size_bytes})
        total_size += size_bytes
    stored_keys = store.keys()
    referenced_keys = {item["key"] for item in canonical}
    manifest_sha256 = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "reference_count": len(references),
        "unique_payload_count": len(referenced_keys),
        "total_size_bytes": total_size,
        "orphan_count": len(stored_keys - referenced_keys),
        "manifest_sha256": manifest_sha256,
    }


def copy_verified_payload_storage(
    database_path: Path,
    source_root: Path,
    destination_root: Path,
) -> dict:
    if destination_root.exists():
        raise FileExistsError(f"Tujuan backup payload sudah ada: {destination_root}")
    references = payload_references(database_path)
    source_store = FilesystemPayloadStore(source_root, fsync=False)
    destination_store = FilesystemPayloadStore(destination_root, fsync=True)
    for reference in references:
        payload = source_store.get(
            str(reference["storage_key"]),
            expected_sha256=str(reference["sha256"]),
            expected_size_bytes=int(reference["size_bytes"]),
        )
        stored = destination_store.put(payload, expected_sha256=str(reference["sha256"]))
        if stored.key != reference["storage_key"]:
            raise RuntimeError("Key payload berubah selama backup.")
    return verify_payload_storage(database_path, destination_root)


def backup(
    source: Path,
    destination: Path,
    *,
    payload_source: Path | None = None,
    payload_destination: Path | None = None,
) -> dict:
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(f"Tujuan backup sudah ada: {destination}")
    references = payload_references(source)
    if references and (payload_source is None or payload_destination is None):
        raise RuntimeError(
            "Database mempunyai payload filesystem; --payload-source dan "
            "--payload-destination wajib diisi."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as source_db, closing(
        sqlite3.connect(destination)
    ) as target_db:
        source_db.backup(target_db)
    verification = verify_database(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    report = {
        "source": str(source.resolve()),
        "destination": str(destination.resolve()),
        "size_bytes": destination.stat().st_size,
        "sha256": digest,
        **verification,
    }
    if references and payload_source and payload_destination:
        report["payload_storage"] = copy_verified_payload_storage(
            destination,
            payload_source,
            payload_destination,
        )
    else:
        report["payload_storage"] = {
            "reference_count": 0,
            "unique_payload_count": 0,
            "total_size_bytes": 0,
            "orphan_count": 0,
            "manifest_sha256": hashlib.sha256(b"[]").hexdigest(),
        }
    return report


def verify_database(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path)) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in CRITICAL_TABLES
            if table in existing
        }
    if integrity != "ok":
        raise RuntimeError(f"Database integrity check gagal: {integrity}")
    return {
        "integrity_check": integrity,
        "critical_table_counts": counts,
    }


def restore_verified(
    backup_path: Path,
    target: Path,
    *,
    payload_backup: Path | None = None,
    payload_target: Path | None = None,
) -> dict:
    source_verification = verify_database(backup_path)
    references = payload_references(backup_path)
    if references and (payload_backup is None or payload_target is None):
        raise RuntimeError(
            "Backup database mempunyai payload filesystem; backup dan target payload wajib diisi."
        )
    if target.exists():
        raise FileExistsError(f"Tujuan restore sudah ada: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(backup_path)) as source_db, closing(
        sqlite3.connect(target)
    ) as target_db:
        source_db.backup(target_db)
    restored_verification = verify_database(target)
    if restored_verification["critical_table_counts"] != source_verification["critical_table_counts"]:
        raise RuntimeError("Critical table counts berbeda setelah restore.")
    report = {
        "backup": str(backup_path.resolve()),
        "restored_to": str(target.resolve()),
        "size_bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        **restored_verification,
    }
    if references and payload_backup and payload_target:
        report["payload_storage"] = copy_verified_payload_storage(
            target,
            payload_backup,
            payload_target,
        )
    else:
        report["payload_storage"] = {
            "reference_count": 0,
            "unique_payload_count": 0,
            "total_size_bytes": 0,
            "orphan_count": 0,
            "manifest_sha256": hashlib.sha256(b"[]").hexdigest(),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and verify a SQLite online backup.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--restore-to", type=Path)
    parser.add_argument("--payload-source", type=Path)
    parser.add_argument("--payload-destination", type=Path)
    parser.add_argument("--restore-payload-to", type=Path)
    args = parser.parse_args()
    report = backup(
        args.source,
        args.destination,
        payload_source=args.payload_source,
        payload_destination=args.payload_destination,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(rendered + "\n", encoding="utf-8")
    if args.restore_to:
        print(json.dumps(
            restore_verified(
                args.destination,
                args.restore_to,
                payload_backup=args.payload_destination,
                payload_target=args.restore_payload_to,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
