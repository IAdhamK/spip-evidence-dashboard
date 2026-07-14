from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
from typing import Any
from uuid import uuid4

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import EngineResult, SecurityFinding
from app.analysis.payload_storage import (
    FilesystemPayloadStore,
    PayloadIntegrityError,
    PayloadStorageError,
)
from app.analysis.storage_evidence import storage_encryption_attestation_status
from app.config import Settings, get_settings
from app.database import Database


JSON_FIELDS = {
    "block_reasons_json": ("block_reasons", []),
    "payload_json": ("payload", {}),
    "input_refs_json": ("input_refs", []),
    "output_refs_json": ("output_refs", []),
    "coverage_json": ("coverage", {}),
    "warnings_json": ("warnings", []),
    "metrics_json": ("metrics", {}),
    "output_json": ("output", {}),
    "details_json": ("details", {}),
    "heading_path_json": ("heading_path", []),
    "source_location_json": ("source_location", {}),
    "metadata_json": ("metadata", {}),
    "structure_json": ("structure", {}),
    "supporting_fact_ids_json": ("supporting_fact_ids", []),
    "reasons_json": ("reasons", []),
    "missing_evidence_json": ("missing_evidence", []),
    "rule_trace_json": ("rule_trace", {}),
    "missing_requirements_json": ("missing_requirements", []),
    "findings_json": ("findings", []),
    "grades_json": ("grades", []),
    "chain_json": ("chain", {}),
    "supporting_run_ids_json": ("supporting_run_ids", []),
    "contradictions_json": ("contradictions", []),
    "rule_definition_json": ("rule_definition", {}),
    "assessment_snapshot_json": ("assessment_snapshot", []),
    "destination_json": ("destination", {}),
    "selected_fact_ids_json": ("selected_fact_ids", []),
    "expected_mappings_json": ("expected_mappings", []),
    "expected_source_locations_json": ("expected_source_locations", []),
    "audit_json": ("audit", {}),
    "expected_tokens_json": ("expected_tokens", []),
    "evidence_json": ("evidence", {}),
    "comparison_json": ("comparison", {}),
}
VALID_EVIDENCE_ROLES = {"primary", "supporting", "context", "contradictory"}
VALID_EXPERT_TEMPLATE_STATUSES = {"template_only", "substantive", "not_assessed"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_json_fields(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for source, (target, default) in JSON_FIELDS.items():
        if source not in item:
            continue
        raw = item.pop(source)
        try:
            item[target] = json.loads(raw) if raw else default
        except (json.JSONDecodeError, TypeError):
            item[target] = default
    for boolean_field in (
        "primary_blocked",
        "primary_allowed",
        "blocking",
        "is_active",
        "external_ai_allowed",
        "attested",
        "release_authority",
    ):
        if boolean_field in item:
            item[boolean_field] = bool(item[boolean_field])
    return item


class AnalysisRepository:
    def __init__(
        self,
        db: Database,
        payload_store: FilesystemPayloadStore | None = None,
        settings: Settings | None = None,
    ):
        self.db = db
        configured = settings or get_settings()
        self.settings = configured
        backend = str(configured.analysis_payload_storage_backend or "database").strip().lower()
        if payload_store is not None:
            self.payload_store = payload_store
        elif backend == "database":
            self.payload_store = None
        elif backend == "filesystem":
            self.payload_store = FilesystemPayloadStore(
                configured.analysis_payload_storage_path,
                fsync=configured.analysis_payload_storage_fsync,
            )
        else:
            raise PayloadStorageError(
                f"Payload storage backend {backend!r} belum didukung."
            )

    def canonical_persistence_capabilities(self) -> dict[str, object]:
        provider = getattr(self.db, "canonical_persistence_capabilities", None)
        if callable(provider):
            return dict(provider())
        return {
            "backend_name": "unknown",
            "shared_across_replicas": False,
            "atomic_distributed_claims": False,
            "shared_payload_storage": False,
        }

    def _payload_from_row(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        blob_field: str,
        fallback_sha256: str | None = None,
        fallback_size_bytes: int | None = None,
    ) -> bytes | None:
        item = dict(row)
        backend = str(item.get("payload_storage_backend") or "database").lower()
        expected_sha256 = str(item.get("payload_storage_sha256") or fallback_sha256 or "")
        expected_size = int(
            item.get("payload_storage_size_bytes")
            if item.get("payload_storage_size_bytes") is not None
            else (fallback_size_bytes or 0)
        )
        if backend == "filesystem":
            if not self.payload_store:
                raise PayloadStorageError(
                    "Payload berada di filesystem tetapi backend tersebut tidak dikonfigurasi."
                )
            key = str(item.get("payload_storage_key") or "")
            if not key or not expected_sha256 or expected_size < 0:
                raise PayloadIntegrityError("Metadata payload filesystem tidak lengkap.")
            return self.payload_store.get(
                key,
                expected_sha256=expected_sha256,
                expected_size_bytes=expected_size,
            )
        if backend != "database":
            raise PayloadStorageError(f"Backend payload tersimpan {backend!r} tidak didukung.")
        blob = item.get(blob_field)
        if blob is None:
            return None
        payload = bytes(blob)
        if fallback_size_bytes is not None and len(payload) != int(fallback_size_bytes):
            raise PayloadIntegrityError("Ukuran payload database tidak cocok.")
        if expected_size and len(payload) != expected_size:
            raise PayloadIntegrityError("Ukuran metadata payload database tidak cocok.")
        if expected_sha256 and hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise PayloadIntegrityError("Checksum payload database tidak cocok.")
        return payload

    def referenced_payload_keys(self) -> set[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_storage_key AS storage_key
                FROM documents
                WHERE payload_storage_backend = 'filesystem'
                  AND payload_storage_key IS NOT NULL
                  AND storage_status != 'purged'
                UNION
                SELECT payload_storage_key AS storage_key
                FROM analysis_jobs
                WHERE payload_storage_backend = 'filesystem'
                  AND payload_storage_key IS NOT NULL
                  AND status IN ('queued', 'running', 'cancel_requested')
                """
            ).fetchall()
        return {str(row["storage_key"]) for row in rows if row["storage_key"]}

    def cleanup_orphaned_payloads(self) -> dict[str, int]:
        if not self.payload_store:
            return {
                "stored_count": 0,
                "referenced_count": 0,
                "orphan_count": 0,
                "deleted_count": 0,
            }
        return self.payload_store.cleanup_orphans(self.referenced_payload_keys())

    def payload_storage_status(self, *, scan_filesystem: bool = False) -> dict[str, Any]:
        encryption_attestation = storage_encryption_attestation_status(self.settings)
        with self.db.connect() as conn:
            document_rows = conn.execute(
                """
                SELECT payload_storage_backend AS backend, COUNT(*) AS total,
                       COALESCE(SUM(payload_storage_size_bytes), 0) AS size_bytes
                FROM documents
                WHERE storage_status != 'purged'
                GROUP BY payload_storage_backend
                """
            ).fetchall()
            job_rows = conn.execute(
                """
                SELECT payload_storage_backend AS backend, COUNT(*) AS total,
                       COALESCE(SUM(payload_storage_size_bytes), 0) AS size_bytes
                FROM analysis_jobs
                WHERE status IN ('queued', 'running', 'cancel_requested')
                GROUP BY payload_storage_backend
                """
            ).fetchall()
        backend = self.payload_store.backend if self.payload_store else "database"
        referenced_keys = self.referenced_payload_keys() if self.payload_store else set()
        stored_keys = (
            self.payload_store.keys()
            if self.payload_store and scan_filesystem else None
        )
        return {
            "configured_backend": backend,
            "filesystem_configured": bool(self.payload_store),
            "application_layer_encryption": False,
            "encrypted_volume_required": True,
            "platform_encryption_validated": bool(encryption_attestation["effective"]),
            "encryption_attestation": encryption_attestation,
            "filesystem_private_permissions": bool(
                self.payload_store
                and not (self.payload_store.root.stat().st_mode & 0o077)
            ),
            "filesystem_scan_performed": bool(stored_keys is not None),
            "filesystem_stored_count": len(stored_keys) if stored_keys is not None else None,
            "filesystem_referenced_count": len(referenced_keys),
            "filesystem_orphan_count": (
                len(stored_keys - referenced_keys) if stored_keys is not None else None
            ),
            "documents_by_backend": {
                str(row["backend"] or "database"): {
                    "count": int(row["total"] or 0),
                    "size_bytes": int(row["size_bytes"] or 0),
                }
                for row in document_rows
            },
            "active_jobs_by_backend": {
                str(row["backend"] or "database"): {
                    "count": int(row["total"] or 0),
                    "size_bytes": int(row["size_bytes"] or 0),
                }
                for row in job_rows
            },
        }

    def _delete_payload_if_unreferenced(self, key: str | None) -> None:
        if not self.payload_store or not key:
            return
        if key not in self.referenced_payload_keys():
            self.payload_store.delete(key)

    @staticmethod
    def _cancel_nonterminal_run(
        conn: sqlite3.Connection,
        run_id: int | None,
        *,
        source: str,
        message: str,
    ) -> bool:
        if not run_id:
            return False
        cursor = conn.execute(
            """
            UPDATE analysis_runs
            SET status = 'cancelled', primary_blocked = 1,
                block_reasons_json = ?, error_message = ?,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status NOT IN (
                  'blocked', 'cancelled', 'screening_complete', 'review_required',
                  'approved', 'rejected', 'uploaded', 'failed'
              )
            """,
            (
                _json(["Analysis run dibatalkan oleh pengguna."]),
                message[:1000],
                int(run_id),
            ),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute(
            """
            INSERT INTO analysis_events (
                run_id, event_type, stage, progress, message, payload_json
            ) VALUES (?, 'run_cancelled', 'orchestration', 100, ?, ?)
            """,
            (
                int(run_id),
                message[:1000],
                _json({"source": source}),
            ),
        )
        return True

    def upsert_document(
        self,
        *,
        file_name: str,
        content_type: str | None,
        size_bytes: int,
        sha256: str,
        payload: bytes,
        ttl_hours: int,
    ) -> dict[str, Any]:
        content = bytes(payload)
        if len(content) != int(size_bytes) or hashlib.sha256(content).hexdigest() != sha256:
            raise PayloadIntegrityError("Ukuran/checksum dokumen tidak cocok dengan payload.")
        purge_after = (
            datetime.now(timezone.utc) + timedelta(hours=max(1, ttl_hours))
        ).isoformat(timespec="seconds")
        stored = self.payload_store.put(content, expected_sha256=sha256) if self.payload_store else None
        storage_backend = stored.backend if stored else "database"
        storage_key = stored.key if stored else None
        storage_sha256 = stored.sha256 if stored else sha256
        storage_size_bytes = stored.size_bytes if stored else len(content)
        pending_bytes = None if stored else content
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    file_name, content_type, size_bytes, sha256, storage_status,
                    pending_bytes, purge_after, payload_storage_backend,
                    payload_storage_key, payload_storage_sha256,
                    payload_storage_size_bytes, payload_storage_created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(sha256, size_bytes) DO UPDATE SET
                    file_name = excluded.file_name,
                    content_type = excluded.content_type,
                    storage_status = 'pending',
                    pending_bytes = excluded.pending_bytes,
                    purge_after = excluded.purge_after,
                    payload_storage_backend = excluded.payload_storage_backend,
                    payload_storage_key = excluded.payload_storage_key,
                    payload_storage_sha256 = excluded.payload_storage_sha256,
                    payload_storage_size_bytes = excluded.payload_storage_size_bytes,
                    payload_storage_created_at = CURRENT_TIMESTAMP
                """,
                (
                    file_name, content_type, size_bytes, sha256, pending_bytes,
                    purge_after, storage_backend, storage_key, storage_sha256,
                    storage_size_bytes,
                ),
            )
            row = conn.execute(
                "SELECT * FROM documents WHERE sha256 = ? AND size_bytes = ?",
                (sha256, size_bytes),
            ).fetchone()
        if not row:
            raise RuntimeError("Dokumen V2 gagal disimpan.")
        item = dict(row)
        item.pop("pending_bytes", None)
        return item

    def enqueue_job(
        self,
        *,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        analysis_mode: str,
        dedupe_key: str | None = None,
        resume_from_run_id: int | None = None,
        external_ai_allowed: bool = True,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        content = bytes(payload)
        content_sha256 = hashlib.sha256(content).hexdigest()
        if dedupe_key:
            with self.db.connect() as conn:
                existing = conn.execute(
                    """
                    SELECT id FROM analysis_jobs
                    WHERE dedupe_key = ? AND status IN ('queued', 'running', 'completed')
                    LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
            if existing:
                job = self.get_job(str(existing["id"]))
                if job:
                    job["deduplicated"] = True
                    return job
        stored = self.payload_store.put(content, expected_sha256=content_sha256) if self.payload_store else None
        storage_backend = stored.backend if stored else "database"
        storage_key = stored.key if stored else None
        storage_sha256 = stored.sha256 if stored else content_sha256
        storage_size_bytes = stored.size_bytes if stored else len(content)
        database_payload = None if stored else content
        with self.db.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO analysis_jobs (
                        id, status, file_name, content_type, size_bytes, payload,
                        analysis_mode, dedupe_key, resume_from_run_id, external_ai_allowed,
                        payload_storage_backend, payload_storage_key,
                        payload_storage_sha256, payload_storage_size_bytes,
                        payload_storage_created_at
                    )
                    VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        job_id, file_name, content_type, len(content), database_payload,
                        analysis_mode, dedupe_key, resume_from_run_id,
                        int(bool(external_ai_allowed)), storage_backend, storage_key,
                        storage_sha256, storage_size_bytes,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT id FROM analysis_jobs WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if not existing:
                    raise
                job = self.get_job(str(existing["id"]))
                if job:
                    job["deduplicated"] = True
                    return job
        job = self.get_job(job_id)
        if not job:
            raise RuntimeError("Job analisis gagal dibuat.")
        return job

    def recover_expired_jobs(self) -> int:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            external_keys = {
                str(row["payload_storage_key"])
                for row in conn.execute(
                    """
                    SELECT payload_storage_key FROM analysis_jobs
                    WHERE payload_storage_backend = 'filesystem'
                      AND payload_storage_key IS NOT NULL
                      AND status IN ('running', 'cancel_requested')
                      AND lease_until IS NOT NULL
                      AND lease_until < CURRENT_TIMESTAMP
                    """
                ).fetchall()
            }
            expired_cancellations = conn.execute(
                """
                SELECT id, run_id FROM analysis_jobs
                WHERE status = 'cancel_requested'
                  AND lease_until IS NOT NULL
                  AND lease_until < CURRENT_TIMESTAMP
                """
            ).fetchall()
            for item in expired_cancellations:
                self._cancel_nonterminal_run(
                    conn,
                    int(item["run_id"]) if item["run_id"] is not None else None,
                    source="lease_recovery",
                    message=(
                        "Permintaan pembatalan dipulihkan setelah lease worker berakhir; "
                        "run ditutup fail-closed."
                    ),
                )
            terminal_cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'completed', payload = NULL, payload_storage_key = NULL,
                    lease_until = NULL,
                    finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP,
                    error_message = 'Recovered terminal analysis run after worker restart.'
                WHERE status = 'running'
                  AND lease_until IS NOT NULL
                  AND lease_until < CURRENT_TIMESTAMP
                  AND run_id IN (
                      SELECT id FROM analysis_runs
                      WHERE status IN (
                          'blocked', 'cancelled', 'screening_complete', 'review_required',
                          'approved', 'rejected', 'uploaded', 'failed'
                      )
                  )
                """
            )
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'queued', lease_until = NULL, updated_at = CURRENT_TIMESTAMP,
                    error_message = CASE
                        WHEN error_message IS NULL OR error_message = ''
                        THEN 'Worker sebelumnya berhenti; job dijadwalkan ulang.'
                        ELSE error_message
                    END
                WHERE status = 'running'
                  AND lease_until IS NOT NULL
                  AND lease_until < CURRENT_TIMESTAMP
                """
            )
            cancelled_cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'cancelled', payload = NULL, payload_storage_key = NULL,
                    lease_until = NULL,
                    finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE status = 'cancel_requested'
                  AND lease_until IS NOT NULL
                  AND lease_until < CURRENT_TIMESTAMP
                """
            )
            recovered = (
                int(cursor.rowcount)
                + int(terminal_cursor.rowcount)
                + int(cancelled_cursor.rowcount)
            )
        for key in external_keys:
            self._delete_payload_if_unreferenced(key)
        for item in expired_cancellations:
            self.mark_shadow_job_status(str(item["id"]), "cancelled")
        return recovered

    def supersede_nonterminal_job_run(
        self,
        job_id: str,
        run_id: int,
        *,
        expected_attempt: int | None = None,
    ) -> bool:
        """Atomically persist retry lineage before a recovered job starts a new run."""
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT status, run_id, attempt_count FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if (
                not job
                or str(job["status"]) != "running"
                or int(job["run_id"] or 0) != int(run_id)
                or (
                    expected_attempt is not None
                    and int(job["attempt_count"] or 0) != int(expected_attempt)
                )
            ):
                return False
            run_cursor = conn.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed', primary_blocked = 1,
                    block_reasons_json = ?,
                    error_message = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status NOT IN (
                      'blocked', 'cancelled', 'screening_complete', 'review_required',
                      'approved', 'rejected', 'uploaded', 'failed'
                  )
                """,
                (
                    _json(["Worker lease expired; retry dibuat sebagai run baru."]),
                    "Run tidak terminal saat worker lease dipulihkan.",
                    int(run_id),
                ),
            )
            if run_cursor.rowcount != 1:
                return False
            conn.execute(
                """
                INSERT INTO analysis_events (
                    run_id, event_type, stage, progress, message, payload_json
                ) VALUES (?, 'run_superseded_after_recovery', 'orchestration', 100, ?, ?)
                """,
                (
                    int(run_id),
                    "Run lama dipertahankan untuk audit; job dilanjutkan sebagai run retry baru.",
                    _json({"source_run_id": int(run_id)}),
                ),
            )
            job_cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET resume_from_run_id = ?, run_id = NULL,
                    error_message = 'Lease recovery tersimpan; menunggu run retry.',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running' AND run_id = ?
                  AND (? IS NULL OR attempt_count = ?)
                """,
                (
                    int(run_id), job_id, int(run_id),
                    expected_attempt, expected_attempt,
                ),
            )
            if job_cursor.rowcount != 1:
                raise RuntimeError("Pointer recovery job gagal dipersistenkan secara atomik.")
        return True

    def purge_expired_payloads(self) -> int:
        """Delete retained source bytes while preserving immutable analysis/audit metadata."""
        with self.db.connect() as conn:
            document_keys = {
                str(row["payload_storage_key"])
                for row in conn.execute(
                    """
                    SELECT payload_storage_key FROM documents
                    WHERE payload_storage_backend = 'filesystem'
                      AND payload_storage_key IS NOT NULL
                      AND storage_status != 'purged'
                      AND purge_after IS NOT NULL
                      AND purge_after <= CURRENT_TIMESTAMP
                    """
                ).fetchall()
            }
            terminal_job_keys = {
                str(row["payload_storage_key"])
                for row in conn.execute(
                    """
                    SELECT payload_storage_key FROM analysis_jobs
                    WHERE payload_storage_backend = 'filesystem'
                      AND payload_storage_key IS NOT NULL
                      AND status IN ('completed', 'failed', 'cancelled')
                    """
                ).fetchall()
            }
            cursor = conn.execute(
                """
                UPDATE documents
                SET pending_bytes = NULL, storage_status = 'purged'
                WHERE storage_status != 'purged'
                  AND purge_after IS NOT NULL
                  AND purge_after <= CURRENT_TIMESTAMP
                  AND (pending_bytes IS NOT NULL OR payload_storage_key IS NOT NULL)
                """
            )
            conn.execute(
                """
                UPDATE analysis_jobs
                SET payload = NULL, payload_storage_key = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND (payload IS NOT NULL OR payload_storage_key IS NOT NULL)
                """
            )
            purged = int(cursor.rowcount)
        for key in document_keys | terminal_job_keys:
            self._delete_payload_if_unreferenced(key)
        return purged

    def operational_metrics(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            queue_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM analysis_jobs GROUP BY status"
            ).fetchall()
            run_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM analysis_runs GROUP BY status"
            ).fetchall()
            engine_rows = conn.execute(
                """
                SELECT engine_name, status, COUNT(*) AS total
                FROM engine_results
                GROUP BY engine_name, status
                """
            ).fetchall()
            routing_rows = conn.execute(
                """
                SELECT engine_name, output_json
                FROM engine_results
                WHERE engine_name LIKE 'compute_routing_%'
                """
            ).fetchall()
            ocr_resource_rows = conn.execute(
                """
                SELECT output_json
                FROM engine_results
                WHERE engine_name = 'visual_ocr'
                """
            ).fetchall()
            summary = conn.execute(
                """
                SELECT COUNT(*) AS run_count,
                       AVG(CASE WHEN started_at IS NOT NULL AND finished_at IS NOT NULL
                           THEN (julianday(finished_at) - julianday(started_at)) * 86400 END)
                           AS average_duration_seconds,
                       SUM(CASE WHEN coverage_status = 'complete' THEN 1 ELSE 0 END)
                           AS complete_coverage_count,
                       SUM(CASE WHEN ocr_required_units > 0 THEN 1 ELSE 0 END)
                           AS ocr_run_count,
                       SUM(CASE WHEN primary_blocked = 1 THEN 1 ELSE 0 END)
                           AS primary_blocked_count,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                       COALESCE(SUM(
                           CASE WHEN created_at >= datetime('now', '-1 hour')
                           THEN estimated_cost_usd ELSE 0 END
                       ), 0) AS estimated_cost_usd_last_hour
                FROM analysis_runs
                """
            ).fetchone()
            verification = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status != 'verified' THEN 1 ELSE 0 END) AS rejected
                FROM verification_results
                """
            ).fetchone()
            decisions = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN decision = 'correct' THEN 1 ELSE 0 END) AS corrected,
                       SUM(CASE WHEN decision = 'reject' THEN 1 ELSE 0 END) AS rejected
                FROM human_review_decisions
                """
            ).fetchone()
            findings = conn.execute(
                "SELECT severity, COUNT(*) AS total FROM security_findings GROUP BY severity"
            ).fetchall()
            uploads = conn.execute(
                "SELECT status, COUNT(*) AS total FROM controlled_upload_actions GROUP BY status"
            ).fetchall()
            stale_upload_reservations = conn.execute(
                """
                SELECT COUNT(*) AS total
                FROM controlled_upload_actions
                WHERE status = 'uploading'
                  AND created_at < datetime('now', '-10 minutes')
                """
            ).fetchone()
            ambiguous_upload_actions = conn.execute(
                """
                SELECT * FROM controlled_upload_actions
                WHERE status = 'blocked_ambiguous'
                ORDER BY id
                """
            ).fetchall()
            reconciliation_event_rows = conn.execute(
                """
                SELECT events.*
                FROM controlled_upload_reconciliation_events AS events
                JOIN controlled_upload_actions AS actions
                  ON actions.id = events.action_id
                WHERE actions.status = 'blocked_ambiguous'
                ORDER BY events.id
                """
            ).fetchall()
            legacy_usage_rows = conn.execute(
                """
                SELECT usage_kind, source, SUM(call_count) AS total
                FROM legacy_pipeline_usage_daily
                GROUP BY usage_kind, source
                """
            ).fetchall()
            batch_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM analysis_batch_intakes GROUP BY status"
            ).fetchall()
            batch_member_rows = conn.execute(
                "SELECT member_status, COUNT(*) AS total FROM analysis_batch_members GROUP BY member_status"
            ).fetchall()
            local_only_batches = conn.execute(
                "SELECT COUNT(*) AS total FROM analysis_batch_intakes WHERE external_ai_allowed = 0"
            ).fetchone()
            vision_probe_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM vision_capability_probes GROUP BY status"
            ).fetchall()
            vision_decision_rows = conn.execute(
                """
                SELECT scope, status, COUNT(*) AS total
                FROM vision_governance_decisions
                WHERE is_active = 1
                GROUP BY scope, status
                """
            ).fetchall()
            evaluation_authority_rows = conn.execute(
                """
                SELECT release_authority, COUNT(*) AS total
                FROM evaluation_reports
                GROUP BY release_authority
                """
            ).fetchall()
            job_recovery = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN attempt_count > 1 THEN 1 ELSE 0 END), 0)
                        AS recovered_job_count,
                    COALESCE(SUM(CASE WHEN attempt_count > 1 THEN attempt_count - 1 ELSE 0 END), 0)
                        AS lease_retry_attempt_count,
                    COALESCE(SUM(CASE WHEN resume_from_run_id IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS resume_lineage_job_count,
                    COALESCE(SUM(CASE
                        WHEN status IN ('queued', 'running', 'cancel_requested')
                         AND attempt_count >= 3 THEN 1 ELSE 0 END), 0)
                        AS active_recovery_loop_count
                FROM analysis_jobs
                """
            ).fetchone()
        reconciliation_events_by_action: dict[int, list[dict[str, Any]]] = {}
        for row in reconciliation_event_rows:
            event = _decode_json_fields(dict(row))
            reconciliation_events_by_action.setdefault(
                int(event["action_id"]), []
            ).append(event)
        ambiguity_summaries = [
            self._controlled_upload_reconciliation_summary(
                _decode_json_fields(dict(row)),
                reconciliation_events_by_action.get(int(row["id"]), []),
            )
            for row in ambiguous_upload_actions
        ]
        resolved_upload_ambiguities = sum(
            1 for item in ambiguity_summaries if item["effective"]
        )
        unresolved_upload_ambiguities = (
            len(ambiguity_summaries) - resolved_upload_ambiguities
        )
        retrieval_feedback = self.retrieval_feedback_summary()
        compute_routing: dict[str, dict[str, int | float]] = {}
        for row in routing_rows:
            try:
                output = json.loads(row["output_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            phase = str(output.get("phase") or str(row["engine_name"]).removeprefix("compute_routing_"))
            bucket = compute_routing.setdefault(phase, {
                "total": 0,
                "selected": 0,
                "complexity_sum": 0.0,
                "risk_sum": 0.0,
            })
            bucket["total"] = int(bucket["total"]) + 1
            bucket["selected"] = int(bucket["selected"]) + int(bool(output.get("selected")))
            bucket["complexity_sum"] = float(bucket["complexity_sum"]) + float(
                output.get("complexity_score") or 0
            )
            bucket["risk_sum"] = float(bucket["risk_sum"]) + float(
                output.get("risk_score") or 0
            )
        compute_routing_summary = {
            phase: {
                "total": int(values["total"]),
                "selected": int(values["selected"]),
                "average_complexity_score": round(
                    float(values["complexity_sum"]) / max(1, int(values["total"])),
                    6,
                ),
                "average_risk_score": round(
                    float(values["risk_sum"]) / max(1, int(values["total"])),
                    6,
                ),
            }
            for phase, values in compute_routing.items()
        }
        ocr_resource = {
            "attempt_count": 0,
            "timeout_count": 0,
            "budget_exhausted_unit_count": 0,
            "durable_checkpoint_batch_count": 0,
            "document_elapsed_ms": 0,
            "budget_exhaustion_reason_counts": {},
        }
        reason_counts: dict[str, int] = {}
        for row in ocr_resource_rows:
            try:
                output = json.loads(row["output_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            ocr_resource["attempt_count"] += int(
                output.get("local_ocr_attempt_count") or 0
            )
            ocr_resource["timeout_count"] += int(
                output.get("local_ocr_timeout_count") or 0
            )
            ocr_resource["budget_exhausted_unit_count"] += int(
                output.get("local_ocr_budget_exhausted_units") or 0
            )
            ocr_resource["durable_checkpoint_batch_count"] += int(
                output.get("durable_checkpoint_batches") or 0
            )
            ocr_resource["document_elapsed_ms"] += int(
                output.get("local_ocr_document_elapsed_ms") or 0
            )
            for reason in (
                output.get("local_ocr_budget_exhaustion_reasons") or {}
            ).values():
                safe_reason = str(reason or "unknown")[:80]
                reason_counts[safe_reason] = reason_counts.get(safe_reason, 0) + 1
        ocr_resource["budget_exhaustion_reason_counts"] = dict(
            sorted(reason_counts.items())
        )
        return {
            "queue_by_status": {row["status"]: int(row["total"]) for row in queue_rows},
            "job_recovery": {
                "recovered_job_count": int(job_recovery["recovered_job_count"] or 0),
                "lease_retry_attempt_count": int(
                    job_recovery["lease_retry_attempt_count"] or 0
                ),
                "resume_lineage_job_count": int(
                    job_recovery["resume_lineage_job_count"] or 0
                ),
                "active_recovery_loop_count": int(
                    job_recovery["active_recovery_loop_count"] or 0
                ),
            },
            "runs_by_status": {row["status"]: int(row["total"]) for row in run_rows},
            "engines": {
                f"{row['engine_name']}:{row['status']}": int(row["total"])
                for row in engine_rows
            },
            "compute_routing": compute_routing_summary,
            "ocr_resource": ocr_resource,
            "run_count": int(summary["run_count"] or 0),
            "average_duration_seconds": round(float(summary["average_duration_seconds"] or 0), 3),
            "complete_coverage_count": int(summary["complete_coverage_count"] or 0),
            "ocr_run_count": int(summary["ocr_run_count"] or 0),
            "primary_blocked_count": int(summary["primary_blocked_count"] or 0),
            "input_tokens": int(summary["input_tokens"] or 0),
            "output_tokens": int(summary["output_tokens"] or 0),
            "estimated_cost_usd": round(float(summary["estimated_cost_usd"] or 0), 6),
            "estimated_cost_usd_last_hour": round(
                float(summary["estimated_cost_usd_last_hour"] or 0), 6
            ),
            "verification": {
                "total": int(verification["total"] or 0),
                "rejected": int(verification["rejected"] or 0),
            },
            "human_review": {
                "total": int(decisions["total"] or 0),
                "corrected": int(decisions["corrected"] or 0),
                "rejected": int(decisions["rejected"] or 0),
                "override_ratio": round(
                    (
                        int(decisions["corrected"] or 0)
                        + int(decisions["rejected"] or 0)
                    )
                    / int(decisions["total"] or 1),
                    4,
                ) if int(decisions["total"] or 0) else 0.0,
            },
            "security_findings_by_severity": {
                row["severity"]: int(row["total"]) for row in findings
            },
            "controlled_uploads_by_status": {
                row["status"]: int(row["total"]) for row in uploads
            },
            "stale_controlled_upload_reservation_count": int(
                stale_upload_reservations["total"] or 0
            ),
            "controlled_upload_ambiguity_count": len(ambiguity_summaries),
            "resolved_controlled_upload_ambiguity_count": resolved_upload_ambiguities,
            "unresolved_controlled_upload_ambiguity_count": unresolved_upload_ambiguities,
            "legacy_pipeline_calls_by_kind": {
                f"{row['usage_kind']}:{row['source']}": int(row["total"])
                for row in legacy_usage_rows
            },
            "retrieval_feedback": {
                "active": bool(retrieval_feedback.get("active")),
                "dataset_matches": bool(retrieval_feedback.get("dataset_matches")),
                "parameter_catalog_matches": bool(
                    retrieval_feedback.get("parameter_catalog_matches")
                ),
                "term_count": int(retrieval_feedback.get("term_count") or 0),
                "source_label_count": int(
                    retrieval_feedback.get("source_label_count") or 0
                ),
                "learning_gold_case_count": int(
                    retrieval_feedback.get("expert_gold_case_count") or 0
                ),
            },
            "evaluation_reports_by_authority": {
                ("release" if row["release_authority"] else "informational"): int(row["total"])
                for row in evaluation_authority_rows
            },
            "batch_intakes_by_status": {
                row["status"]: int(row["total"]) for row in batch_rows
            },
            "batch_members_by_status": {
                row["member_status"]: int(row["total"]) for row in batch_member_rows
            },
            "local_only_batch_count": int(local_only_batches["total"] or 0),
            "vision_probes_by_status": {
                row["status"]: int(row["total"]) for row in vision_probe_rows
            },
            "active_vision_decisions": {
                f"{row['scope']}:{row['status']}": int(row["total"])
                for row in vision_decision_rows
            },
        }

    def claim_next_job(self, lease_minutes: int = 15) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM analysis_jobs
                WHERE status = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            job_id = str(row["id"])
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    lease_until = datetime('now', ?), heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (f"+{max(1, lease_minutes)} minutes", job_id),
            )
            claimed = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(claimed) if claimed else None

    def claim_job(self, job_id: str, lease_minutes: int = 15) -> dict[str, Any] | None:
        """Atomically claim a notified job ID; stale/duplicate signals are harmless."""

        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    lease_until = datetime('now', ?), heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (f"+{max(1, lease_minutes)} minutes", job_id),
            )
            if cursor.rowcount != 1:
                return None
            claimed = conn.execute(
                "SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(claimed) if claimed else None

    def acquire_worker_leader(self, owner_id: str, lease_seconds: int = 30) -> bool:
        safe_seconds = max(10, min(300, int(lease_seconds or 30)))
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO analysis_worker_leases (
                    lease_name, owner_id, acquired_at, heartbeat_at, lease_until
                ) VALUES (
                    'analysis-v2-worker', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                    datetime('now', ?)
                )
                ON CONFLICT(lease_name) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    acquired_at = CASE
                        WHEN analysis_worker_leases.owner_id = excluded.owner_id
                        THEN analysis_worker_leases.acquired_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    lease_until = excluded.lease_until
                WHERE analysis_worker_leases.owner_id = excluded.owner_id
                   OR analysis_worker_leases.lease_until < CURRENT_TIMESTAMP
                """,
                (owner_id, f"+{safe_seconds} seconds"),
            )
            row = conn.execute(
                """
                SELECT owner_id, lease_until > CURRENT_TIMESTAMP AS active
                FROM analysis_worker_leases
                WHERE lease_name = 'analysis-v2-worker'
                """
            ).fetchone()
        return bool(row and row["owner_id"] == owner_id and row["active"])

    def renew_worker_leader(self, owner_id: str, lease_seconds: int = 30) -> bool:
        safe_seconds = max(10, min(300, int(lease_seconds or 30)))
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE analysis_worker_leases
                SET heartbeat_at = CURRENT_TIMESTAMP, lease_until = datetime('now', ?)
                WHERE lease_name = 'analysis-v2-worker'
                  AND owner_id = ?
                  AND lease_until >= CURRENT_TIMESTAMP
                """,
                (f"+{safe_seconds} seconds", owner_id),
            )
            return bool(cursor.rowcount)

    def release_worker_leader(self, owner_id: str) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM analysis_worker_leases
                WHERE lease_name = 'analysis-v2-worker' AND owner_id = ?
                """,
                (owner_id,),
            )
            return bool(cursor.rowcount)

    def worker_leader_status(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT heartbeat_at, lease_until,
                       lease_until > CURRENT_TIMESTAMP AS active
                FROM analysis_worker_leases
                WHERE lease_name = 'analysis-v2-worker'
                """
            ).fetchone()
        if not row:
            return {"present": False, "active": False, "heartbeat_at": None, "lease_until": None}
        return {
            "present": True,
            "active": bool(row["active"]),
            "heartbeat_at": row["heartbeat_at"],
            "lease_until": row["lease_until"],
        }

    def record_shadow_pair(self, legacy_review_id: int, v2_job_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_shadow_pairs (legacy_review_id, v2_job_id)
                VALUES (?, ?)
                ON CONFLICT(legacy_review_id) DO UPDATE SET
                    v2_job_id = excluded.v2_job_id,
                    status = CASE
                        WHEN analysis_shadow_pairs.v2_job_id = excluded.v2_job_id
                        THEN analysis_shadow_pairs.status
                        ELSE 'queued'
                    END,
                    v2_run_id = CASE
                        WHEN analysis_shadow_pairs.v2_job_id = excluded.v2_job_id
                        THEN analysis_shadow_pairs.v2_run_id
                        ELSE NULL
                    END,
                    comparison_json = CASE
                        WHEN analysis_shadow_pairs.v2_job_id = excluded.v2_job_id
                        THEN analysis_shadow_pairs.comparison_json
                        ELSE '{}'
                    END,
                    report_sha256 = CASE
                        WHEN analysis_shadow_pairs.v2_job_id = excluded.v2_job_id
                        THEN analysis_shadow_pairs.report_sha256
                        ELSE NULL
                    END,
                    error_code = NULL,
                    completed_at = CASE
                        WHEN analysis_shadow_pairs.v2_job_id = excluded.v2_job_id
                        THEN analysis_shadow_pairs.completed_at
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (legacy_review_id, v2_job_id),
            )
            row = conn.execute(
                "SELECT * FROM analysis_shadow_pairs WHERE legacy_review_id = ?",
                (legacy_review_id,),
            ).fetchone()
        if not row:
            raise RuntimeError("Shadow pair gagal dicatat.")
        return _decode_json_fields(dict(row))

    def get_shadow_pair(self, pair_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_shadow_pairs WHERE id = ?",
                (pair_id,),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def find_shadow_pair(
        self,
        *,
        legacy_review_id: int | None = None,
        v2_job_id: str | None = None,
        v2_run_id: int | None = None,
    ) -> dict[str, Any] | None:
        clauses = []
        values: list[Any] = []
        for column, value in (
            ("legacy_review_id", legacy_review_id),
            ("v2_job_id", v2_job_id),
            ("v2_run_id", v2_run_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        if not clauses:
            return None
        with self.db.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM analysis_shadow_pairs WHERE {' AND '.join(clauses)} LIMIT 1",
                values,
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def list_shadow_pairs(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM analysis_shadow_pairs
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(5000, int(limit or 500))),),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_shadow_comparison(
        self,
        pair_id: int,
        *,
        v2_run_id: int,
        comparison: dict[str, Any],
        report_sha256: str,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_shadow_pairs
                SET v2_run_id = ?, status = 'completed', comparison_json = ?,
                    report_sha256 = ?, error_code = NULL,
                    completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (v2_run_id, _json(comparison), report_sha256, pair_id),
            )
        pair = self.get_shadow_pair(pair_id)
        if not pair:
            raise RuntimeError("Shadow comparison pair tidak ditemukan setelah update.")
        return pair

    def mark_shadow_job_status(self, v2_job_id: str, status: str) -> int:
        normalized = status if status in {"queued", "running", "failed", "cancelled"} else "failed"
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE analysis_shadow_pairs
                SET status = ?, error_code = CASE
                        WHEN ? IN ('failed', 'cancelled') THEN 'v2_job_' || ?
                        ELSE NULL
                    END,
                    completed_at = CASE
                        WHEN ? IN ('failed', 'cancelled') THEN CURRENT_TIMESTAMP
                        ELSE NULL
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE v2_job_id = ? AND status != 'completed'
                """,
                (normalized, normalized, normalized, normalized, v2_job_id),
            )
            return int(cursor.rowcount)

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int = 15,
        *,
        expected_attempt: int | None = None,
    ) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET lease_until = datetime('now', ?), heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('running', 'cancel_requested')
                  AND (? IS NULL OR attempt_count = ?)
                """,
                (
                    f"+{max(1, lease_minutes)} minutes",
                    job_id,
                    expected_attempt,
                    expected_attempt,
                ),
            )
            return bool(cursor.rowcount)

    def requeue_job_after_worker_shutdown(
        self,
        job_id: str,
        *,
        expected_attempt: int | None = None,
    ) -> bool:
        """Return a cooperatively stopped job to the queue without dropping payload/lineage."""
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'queued', lease_until = NULL,
                    error_message = 'Worker shutdown aman; job menunggu recovery.',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running'
                  AND (? IS NULL OR attempt_count = ?)
                """,
                (job_id, expected_attempt, expected_attempt),
            )
            return bool(cursor.rowcount)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id, status, file_name, content_type, size_bytes, analysis_mode,
                       run_id, attempt_count, lease_until, heartbeat_at, dedupe_key,
                       resume_from_run_id, external_ai_allowed,
                       error_message, created_at,
                       started_at, finished_at, updated_at
                FROM analysis_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def find_job_for_run(self, run_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM analysis_jobs
                WHERE run_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (int(run_id),),
            ).fetchone()
        return self.get_job(str(row["id"])) if row else None

    def job_payload(self, job_id: str) -> bytes | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT payload, size_bytes, payload_storage_backend,
                       payload_storage_key, payload_storage_sha256,
                       payload_storage_size_bytes
                FROM analysis_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        return (
            self._payload_from_row(
                row,
                blob_field="payload",
                fallback_size_bytes=int(row["size_bytes"]),
            )
            if row else None
        )

    def job_cancel_requested(self, job_id: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["status"] in {"cancel_requested", "cancelled"})

    def cancel_job(self, job_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, payload_storage_key, run_id
                FROM analysis_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if not row:
                return None
            current = str(row["status"])
            storage_key = str(row["payload_storage_key"] or "") or None
            run_id = int(row["run_id"]) if row["run_id"] is not None else None
            cancelled_immediately = False
            if current == "queued":
                cursor = conn.execute(
                    """
                    UPDATE analysis_jobs SET status = 'cancelled', payload = NULL,
                        payload_storage_key = NULL, finished_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'queued'
                    """,
                    (job_id,),
                )
                cancelled_immediately = cursor.rowcount == 1
                if cancelled_immediately:
                    self._cancel_nonterminal_run(
                        conn,
                        run_id,
                        source="queued_job_cancel",
                        message=(
                            "Job yang menunggu recovery dibatalkan; run aktif ditutup "
                            "sebelum payload dilepas."
                        ),
                    )
            elif current == "running":
                conn.execute(
                    """
                    UPDATE analysis_jobs
                    SET status = 'cancel_requested', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status = 'running'
                    """,
                    (job_id,),
                )
        if cancelled_immediately:
            self._delete_payload_if_unreferenced(storage_key)
            self.mark_shadow_job_status(job_id, "cancelled")
        return self.get_job(job_id)

    def complete_job(
        self,
        job_id: str,
        run_id: int,
        *,
        expected_attempt: int | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, payload_storage_key, attempt_count
                FROM analysis_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                not row
                or (
                    expected_attempt is not None
                    and int(row["attempt_count"] or 0) != int(expected_attempt)
                )
            ):
                result = self.get_job(job_id) or {}
                result["_write_applied"] = False
                return result
            cancelled = bool(row and row["status"] in {"cancel_requested", "cancelled"})
            storage_key = str(row["payload_storage_key"] or "") if row else ""
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, run_id = ?, payload = NULL, payload_storage_key = NULL,
                    lease_until = NULL,
                    finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR (
                      attempt_count = ?
                      AND status IN ('running', 'cancel_requested')
                  ))
                """,
                (
                    "cancelled" if cancelled else "completed",
                    run_id,
                    job_id,
                    expected_attempt,
                    expected_attempt,
                ),
            )
        applied = bool(cursor.rowcount)
        if applied:
            self._delete_payload_if_unreferenced(storage_key)
        result = self.get_job(job_id) or {}
        result["_write_applied"] = applied
        return result

    def attach_job_run(
        self,
        job_id: str,
        run_id: int,
        *,
        expected_attempt: int | None = None,
    ) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET run_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('running', 'cancel_requested')
                  AND (? IS NULL OR attempt_count = ?)
                """,
                (run_id, job_id, expected_attempt, expected_attempt),
            )
            return bool(cursor.rowcount)

    def fail_job(
        self,
        job_id: str,
        message: str,
        run_id: int | None = None,
        *,
        expected_attempt: int | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT payload_storage_key, attempt_count FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if (
                not row
                or (
                    expected_attempt is not None
                    and int(row["attempt_count"] or 0) != int(expected_attempt)
                )
            ):
                result = self.get_job(job_id) or {}
                result["_write_applied"] = False
                return result
            storage_key = str(row["payload_storage_key"] or "") if row else ""
            cursor = conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'failed', run_id = COALESCE(?, run_id), payload = NULL,
                    payload_storage_key = NULL,
                    lease_until = NULL, error_message = ?, finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (? IS NULL OR (
                      attempt_count = ?
                      AND status IN ('running', 'cancel_requested')
                  ))
                """,
                (
                    run_id, message[:1000], job_id,
                    expected_attempt, expected_attempt,
                ),
            )
        applied = bool(cursor.rowcount)
        if applied:
            self._delete_payload_if_unreferenced(storage_key)
            self.mark_shadow_job_status(job_id, "failed")
        result = self.get_job(job_id) or {}
        result["_write_applied"] = applied
        return result

    def create_run(
        self,
        *,
        document_id: int,
        analysis_mode: str,
        pipeline_version: str,
        parser_version: str,
        rule_version: str,
        prompt_version: str,
        provider: str | None,
        model: str | None,
        configuration_hash: str,
        resumed_from_run_id: int | None = None,
        external_ai_allowed: bool = True,
        visual_review_checksum: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_runs (
                    document_id, status, analysis_mode, pipeline_version,
                    parser_version, rule_version, prompt_version, provider,
                    model, configuration_hash, resumed_from_run_id,
                    external_ai_allowed, visual_review_checksum,
                    primary_blocked, block_reasons_json
                )
                VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, '[]')
                """,
                (
                    document_id,
                    analysis_mode,
                    pipeline_version,
                    parser_version,
                    rule_version,
                    prompt_version,
                    provider,
                    model,
                    configuration_hash,
                    resumed_from_run_id,
                    int(bool(external_ai_allowed)),
                    visual_review_checksum,
                ),
            )
            return int(cursor.lastrowid)

    def create_batch_intake(
        self,
        *,
        archive_file_name: str,
        archive_sha256: str,
        archive_size_bytes: int,
        analysis_mode: str,
        requested_limit: int,
        external_ai_allowed: bool,
        dedupe_key: str | None,
        audit: dict[str, Any],
        status: str = "processing",
        error_message: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        batch_id = str(uuid4())
        with self.db.connect() as conn:
            if dedupe_key:
                existing = conn.execute(
                    "SELECT id FROM analysis_batch_intakes WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if existing:
                    batch = self.describe_batch_intake(str(existing["id"]))
                    if batch:
                        batch["deduplicated"] = True
                        return batch, False
            try:
                conn.execute(
                    """
                    INSERT INTO analysis_batch_intakes (
                        id, status, archive_file_name, archive_sha256,
                        archive_size_bytes, analysis_mode, requested_limit,
                        external_ai_allowed, dedupe_key, audit_json, error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        status,
                        archive_file_name,
                        archive_sha256,
                        archive_size_bytes,
                        analysis_mode,
                        requested_limit,
                        int(bool(external_ai_allowed)),
                        dedupe_key,
                        _json(audit),
                        error_message,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT id FROM analysis_batch_intakes WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if not existing:
                    raise
                batch = self.describe_batch_intake(str(existing["id"]))
                if batch:
                    batch["deduplicated"] = True
                    return batch, False
        batch = self.describe_batch_intake(batch_id)
        if not batch:
            raise RuntimeError("Batch intake gagal dibuat.")
        return batch, True

    def add_batch_member(
        self,
        batch_id: str,
        *,
        ordinal: int,
        archive_path: str,
        file_name: str,
        file_kind: str,
        size_bytes: int,
        member_status: str,
        sha256: str | None = None,
        job_id: str | None = None,
        reason: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_batch_members (
                    batch_id, ordinal, archive_path, file_name, file_kind,
                    size_bytes, sha256, member_status, job_id, reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id, archive_path) DO UPDATE SET
                    ordinal = excluded.ordinal,
                    file_name = excluded.file_name,
                    file_kind = excluded.file_kind,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    member_status = excluded.member_status,
                    job_id = excluded.job_id,
                    reason = excluded.reason
                """,
                (
                    batch_id,
                    ordinal,
                    archive_path,
                    file_name,
                    file_kind,
                    size_bytes,
                    sha256,
                    member_status,
                    job_id,
                    reason,
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            row = conn.execute(
                "SELECT id FROM analysis_batch_members WHERE batch_id = ? AND archive_path = ?",
                (batch_id, archive_path),
            ).fetchone()
        if not row:
            raise RuntimeError("Anggota batch gagal disimpan.")
        return int(row["id"])

    def finalize_batch_intake(
        self,
        batch_id: str,
        *,
        selected_count: int,
        enqueued_count: int,
        rejected_count: int,
        skipped_count: int,
        duplicate_count: int,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        initial_status = "processing" if enqueued_count else "completed_with_errors"
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_batch_intakes
                SET status = ?, selected_count = ?, enqueued_count = ?,
                    rejected_count = ?, skipped_count = ?, duplicate_count = ?,
                    error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    initial_status,
                    selected_count,
                    enqueued_count,
                    rejected_count,
                    skipped_count,
                    duplicate_count,
                    error_message,
                    batch_id,
                ),
            )
        batch = self.describe_batch_intake(batch_id)
        if not batch:
            raise RuntimeError("Batch intake tidak ditemukan setelah finalisasi.")
        return batch

    def describe_batch_intake(self, batch_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            batch_row = conn.execute(
                "SELECT * FROM analysis_batch_intakes WHERE id = ?",
                (batch_id,),
            ).fetchone()
            if not batch_row:
                return None
            member_rows = conn.execute(
                """
                SELECT analysis_batch_members.*,
                       analysis_jobs.status AS job_status,
                       analysis_jobs.run_id,
                       analysis_jobs.error_message AS job_error_message,
                       analysis_runs.status AS run_status,
                       analysis_runs.primary_blocked
                FROM analysis_batch_members
                LEFT JOIN analysis_jobs ON analysis_jobs.id = analysis_batch_members.job_id
                LEFT JOIN analysis_runs ON analysis_runs.id = analysis_jobs.run_id
                WHERE analysis_batch_members.batch_id = ?
                ORDER BY analysis_batch_members.ordinal, analysis_batch_members.id
                """,
                (batch_id,),
            ).fetchall()
        batch = _decode_json_fields(dict(batch_row))
        members = [_decode_json_fields(dict(row)) for row in member_rows]
        enqueued = [item for item in members if item.get("job_id")]
        terminal = [
            item for item in enqueued
            if item.get("job_status") in {"completed", "failed", "cancelled"}
        ]
        active = [
            item for item in enqueued
            if item.get("job_status") in {"queued", "running", "cancel_requested"}
        ]
        if batch["status"] != "rejected":
            if active:
                effective_status = "processing"
            elif not enqueued:
                effective_status = "completed_with_errors"
            elif any(item.get("job_status") != "completed" for item in enqueued) or batch.get("rejected_count"):
                effective_status = "completed_with_errors"
            else:
                effective_status = "completed"
            if effective_status != batch["status"]:
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE analysis_batch_intakes SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (effective_status, batch_id),
                    )
                batch["status"] = effective_status
        batch["progress"] = {
            "total": len(enqueued),
            "terminal": len(terminal),
            "completed": sum(item.get("job_status") == "completed" for item in enqueued),
            "failed": sum(item.get("job_status") in {"failed", "cancelled"} for item in enqueued),
            "queued": sum(item.get("job_status") == "queued" for item in enqueued),
            "running": sum(item.get("job_status") in {"running", "cancel_requested"} for item in enqueued),
            "percentage": round(100 * len(terminal) / max(1, len(enqueued)), 2),
        }
        batch["members"] = members
        return batch

    def list_batch_job_ids(self, batch_id: str) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM analysis_batch_members WHERE batch_id = ? AND job_id IS NOT NULL",
                (batch_id,),
            ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def list_recent_batch_intakes(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM analysis_batch_intakes
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [
            batch
            for row in rows
            if (batch := self.describe_batch_intake(str(row["id"]))) is not None
        ]

    def update_run(self, run_id: int, **values: Any) -> None:
        allowed = {
            "status",
            "total_units",
            "processed_units",
            "failed_units",
            "ocr_required_units",
            "coverage_percentage",
            "coverage_status",
            "primary_blocked",
            "block_reasons_json",
            "error_message",
            "input_tokens",
            "output_tokens",
            "estimated_cost_usd",
            "started_at",
            "finished_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Field analysis run tidak diizinkan: {sorted(unknown)}")
        if not values:
            return
        normalized = dict(values)
        if "block_reasons_json" in normalized and not isinstance(normalized["block_reasons_json"], str):
            normalized["block_reasons_json"] = _json(normalized["block_reasons_json"])
        if "primary_blocked" in normalized:
            normalized["primary_blocked"] = int(bool(normalized["primary_blocked"]))
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE analysis_runs SET {assignments} WHERE id = ?",
                (*normalized.values(), run_id),
            )

    def add_event(
        self,
        run_id: int,
        *,
        event_type: str,
        stage: str,
        progress: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        safe_progress = max(0, min(100, int(progress)))
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_events (
                    run_id, event_type, stage, progress, message, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, event_type, stage, safe_progress, message, _json(payload or {})),
            )
            return int(cursor.lastrowid)

    def save_engine_result(self, run_id: int, result: EngineResult) -> int:
        result.finish()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO engine_results (
                    run_id, engine_name, engine_version, status, input_checksum,
                    input_refs_json, output_refs_json, coverage_json, warnings_json,
                    metrics_json, output_json, error_message, started_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, engine_name, engine_version, input_checksum) DO UPDATE SET
                    status = excluded.status,
                    input_refs_json = excluded.input_refs_json,
                    output_refs_json = excluded.output_refs_json,
                    coverage_json = excluded.coverage_json,
                    warnings_json = excluded.warnings_json,
                    metrics_json = excluded.metrics_json,
                    output_json = excluded.output_json,
                    error_message = excluded.error_message,
                    started_at = excluded.started_at,
                    completed_at = excluded.completed_at
                """,
                (
                    run_id,
                    result.engine_name,
                    result.engine_version,
                    result.status.value,
                    result.input_checksum,
                    _json(result.input_refs),
                    _json(result.output_refs),
                    _json(result.coverage),
                    _json(result.warnings),
                    _json(result.metrics),
                    _json(result.output),
                    result.error_message,
                    result.started_at,
                    result.completed_at,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM engine_results
                WHERE run_id = ? AND engine_name = ? AND engine_version = ? AND input_checksum = ?
                """,
                (run_id, result.engine_name, result.engine_version, result.input_checksum),
            ).fetchone()
            usage_totals: dict[str, int | float] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0.0,
            }
            for usage_row in conn.execute(
                "SELECT metrics_json FROM engine_results WHERE run_id = ?",
                (run_id,),
            ).fetchall():
                try:
                    metrics = json.loads(usage_row["metrics_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    metrics = {}
                for token_field in ("input_tokens", "output_tokens"):
                    try:
                        usage_totals[token_field] += max(
                            0, int(metrics.get(token_field) or 0)
                        )
                    except (TypeError, ValueError):
                        continue
                try:
                    usage_totals["estimated_cost_usd"] += max(
                        0.0, float(metrics.get("estimated_cost_usd") or 0)
                    )
                except (TypeError, ValueError):
                    pass
            conn.execute(
                """
                UPDATE analysis_runs
                SET input_tokens = ?, output_tokens = ?, estimated_cost_usd = ?
                WHERE id = ?
                """,
                (
                    int(usage_totals["input_tokens"]),
                    int(usage_totals["output_tokens"]),
                    round(float(usage_totals["estimated_cost_usd"]), 9),
                    run_id,
                ),
            )
        if not row:
            raise RuntimeError("Hasil engine gagal disimpan.")
        return int(row["id"])

    def add_security_finding(
        self,
        run_id: int,
        document_id: int,
        finding: SecurityFinding,
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO security_findings (
                    run_id, document_id, severity, code, message, blocking, details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    document_id,
                    finding.severity,
                    finding.code,
                    finding.message,
                    int(finding.blocking),
                    _json(finding.details),
                ),
            )
            return int(cursor.lastrowid)

    def save_document_units(self, run_id: int, units: list[dict[str, Any]]) -> list[int]:
        ids = []
        with self.db.connect() as conn:
            for unit in units:
                text = str(unit.get("text") or "")
                text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
                conn.execute(
                    """
                    INSERT INTO document_units (
                        run_id, unit_key, unit_type, ordinal, heading_path_json,
                        source_location_json, text, text_sha256, char_count, status,
                        warnings_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, unit_key) DO UPDATE SET
                        unit_type = excluded.unit_type,
                        ordinal = excluded.ordinal,
                        heading_path_json = excluded.heading_path_json,
                        source_location_json = excluded.source_location_json,
                        text = excluded.text,
                        text_sha256 = excluded.text_sha256,
                        char_count = excluded.char_count,
                        status = excluded.status,
                        warnings_json = excluded.warnings_json,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        run_id,
                        unit["unit_key"],
                        unit["unit_type"],
                        unit.get("ordinal"),
                        _json(unit.get("heading_path") or []),
                        _json(unit.get("source_location") or {}),
                        text,
                        text_sha256,
                        len(text),
                        unit.get("status") or "pending",
                        _json(unit.get("warnings") or []),
                        _json(unit.get("metadata") or {}),
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM document_units WHERE run_id = ? AND unit_key = ?",
                    (run_id, unit["unit_key"]),
                ).fetchone()
                if row:
                    ids.append(int(row["id"]))
        return ids

    def save_unit_checkpoint(
        self,
        run_id: int,
        *,
        unit_key: str,
        stage: str,
        status: str,
        input_checksum: str,
        output_refs: list[str] | None = None,
        error_message: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_unit_checkpoints (
                    run_id, unit_key, stage, status, input_checksum,
                    output_refs_json, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, unit_key, stage, input_checksum) DO UPDATE SET
                    status = excluded.status,
                    output_refs_json = excluded.output_refs_json,
                    error_message = excluded.error_message,
                    attempt_count = analysis_unit_checkpoints.attempt_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    run_id, unit_key, stage, status, input_checksum,
                    _json(output_refs or []), error_message,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM analysis_unit_checkpoints
                WHERE run_id = ? AND unit_key = ? AND stage = ? AND input_checksum = ?
                """,
                (run_id, unit_key, stage, input_checksum),
            ).fetchone()
        if not row:
            raise RuntimeError("Checkpoint unit gagal disimpan.")
        return int(row["id"])

    def list_unit_checkpoints(
        self,
        run_id: int,
        stage: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM analysis_unit_checkpoints
                WHERE run_id = ? AND (? IS NULL OR stage = ?)
                ORDER BY stage, unit_key, id
                """,
                (run_id, stage, stage),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def checkpoint_summary(self, run_id: int) -> dict[str, dict[str, int]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT stage, status, COUNT(*) AS total
                FROM analysis_unit_checkpoints
                WHERE run_id = ?
                GROUP BY stage, status
                ORDER BY stage, status
                """,
                (run_id,),
            ).fetchall()
        summary: dict[str, dict[str, int]] = {}
        for row in rows:
            summary.setdefault(str(row["stage"]), {})[str(row["status"])] = int(row["total"])
        return summary

    def save_document_structure(self, run_id: int, structure_type: str, structure: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO document_structures (run_id, structure_type, structure_json)
                VALUES (?, ?, ?)
                """,
                (run_id, structure_type, _json(structure)),
            )
            return int(cursor.lastrowid)

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    analysis_runs.*,
                    documents.file_name,
                    documents.content_type,
                    documents.size_bytes,
                    documents.sha256,
                    documents.storage_status,
                    documents.source_system,
                    documents.source_reference,
                    documents.purge_after
                FROM analysis_runs
                JOIN documents ON documents.id = analysis_runs.document_id
                WHERE analysis_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def list_guided_review_runs(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    analysis_runs.id,
                    analysis_runs.status,
                    analysis_runs.analysis_mode,
                    analysis_runs.pipeline_version,
                    analysis_runs.coverage_status,
                    analysis_runs.coverage_percentage,
                    analysis_runs.total_units,
                    analysis_runs.processed_units,
                    analysis_runs.failed_units,
                    analysis_runs.ocr_required_units,
                    analysis_runs.primary_blocked,
                    analysis_runs.block_reasons_json,
                    analysis_runs.created_at,
                    analysis_runs.finished_at,
                    documents.file_name,
                    documents.content_type,
                    documents.size_bytes,
                    documents.sha256,
                    (SELECT COUNT(*) FROM mapping_candidates
                     WHERE mapping_candidates.run_id = analysis_runs.id) AS mapping_count,
                    (SELECT COUNT(*) FROM extracted_facts
                     WHERE extracted_facts.run_id = analysis_runs.id) AS fact_count,
                    labels.id AS active_label_id,
                    labels.outcome AS review_outcome,
                    labels.dataset_status,
                    labels.reviewer_id,
                    labels.created_at AS reviewed_at
                FROM analysis_runs
                JOIN documents ON documents.id = analysis_runs.document_id
                LEFT JOIN expert_review_labels AS labels
                  ON labels.run_id = analysis_runs.id AND labels.is_active = 1
                WHERE analysis_runs.pipeline_version != 'legacy'
                  AND analysis_runs.status NOT IN ('queued', 'running')
                ORDER BY
                    CASE WHEN labels.id IS NULL OR labels.outcome = 'unsure' THEN 0 ELSE 1 END,
                    analysis_runs.created_at DESC,
                    analysis_runs.id DESC
                """
            ).fetchall()
        items = []
        for row in rows:
            item = _decode_json_fields(dict(row))
            if not item.get("active_label_id"):
                item["review_state"] = "pending"
            elif item.get("review_outcome") == "unsure":
                item["review_state"] = "needs_attention"
            else:
                item["review_state"] = "completed"
            items.append(item)
        return items

    def list_events(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_events WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_engine_results(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM engine_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_security_findings(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM security_findings WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_document_units(self, run_id: int, include_text: bool = False) -> list[dict[str, Any]]:
        columns = "*" if include_text else (
            "id, run_id, unit_key, unit_type, ordinal, heading_path_json, "
            "source_location_json, text_sha256, char_count, status, warnings_json, "
            "metadata_json, created_at"
        )
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT {columns} FROM document_units WHERE run_id = ? ORDER BY ordinal, id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def get_document_unit(self, run_id: int, unit_key: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM document_units WHERE run_id = ? AND unit_key = ?",
                (run_id, unit_key),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def save_visual_review_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            previous = conn.execute(
                """
                SELECT id FROM visual_review_decisions
                WHERE run_id = ? AND unit_key = ?
                ORDER BY id DESC LIMIT 1
                """,
                (decision["run_id"], decision["unit_key"]),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO visual_review_decisions (
                    run_id, unit_id, unit_key, review_kind, decision, unit_text_sha256,
                    source_image_sha256, reviewed_text, reviewed_text_sha256,
                    semantic_description, source_location_json, evidence_json,
                    reviewer_id, reason, attested, supersedes_decision_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    decision["run_id"], decision["unit_id"], decision["unit_key"],
                    decision.get("review_kind") or "visual_semantics",
                    decision["decision"], decision["unit_text_sha256"],
                    decision.get("source_image_sha256"),
                    decision.get("reviewed_text") or "",
                    decision.get("reviewed_text_sha256"),
                    decision.get("semantic_description") or "",
                    _json(decision.get("source_location") or {}),
                    _json(decision.get("evidence") or {}),
                    decision["reviewer_id"], decision["reason"],
                    int(previous["id"]) if previous else None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM visual_review_decisions WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        if not row:
            raise RuntimeError("Keputusan review visual gagal disimpan.")
        return _decode_json_fields(dict(row))

    def list_visual_review_decisions(
        self,
        run_id: int,
        unit_key: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM visual_review_decisions
                WHERE run_id = ? AND (? IS NULL OR unit_key = ?)
                ORDER BY id
                """,
                (run_id, unit_key, unit_key),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def latest_visual_review_decisions(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT decisions.*
                FROM visual_review_decisions AS decisions
                JOIN (
                    SELECT unit_key, MAX(id) AS id
                    FROM visual_review_decisions
                    WHERE run_id = ?
                    GROUP BY unit_key
                ) AS latest ON latest.id = decisions.id
                ORDER BY decisions.unit_key
                """,
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def visual_review_snapshot(self, run_id: int | None) -> dict[str, Any]:
        if not run_id:
            return {
                "source_run_id": None,
                "decision_count": 0,
                "actionable_count": 0,
                "checksum": None,
                "decisions": [],
            }
        decisions = self.latest_visual_review_decisions(int(run_id))
        canonical = [
            {
                "id": int(item["id"]),
                "unit_key": item["unit_key"],
                "review_kind": item.get("review_kind") or "visual_semantics",
                "decision": item["decision"],
                "unit_text_sha256": item["unit_text_sha256"],
                "source_image_sha256": item.get("source_image_sha256"),
                "reviewed_text_sha256": item.get("reviewed_text_sha256"),
                "semantic_description_sha256": hashlib.sha256(
                    str(item.get("semantic_description") or "").encode("utf-8")
                ).hexdigest(),
                "evidence_sha256": hashlib.sha256(
                    json.dumps(
                        item.get("evidence") or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "reviewer_id": item["reviewer_id"],
            }
            for item in decisions
        ]
        checksum = (
            hashlib.sha256(
                json.dumps(
                    canonical,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if canonical else None
        )
        return {
            "source_run_id": int(run_id),
            "decision_count": len(decisions),
            "actionable_count": sum(
                item["decision"] in {"confirmed", "corrected", "not_evidence"}
                for item in decisions
            ),
            "checksum": checksum,
            "decisions": decisions,
        }

    def apply_visual_review_decisions(
        self,
        source_run_id: int | None,
        units: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        snapshot = self.visual_review_snapshot(source_run_id)
        latest = {item["unit_key"]: item for item in snapshot["decisions"]}
        applied_ids: list[int] = []
        stale_ids: list[int] = []
        pending_ids: list[int] = []
        updated: list[dict[str, Any]] = []
        for original in units:
            unit = {**original, "metadata": dict(original.get("metadata") or {})}
            unit["warnings"] = list(original.get("warnings") or [])
            decision = latest.get(str(unit.get("unit_key") or ""))
            if not decision:
                updated.append(unit)
                continue
            current_text = str(unit.get("text") or "")
            current_text_sha256 = hashlib.sha256(
                current_text.encode("utf-8")
            ).hexdigest()
            current_image_sha256 = str(
                unit["metadata"].get("ocr_source_image_sha256") or ""
            )
            review_kind = str(decision.get("review_kind") or "visual_semantics")
            evidence = decision.get("evidence") or {}
            current_semantic_regions_sha256 = hashlib.sha256(
                json.dumps(
                    unit["metadata"].get("semantic_regions") or [],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if (
                current_text_sha256 != str(decision.get("unit_text_sha256") or "")
                or current_image_sha256
                != str(decision.get("source_image_sha256") or "")
                or (
                    review_kind == "ocr_rescue"
                    and str(unit["metadata"].get("ocr_review_candidate_text_sha256") or "")
                    != str(evidence.get("ocr_review_candidate_text_sha256") or "")
                )
                or (
                    evidence.get("semantic_regions_sha256")
                    and current_semantic_regions_sha256
                    != str(evidence.get("semantic_regions_sha256") or "")
                )
            ):
                stale_ids.append(int(decision["id"]))
                updated.append(unit)
                continue
            if decision["decision"] == "unsure":
                pending_ids.append(int(decision["id"]))
                updated.append(unit)
                continue
            visual_pending = (
                unit["metadata"].get("visual_semantics_status")
                == "pending_review_or_vision"
            )
            ocr_rescue_pending = (
                unit.get("status") == "ocr_required"
                and bool(
                    unit["metadata"].get("ocr_review_candidate_text_sha256")
                    or unit["metadata"].get("ocr_manual_review_required")
                )
            )
            if (
                (review_kind == "visual_semantics" and not visual_pending)
                or (review_kind == "ocr_rescue" and not ocr_rescue_pending)
            ):
                stale_ids.append(int(decision["id"]))
                updated.append(unit)
                continue

            metadata = unit["metadata"]
            review_provenance = {
                "decision_id": int(decision["id"]),
                "source_run_id": int(source_run_id or 0),
                "review_kind": review_kind,
                "decision": decision["decision"],
                "reviewer_id": decision["reviewer_id"],
                "reviewed_at": decision["created_at"],
                "snapshot_checksum": snapshot["checksum"],
                "semantic_regions_sha256": current_semantic_regions_sha256,
            }
            metadata[
                "ocr_rescue" if review_kind == "ocr_rescue" else "visual_review"
            ] = review_provenance
            metadata["visual_semantics_status"] = (
                "human_not_evidence"
                if decision["decision"] == "not_evidence"
                else "human_verified"
            )
            if decision.get("semantic_description"):
                metadata["visual_semantic_description"] = str(
                    decision["semantic_description"]
                )
            reviewed_semantic_regions = evidence.get("reviewed_semantic_regions") or []
            if reviewed_semantic_regions and decision["decision"] in {"confirmed", "corrected"}:
                metadata["semantic_regions"] = [
                    *(metadata.get("semantic_regions") or []),
                    *reviewed_semantic_regions,
                ][:500]
                metadata["semantic_region_count"] = len(metadata["semantic_regions"])
            unit["status"] = "processed"
            if decision["decision"] in {"confirmed", "corrected"}:
                unit["text"] = str(decision.get("reviewed_text") or "")
                metadata["ocr_regions"] = []
                metadata["ocr_region_count"] = 0
                if review_kind == "ocr_rescue":
                    metadata["ocr_method"] = "human_transcription_v1"
                    metadata["ocr_provider"] = "human_review"
                    metadata["human_transcribed_ocr"] = True
                elif decision["decision"] == "corrected":
                    metadata["human_corrected_visual_text"] = True
            elif decision["decision"] == "not_evidence":
                unit["text"] = ""
                metadata["human_not_evidence"] = True
            unit["warnings"] = [
                warning for warning in unit["warnings"]
                if (
                    "makna visual" not in warning.lower()
                    and (review_kind != "ocr_rescue" or (
                        "ocr" not in warning.lower()
                        and "vision" not in warning.lower()
                    ))
                )
            ]
            applied_ids.append(int(decision["id"]))
            updated.append(unit)
        return updated, {
            **snapshot,
            "applied_count": len(applied_ids),
            "applied_decision_ids": applied_ids,
            "stale_count": len(stale_ids),
            "stale_decision_ids": stale_ids,
            "pending_count": len(pending_ids),
            "pending_decision_ids": pending_ids,
        }

    def list_visual_review_items(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    units.*, runs.status AS run_status,
                    runs.coverage_status, runs.coverage_percentage,
                    runs.pipeline_version, runs.created_at AS run_created_at,
                    documents.file_name, documents.content_type,
                    documents.sha256 AS document_sha256
                FROM document_units AS units
                JOIN analysis_runs AS runs ON runs.id = units.run_id
                JOIN documents ON documents.id = runs.document_id
                WHERE runs.id IN (
                    SELECT MAX(candidate.id)
                    FROM analysis_runs AS candidate
                    WHERE candidate.pipeline_version != 'legacy'
                      AND candidate.status NOT IN ('queued', 'running', 'intake')
                    GROUP BY candidate.document_id
                )
                ORDER BY runs.created_at DESC, runs.id DESC, units.ordinal, units.id
                """
            ).fetchall()
        decisions_by_key: dict[tuple[int, str], dict[str, Any]] = {}
        run_ids = {int(row["run_id"]) for row in rows}
        for run_id in run_ids:
            for decision in self.latest_visual_review_decisions(run_id):
                decisions_by_key[(run_id, str(decision["unit_key"]))] = decision
        items = []
        for row in rows:
            item = _decode_json_fields(dict(row))
            metadata = item.get("metadata") or {}
            if metadata.get("visual_semantics_status") == "pending_review_or_vision":
                review_kind = "visual_semantics"
                review_text = str(item.get("text") or "")
            elif (
                item.get("status") == "ocr_required"
                and metadata.get("ocr_source_image_sha256")
                and (
                    metadata.get("ocr_review_candidate_text_sha256")
                    or metadata.get("ocr_manual_review_required")
                )
            ):
                review_kind = "ocr_rescue"
                review_text = str(metadata.get("ocr_review_candidate_text") or "")
            else:
                continue
            decision = decisions_by_key.get((int(item["run_id"]), str(item["unit_key"])))
            if not decision:
                review_state = "pending"
            elif decision["decision"] == "unsure":
                review_state = "needs_attention"
            else:
                review_state = "reviewed"
            item.pop("text", None)
            items.append({
                **item,
                "review_kind": review_kind,
                "ocr_text": review_text[:20000],
                "review_state": review_state,
                "latest_decision": decision,
            })
        return items

    def list_document_structures(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM document_structures WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_extracted_facts(self, run_id: int, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = []
        with self.db.connect() as conn:
            for fact in facts:
                evidence_role = str(fact.get("evidence_role") or "context")
                if evidence_role not in VALID_EVIDENCE_ROLES:
                    raise ValueError(f"Peran evidence tidak valid: {evidence_role}")
                conn.execute(
                    """
                    INSERT INTO extracted_facts (
                        run_id, fact_key, claim, fact_type, organization, period,
                        confidence, extraction_method, status, evidence_role,
                        evidence_role_method
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, fact_key) DO UPDATE SET
                        claim = excluded.claim,
                        fact_type = excluded.fact_type,
                        organization = excluded.organization,
                        period = excluded.period,
                        confidence = excluded.confidence,
                        extraction_method = excluded.extraction_method,
                        status = excluded.status,
                        evidence_role = excluded.evidence_role,
                        evidence_role_method = excluded.evidence_role_method
                    """,
                    (
                        run_id,
                        fact["fact_key"],
                        fact["claim"],
                        fact.get("fact_type") or "unknown",
                        fact.get("organization"),
                        fact.get("period"),
                        fact.get("confidence"),
                        fact.get("extraction_method") or "deterministic",
                        fact.get("status") or "extracted",
                        evidence_role,
                        fact.get("evidence_role_method") or "legacy_default_v1",
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM extracted_facts WHERE run_id = ? AND fact_key = ?",
                    (run_id, fact["fact_key"]),
                ).fetchone()
                if not row:
                    continue
                fact_id = int(row["id"])
                source = fact.get("source") or {}
                unit_id = source.get("unit_id")
                if unit_id:
                    conn.execute(
                        """
                        INSERT INTO fact_sources (
                            fact_id, unit_id, source_location_json, source_quote
                        )
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(fact_id, unit_id, source_quote) DO UPDATE SET
                            source_location_json = excluded.source_location_json
                        """,
                        (
                            fact_id,
                            unit_id,
                            _json(source.get("source_location") or {}),
                            source.get("source_quote") or fact["claim"],
                        ),
                    )
                item = dict(row)
                item["source"] = source
                saved.append(item)
        return saved

    def list_facts(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM extracted_facts WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            sources = conn.execute(
                """
                SELECT fact_sources.*, document_units.unit_key,
                       CASE
                           WHEN TRIM(fact_sources.source_quote) != ''
                            AND INSTR(document_units.text, fact_sources.source_quote) > 0
                           THEN 1 ELSE 0
                       END AS source_quote_verified
                FROM fact_sources
                JOIN document_units ON document_units.id = fact_sources.unit_id
                JOIN extracted_facts ON extracted_facts.id = fact_sources.fact_id
                WHERE extracted_facts.run_id = ?
                ORDER BY fact_sources.id
                """,
                (run_id,),
            ).fetchall()
        by_fact: dict[int, list[dict[str, Any]]] = {}
        for row in sources:
            item = _decode_json_fields(dict(row))
            item["source_quote_verified"] = bool(item.get("source_quote_verified"))
            by_fact.setdefault(int(item["fact_id"]), []).append(item)
        result = []
        for row in rows:
            item = dict(row)
            item["sources"] = by_fact.get(int(item["id"]), [])
            result.append(item)
        return result

    def parameter_index(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    parameters.id, parameters.kk_id, parameters.kode,
                    parameters.matrix_subunsur_name, parameters.parameter_no,
                    parameters.uraian, parameters.kode_spip, parameters.kode_mri,
                    parameters.kode_iepk, parameters.grades_json,
                    parameters.cara_pengujian, folders.kk_title,
                    folders.subunsur_name, folders.unsur, folders.evidence_hint
                FROM parameters
                JOIN folders
                  ON folders.kk_id = parameters.kk_id
                 AND folders.kode = parameters.kode
                ORDER BY parameters.kk_id, parameters.kode, parameters.source_row, parameters.id
                """
            ).fetchall()
        parameters = []
        for row in rows:
            item = _decode_json_fields(dict(row))
            number = str(item.get("parameter_no") or "").strip()
            item["detail_kode"] = f"{item['kode']}.{number}" if number else item["kode"]
            parameters.append(item)
        return parameters

    def list_rule_approvals(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM domain_rule_approvals
                ORDER BY kk_id, kode, detail_kode, grade
                """
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_rule_approval_events(
        self,
        *,
        kk_id: str | None = None,
        kode: str | None = None,
        detail_kode: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM domain_rule_approval_events
                WHERE (? IS NULL OR kk_id = ?)
                  AND (? IS NULL OR kode = ?)
                  AND (? IS NULL OR detail_kode = ?)
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    kk_id, kk_id, kode, kode, detail_kode, detail_kode,
                    max(1, min(2000, int(limit))),
                ),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_evaluation_report(self, report: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_reports (
                    pipeline_version, dataset_name, dataset_status, case_count,
                    metrics_json, report_sha256, reviewer_id, notes,
                    dataset_sha256, generation_method, details_json,
                    release_authority
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_version, dataset_name, report_sha256) DO NOTHING
                """,
                (
                    report["pipeline_version"], report["dataset_name"],
                    report["dataset_status"], report["case_count"],
                    _json(report["metrics"]), report["report_sha256"],
                    report["reviewer_id"], report.get("notes") or "",
                    report.get("dataset_sha256"),
                    report.get("generation_method") or "manual_import",
                    _json(report.get("details") or {}),
                    int(bool(report.get("release_authority"))),
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM evaluation_reports
                WHERE pipeline_version = ? AND dataset_name = ? AND report_sha256 = ?
                """,
                (report["pipeline_version"], report["dataset_name"], report["report_sha256"]),
            ).fetchone()
        if not row:
            raise RuntimeError("Evaluation report gagal disimpan.")
        return _decode_json_fields(dict(row))

    def list_evaluation_reports(self, pipeline_version: str | None = None) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM evaluation_reports
                WHERE ? IS NULL OR pipeline_version = ?
                ORDER BY created_at DESC, id DESC
                """,
                (pipeline_version, pipeline_version),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def get_evaluation_report(self, report_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM evaluation_reports WHERE id = ?",
                (int(report_id),),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def save_release_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO analysis_release_events (
                    release_cycle_id, release_version, stage, decision,
                    pipeline_version, rule_version, model, dataset_sha256,
                    comparison_report_sha256, evaluation_report_id,
                    stable_cycle, rollback_rehearsed, critical_incident_count,
                    reviewer_id, reason, evidence_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["release_cycle_id"],
                    event["release_version"],
                    event["stage"],
                    event["decision"],
                    event["pipeline_version"],
                    event["rule_version"],
                    event["model"],
                    event.get("dataset_sha256"),
                    event.get("comparison_report_sha256"),
                    event.get("evaluation_report_id"),
                    int(bool(event.get("stable_cycle"))),
                    int(bool(event.get("rollback_rehearsed"))),
                    max(0, int(event.get("critical_incident_count") or 0)),
                    event["reviewer_id"],
                    event["reason"],
                    _json(event.get("evidence") or {}),
                ),
            )
            row = conn.execute(
                "SELECT * FROM analysis_release_events WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        if not row:
            raise RuntimeError("Release evidence event gagal disimpan.")
        return self._decode_release_event(dict(row))

    def list_release_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM analysis_release_events
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(2000, int(limit))),),
            ).fetchall()
        return [self._decode_release_event(dict(row)) for row in rows]

    def record_legacy_usage(self, usage_kind: str, source: str) -> dict[str, Any]:
        kind = str(usage_kind or "").strip().lower()
        origin = str(source or "").strip().lower()
        for field_name, value in (("usage_kind", kind), ("source", origin)):
            if not value or len(value) > 64 or any(
                not (character.isalnum() or character == "_")
                for character in value
            ):
                raise ValueError(f"{field_name} telemetry tidak valid.")
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO legacy_pipeline_usage_daily (
                    usage_date, usage_kind, source, call_count,
                    first_used_at, last_used_at
                )
                VALUES (DATE('now'), ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(usage_date, usage_kind, source) DO UPDATE SET
                    call_count = legacy_pipeline_usage_daily.call_count + 1,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (kind, origin),
            )
            row = conn.execute(
                """
                SELECT * FROM legacy_pipeline_usage_daily
                WHERE usage_date = DATE('now') AND usage_kind = ? AND source = ?
                """,
                (kind, origin),
            ).fetchone()
        if not row:
            raise RuntimeError("Telemetry pemakaian legacy gagal disimpan.")
        return dict(row)

    def legacy_usage_summary(self, observation_start: str | None = None) -> dict[str, Any]:
        with self.db.connect() as conn:
            migration = conn.execute(
                "SELECT applied_at FROM schema_migrations WHERE version = 23"
            ).fetchone()
            total = conn.execute(
                """
                SELECT COALESCE(SUM(call_count), 0) AS call_count,
                       MIN(first_used_at) AS first_used_at,
                       MAX(last_used_at) AS last_used_at
                FROM legacy_pipeline_usage_daily
                """
            ).fetchone()
            by_kind_rows = conn.execute(
                """
                SELECT usage_kind, source, SUM(call_count) AS call_count,
                       MAX(last_used_at) AS last_used_at
                FROM legacy_pipeline_usage_daily
                GROUP BY usage_kind, source
                ORDER BY usage_kind, source
                """
            ).fetchall()
            observed_call_count = 0
            coverage_valid = False
            if observation_start:
                observed = conn.execute(
                    """
                    SELECT COALESCE(SUM(call_count), 0) AS call_count
                    FROM legacy_pipeline_usage_daily
                    WHERE DATETIME(last_used_at) >= DATETIME(?)
                    """,
                    (observation_start,),
                ).fetchone()
                observed_call_count = int(observed["call_count"] or 0)
                if migration:
                    coverage = conn.execute(
                        "SELECT DATETIME(?) >= DATETIME(?) AS valid",
                        (observation_start, migration["applied_at"]),
                    ).fetchone()
                    coverage_valid = bool(coverage["valid"])
        telemetry_started_at = migration["applied_at"] if migration else None
        return {
            "instrumented": bool(telemetry_started_at),
            "telemetry_started_at": telemetry_started_at,
            "observation_started_at": observation_start,
            "observation_coverage_valid": coverage_valid,
            "observed_call_count": observed_call_count,
            "total_call_count": int(total["call_count"] or 0),
            "first_used_at": total["first_used_at"],
            "last_used_at": total["last_used_at"],
            "by_kind": [
                {
                    "usage_kind": row["usage_kind"],
                    "source": row["source"],
                    "call_count": int(row["call_count"] or 0),
                    "last_used_at": row["last_used_at"],
                }
                for row in by_kind_rows
            ],
            "contains_document_content": False,
        }

    def release_evidence_summary(self) -> dict[str, Any]:
        from app.analysis.expert_evaluation import SERVER_DERIVED_GENERATION_METHOD

        with self.db.connect() as conn:
            latest = conn.execute(
                """
                SELECT events.*
                FROM analysis_release_events events
                JOIN (
                    SELECT release_cycle_id, MAX(id) AS max_id
                    FROM analysis_release_events
                    GROUP BY release_cycle_id
                ) current ON current.max_id = events.id
                ORDER BY events.id DESC
                """
            ).fetchall()
            authoritative_reports = {
                int(row["id"]): dict(row)
                for row in conn.execute(
                    """
                    SELECT id, report_sha256, generation_method, release_authority
                    FROM evaluation_reports
                    WHERE release_authority = 1
                    """
                ).fetchall()
            }
        cycles = [self._decode_release_event(dict(row)) for row in latest]
        stable_candidates = [
            item for item in cycles if item["decision"] == "passed"
            and item["stage"] in {"canary", "general"}
            and item["stable_cycle"]
            and item["critical_incident_count"] == 0
        ]
        stable = []
        for item in stable_candidates:
            snapshot = (item.get("evidence") or {}).get("gate_snapshot") or {}
            report = authoritative_reports.get(int(item.get("evaluation_report_id") or 0))
            if (
                snapshot.get("validated")
                and snapshot.get("evaluation_release_authority") is True
                and report
                and bool(report.get("release_authority"))
                and report.get("generation_method") == SERVER_DERIVED_GENERATION_METHOD
                and snapshot.get("evaluation_report_sha256") == report.get("report_sha256")
                and snapshot.get("evaluation_recomputed_sha256") == report.get("report_sha256")
            ):
                stable.append(item)
        rollback_rehearsed = any(item["rollback_rehearsed"] for item in stable)
        observation_start = min(
            (str(item["created_at"]) for item in stable if item.get("created_at")),
            default=None,
        ) if len(stable) >= 2 else None
        legacy_usage = self.legacy_usage_summary(observation_start)
        deprecation_reasons = []
        if len(stable) < 2:
            deprecation_reasons.append("Dua siklus canary/general yang stabil belum tercatat.")
        invalidated_stable_cycle_count = len(stable_candidates) - len(stable)
        if invalidated_stable_cycle_count:
            deprecation_reasons.append(
                f"{invalidated_stable_cycle_count} siklus stabil lama tidak memiliki authority/recompute V26."
            )
        if not rollback_rehearsed:
            deprecation_reasons.append("Minimal satu siklus stabil belum membuktikan rollback rehearsal.")
        if not legacy_usage["instrumented"]:
            deprecation_reasons.append("Telemetry pemakaian pipeline V1 belum aktif.")
        elif not legacy_usage["observation_coverage_valid"]:
            deprecation_reasons.append("Observation window V1 belum tercakup telemetry sejak siklus stabil pertama.")
        if legacy_usage["observed_call_count"]:
            deprecation_reasons.append(
                f"Pipeline V1 masih dipanggil {legacy_usage['observed_call_count']} kali sejak observation window dimulai."
            )
        return {
            "release_cycle_count": len(cycles),
            "stable_release_cycle_count": len(stable),
            "invalidated_stable_cycle_count": invalidated_stable_cycle_count,
            "rollback_rehearsed": any(item["rollback_rehearsed"] for item in cycles),
            "latest_cycles": cycles,
            "legacy_usage": legacy_usage,
            "legacy_deprecation_reasons": deprecation_reasons,
            "legacy_deprecation_eligible": not deprecation_reasons,
        }

    @staticmethod
    def _decode_release_event(item: dict[str, Any]) -> dict[str, Any]:
        raw = item.pop("evidence_json", None)
        try:
            item["evidence"] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            item["evidence"] = {}
        item["stable_cycle"] = bool(item.get("stable_cycle"))
        item["rollback_rehearsed"] = bool(item.get("rollback_rehearsed"))
        item["critical_incident_count"] = int(item.get("critical_incident_count") or 0)
        return item

    def save_rule_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        return self.save_rule_approval_batch([approval])[0]

    def save_rule_approval_batch(
        self,
        approvals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not approvals:
            return []
        with self.db.connect() as conn:
            for approval in approvals:
                previous = conn.execute(
                    """
                    SELECT id FROM domain_rule_approval_events
                    WHERE kk_id = ? AND kode = ? AND detail_kode = ?
                      AND grade = ? AND rule_version = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (
                        approval["kk_id"], approval["kode"], approval["detail_kode"],
                        approval["grade"], approval["rule_version"],
                    ),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO domain_rule_approval_events (
                        kk_id, kode, detail_kode, grade, rule_version, rule_checksum,
                        status, reviewer_id, reason, rule_definition_json,
                        supersedes_event_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval["kk_id"], approval["kode"], approval["detail_kode"],
                        approval["grade"], approval["rule_version"],
                        approval["rule_checksum"], approval["status"],
                        approval["reviewer_id"], approval["reason"],
                        _json(approval["rule_definition"]),
                        int(previous["id"]) if previous else None,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO domain_rule_approvals (
                        kk_id, kode, detail_kode, grade, rule_version, rule_checksum,
                        status, reviewer_id, reason, rule_definition_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kk_id, kode, detail_kode, grade, rule_version) DO UPDATE SET
                        rule_checksum = excluded.rule_checksum,
                        status = excluded.status,
                        reviewer_id = excluded.reviewer_id,
                        reason = excluded.reason,
                        rule_definition_json = excluded.rule_definition_json,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (
                        approval["kk_id"], approval["kode"], approval["detail_kode"],
                        approval["grade"], approval["rule_version"],
                        approval["rule_checksum"], approval["status"],
                        approval["reviewer_id"], approval["reason"],
                        _json(approval["rule_definition"]),
                    ),
                )
            rows = []
            for approval in approvals:
                row = conn.execute(
                    """
                    SELECT * FROM domain_rule_approvals
                    WHERE kk_id = ? AND kode = ? AND detail_kode = ?
                      AND grade = ? AND rule_version = ?
                    """,
                    (
                        approval["kk_id"], approval["kode"], approval["detail_kode"],
                        approval["grade"], approval["rule_version"],
                    ),
                ).fetchone()
                if not row:
                    raise RuntimeError("Pengesahan rule gagal disimpan.")
                rows.append(_decode_json_fields(dict(row)))
        return rows

    def save_vision_capability_probe(self, report: dict[str, Any], reviewer_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO vision_capability_probes (
                    provider, model, api_surface, status, report_sha256,
                    expected_tokens_json, observed_text, warnings_json,
                    error_message, reviewer_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_sha256) DO UPDATE SET
                    reviewer_id = excluded.reviewer_id
                """,
                (
                    report["provider"], report["model"], report["api_surface"],
                    report["status"], report["report_sha256"],
                    _json(report.get("expected_tokens") or []),
                    report.get("observed_text") or "",
                    _json(report.get("warnings") or []),
                    report.get("error_message"), reviewer_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM vision_capability_probes WHERE report_sha256 = ?",
                (report["report_sha256"],),
            ).fetchone()
        if not row:
            raise RuntimeError("Hasil uji capability vision gagal disimpan.")
        return _decode_json_fields(dict(row))

    def list_vision_capability_probes(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vision_capability_probes ORDER BY id DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_vision_governance_decision(
        self,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            previous = conn.execute(
                """
                SELECT id FROM vision_governance_decisions
                WHERE scope = ? AND provider = ? AND model = ? AND api_surface = ?
                  AND policy_version = ? AND is_active = 1
                """,
                (
                    decision["scope"], decision["provider"], decision["model"],
                    decision["api_surface"], decision["policy_version"],
                ),
            ).fetchone()
            if previous:
                conn.execute(
                    "UPDATE vision_governance_decisions SET is_active = 0 WHERE id = ?",
                    (int(previous["id"]),),
                )
            cursor = conn.execute(
                """
                INSERT INTO vision_governance_decisions (
                    scope, status, provider, model, api_surface, sensitivity_scope,
                    evidence_sha256, policy_version, reviewer_id, reason,
                    expires_at, is_active, supersedes_decision_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    decision["scope"], decision["status"], decision["provider"],
                    decision["model"], decision["api_surface"],
                    decision.get("sensitivity_scope") or "restricted",
                    decision.get("evidence_sha256"), decision["policy_version"],
                    decision["reviewer_id"], decision["reason"],
                    decision["expires_at"], int(previous["id"]) if previous else None,
                ),
            )
            row = conn.execute(
                "SELECT * FROM vision_governance_decisions WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        if not row:
            raise RuntimeError("Keputusan governance vision gagal disimpan.")
        return _decode_json_fields(dict(row))

    def list_vision_governance_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vision_governance_decisions ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def vision_governance_status(self, settings: Any, *, renderer_available: bool) -> dict[str, Any]:
        provider = str(settings.ai_provider)
        model = str(settings.deepseek_model)
        api_surface = "chat_completions_vision"
        with self.db.connect() as conn:
            decisions = conn.execute(
                """
                SELECT * FROM vision_governance_decisions
                WHERE provider = ? AND model = ? AND api_surface = ?
                  AND policy_version = ? AND is_active = 1
                  AND expires_at > CURRENT_TIMESTAMP
                ORDER BY id DESC
                """,
                (provider, model, api_surface, "vision-governance-v1"),
            ).fetchall()
            passed_probe_hashes = {
                str(row["report_sha256"])
                for row in conn.execute(
                    """
                    SELECT report_sha256 FROM vision_capability_probes
                    WHERE provider = ? AND model = ? AND api_surface = ? AND status = 'passed'
                    """,
                    (provider, model, api_surface),
                ).fetchall()
            }
        current = {str(row["scope"]): _decode_json_fields(dict(row)) for row in decisions}
        capability = current.get("capability_validation")
        consent = current.get("external_data_processing")
        capability_approved = bool(
            capability
            and capability.get("status") == "approved"
            and capability.get("evidence_sha256") in passed_probe_hashes
        )
        consent_approved = bool(
            consent
            and consent.get("status") == "approved"
            and consent.get("sensitivity_scope") == "restricted"
        )
        checks = {
            "feature_enabled": bool(settings.vision_analysis_enabled),
            "provider_flag_validated": bool(settings.analysis_vision_provider_validated),
            "api_key_configured": bool(settings.has_ai_key),
            "renderer_available": bool(renderer_available),
            "capability_approved": capability_approved,
            "restricted_data_consent_approved": consent_approved,
        }
        return {
            "policy_version": "vision-governance-v1",
            "provider": provider,
            "model": model,
            "api_surface": api_surface,
            "checks": checks,
            "effective": all(checks.values()),
            "capability_decision": capability,
            "data_processing_decision": consent,
            "reasons": [key for key, value in checks.items() if not value],
        }

    def save_mapping_candidates(self, run_id: int, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        saved = []
        with self.db.connect() as conn:
            for candidate in candidates:
                conn.execute(
                    """
                    INSERT INTO mapping_candidates (
                        run_id, kk_id, kode, detail_kode, retrieval_score,
                        mapping_score, rag_rank, rag_relevance, rag_method,
                        status, supporting_fact_ids_json,
                        reasons_json, missing_evidence_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, kk_id, kode, detail_kode) DO UPDATE SET
                        retrieval_score = excluded.retrieval_score,
                        mapping_score = excluded.mapping_score,
                        rag_rank = excluded.rag_rank,
                        rag_relevance = excluded.rag_relevance,
                        rag_method = excluded.rag_method,
                        status = excluded.status,
                        supporting_fact_ids_json = excluded.supporting_fact_ids_json,
                        reasons_json = excluded.reasons_json,
                        missing_evidence_json = excluded.missing_evidence_json
                    """,
                    (
                        run_id,
                        candidate["kk_id"],
                        candidate["kode"],
                        candidate["detail_kode"],
                        candidate.get("retrieval_score") or 0,
                        candidate.get("mapping_score") or 0,
                        candidate.get("rag_rank"),
                        candidate.get("rag_relevance"),
                        candidate.get("rag_method"),
                        candidate.get("status") or "candidate",
                        _json(candidate.get("supporting_fact_ids") or []),
                        _json(candidate.get("reasons") or []),
                        _json(candidate.get("missing_evidence") or []),
                    ),
                )
                row = conn.execute(
                    """
                    SELECT * FROM mapping_candidates
                    WHERE run_id = ? AND kk_id = ? AND kode = ? AND detail_kode = ?
                    """,
                    (run_id, candidate["kk_id"], candidate["kode"], candidate["detail_kode"]),
                ).fetchone()
                if row:
                    saved.append(_decode_json_fields(dict(row)))
        return saved

    def list_mapping_candidates(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mapping_candidates
                WHERE run_id = ?
                ORDER BY
                    CASE WHEN rag_rank IS NULL THEN 1 ELSE 0 END,
                    rag_rank,
                    mapping_score DESC,
                    retrieval_score DESC,
                    id
                """,
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_grade_assessment(self, run_id: int, assessment: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO grade_assessments (
                    run_id, mapping_candidate_id, candidate_grade, grade_ceiling,
                    rule_version, rule_trace_json, missing_requirements_json,
                    primary_allowed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    assessment["mapping_candidate_id"],
                    assessment.get("candidate_grade"),
                    assessment.get("grade_ceiling"),
                    assessment["rule_version"],
                    _json(assessment.get("rule_trace") or {}),
                    _json(assessment.get("missing_requirements") or []),
                    int(bool(assessment.get("primary_allowed"))),
                ),
            )
            return int(cursor.lastrowid)

    def list_grade_assessments(self, run_id: int, include_history: bool = False) -> list[dict[str, Any]]:
        active_filter = "" if include_history else "AND is_active = 1"
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM grade_assessments WHERE run_id = ? {active_filter} ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def save_verification_result(self, run_id: int, result: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO verification_results (
                    run_id, mapping_candidate_id, verifier_type, status,
                    findings_json, source_coverage_ok, grade_rule_ok,
                    period_ok, organization_ok
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.get("mapping_candidate_id"),
                    result.get("verifier_type") or "deterministic",
                    result["status"],
                    _json(result.get("findings") or []),
                    int(bool(result.get("source_coverage_ok"))),
                    int(bool(result.get("grade_rule_ok"))),
                    int(bool(result.get("period_ok"))),
                    int(bool(result.get("organization_ok"))),
                ),
            )
            return int(cursor.lastrowid)

    def list_verification_results(self, run_id: int, include_history: bool = False) -> list[dict[str, Any]]:
        active_filter = "" if include_history else "AND is_active = 1"
        with self.db.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM verification_results WHERE run_id = ? {active_filter} ORDER BY id",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = _decode_json_fields(dict(row))
            for field in ("source_coverage_ok", "grade_rule_ok", "period_ok", "organization_ok"):
                item[field] = bool(item[field])
            result.append(item)
        return result

    def supersede_active_assessments(self, run_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE grade_assessments SET is_active = 0 WHERE run_id = ? AND is_active = 1",
                (run_id,),
            )
            conn.execute(
                "UPDATE verification_results SET is_active = 0 WHERE run_id = ? AND is_active = 1",
                (run_id,),
            )

    def save_human_review_decision(self, run_id: int, decision: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO human_review_decisions (
                    run_id, mapping_candidate_id, reviewer_id, decision,
                    original_mapping_json, final_mapping_json, reason,
                    override_warnings_json, pipeline_version, rule_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    decision.get("mapping_candidate_id"),
                    decision["reviewer_id"],
                    decision["decision"],
                    _json(decision.get("original_mapping") or {}),
                    _json(decision.get("final_mapping") or {}),
                    decision["reason"],
                    _json(decision.get("override_warnings") or []),
                    decision["pipeline_version"],
                    decision["rule_version"],
                ),
            )
            return int(cursor.lastrowid)

    def list_human_review_decisions(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM human_review_decisions WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for source, target, default in (
                ("original_mapping_json", "original_mapping", {}),
                ("final_mapping_json", "final_mapping", {}),
                ("override_warnings_json", "override_warnings", []),
            ):
                raw = item.pop(source, None)
                try:
                    item[target] = json.loads(raw) if raw else default
                except (json.JSONDecodeError, TypeError):
                    item[target] = default
            result.append(item)
        return result

    def save_expert_review_label(self, run_id: int, label: dict[str, Any]) -> dict[str, Any]:
        expected_template_status = str(
            label.get("expected_template_status") or "not_assessed"
        )
        if expected_template_status not in VALID_EXPERT_TEMPLATE_STATUSES:
            raise ValueError("Status template expert review tidak valid.")
        with self.db.connect() as conn:
            active = conn.execute(
                "SELECT id FROM expert_review_labels WHERE run_id = ? AND is_active = 1",
                (run_id,),
            ).fetchone()
            supersedes = int(active["id"]) if active else None
            if supersedes:
                conn.execute(
                    "UPDATE expert_review_labels SET is_active = 0 WHERE id = ?",
                    (supersedes,),
                )
            cursor = conn.execute(
                """
                INSERT INTO expert_review_labels (
                    run_id, reviewer_id, outcome, selected_mapping_candidate_id,
                    selected_fact_ids_json, expected_mappings_json,
                    expected_source_locations_json, reason, dataset_status,
                    dataset_partition, expected_template_status,
                    is_active, supersedes_label_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    run_id,
                    label["reviewer_id"],
                    label["outcome"],
                    label.get("selected_mapping_candidate_id"),
                    _json(label.get("selected_fact_ids") or []),
                    _json(label.get("expected_mappings") or []),
                    _json(label.get("expected_source_locations") or []),
                    label["reason"],
                    label.get("dataset_status") or "expert_candidate",
                    label.get("dataset_partition") or "evaluation",
                    expected_template_status,
                    supersedes,
                ),
            )
            row = conn.execute(
                "SELECT * FROM expert_review_labels WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        if not row:
            raise RuntimeError("Label expert review gagal disimpan.")
        return _decode_json_fields(dict(row))

    def active_expert_review_label(self, run_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM expert_review_labels
                WHERE run_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    def list_expert_review_label_history(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM expert_review_labels WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def list_expert_dataset_items(self) -> list[dict[str, Any]]:
        """Return active candidate/gold labels with only review metadata, never document text."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    labels.*,
                    documents.file_name,
                    documents.sha256,
                    analysis_runs.coverage_status,
                    analysis_runs.coverage_percentage
                FROM expert_review_labels AS labels
                JOIN analysis_runs ON analysis_runs.id = labels.run_id
                JOIN documents ON documents.id = analysis_runs.document_id
                WHERE labels.is_active = 1
                ORDER BY
                    CASE labels.dataset_status
                        WHEN 'expert_candidate' THEN 0
                        WHEN 'pilot_unlabelled' THEN 1
                        ELSE 2
                    END,
                    labels.id
                """
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def expert_dataset_summary(self) -> dict[str, Any]:
        items = self.list_expert_dataset_items()
        counts = {
            status: sum(item.get("dataset_status") == status for item in items)
            for status in ("pilot_unlabelled", "expert_candidate", "expert_gold")
        }

        def canonical_partition(partition: str) -> list[dict[str, Any]]:
            canonical = [
                {
                    "run_id": int(item["run_id"]),
                    "document_sha256": item["sha256"],
                    "outcome": item["outcome"],
                    "expected_mappings": item.get("expected_mappings") or [],
                    "expected_source_locations": item.get("expected_source_locations") or [],
                    "label_id": int(item["id"]),
                    "reviewer_id": item["reviewer_id"],
                    "dataset_partition": item.get("dataset_partition") or "evaluation",
                    "expected_template_status": (
                        item.get("expected_template_status") or "not_assessed"
                    ),
                }
                for item in items
                if item.get("dataset_status") == "expert_gold"
                and (item.get("dataset_partition") or "evaluation") == partition
            ]
            canonical.sort(key=lambda item: (item["document_sha256"], item["label_id"]))
            return canonical

        evaluation_gold = canonical_partition("evaluation")
        learning_gold = canonical_partition("learning")
        evaluation_sha256 = {item["document_sha256"] for item in evaluation_gold}
        learning_sha256 = {item["document_sha256"] for item in learning_gold}
        partition_overlap_sha256 = sorted(evaluation_sha256 & learning_sha256)
        evaluation_encoded = _json(evaluation_gold).encode("utf-8")
        learning_encoded = _json(learning_gold).encode("utf-8")
        return {
            "counts": counts,
            "active_label_count": len(items),
            "expert_gold_case_count": len(evaluation_gold),
            "evaluation_gold_case_count": len(evaluation_gold),
            "learning_gold_case_count": len(learning_gold),
            "total_expert_gold_case_count": counts["expert_gold"],
            "partition_overlap_count": len(partition_overlap_sha256),
            "partition_overlap_sha256": partition_overlap_sha256,
            "shadow_target": 50,
            "general_release_target": 200,
            "dataset_sha256": (
                hashlib.sha256(evaluation_encoded).hexdigest() if evaluation_gold else None
            ),
            "learning_dataset_sha256": (
                hashlib.sha256(learning_encoded).hexdigest() if learning_gold else None
            ),
        }

    def deactivate_retrieval_feedback_snapshots(self) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                "UPDATE retrieval_feedback_snapshots SET is_active = 0 WHERE is_active = 1"
            )
            return int(cursor.rowcount or 0)

    def save_retrieval_feedback_snapshot(
        self,
        compiled: dict[str, Any],
        *,
        expert_gold_case_count: int,
    ) -> dict[str, Any]:
        from app.analysis.learning import retrieval_parameter_catalog_sha256

        dataset_sha256 = str(compiled.get("dataset_sha256") or "")
        pipeline_version = str(compiled.get("pipeline_version") or "")
        learning_version = str(compiled.get("learning_version") or "")
        parameter_catalog_sha256 = str(compiled.get("parameter_catalog_sha256") or "")
        registry_sha256 = str(compiled.get("registry_sha256") or "")
        terms = list(compiled.get("terms") or [])
        if not all(
            re.fullmatch(r"[a-f0-9]{64}", value)
            for value in (dataset_sha256, parameter_catalog_sha256, registry_sha256)
        ):
            raise ValueError("Checksum retrieval feedback tidak valid.")
        if not pipeline_version or not learning_version:
            raise ValueError("Versi retrieval feedback wajib diisi.")
        if int(compiled.get("term_count") or 0) != len(terms):
            raise ValueError("Jumlah term retrieval feedback tidak konsisten.")
        current_parameters = self.parameter_index()
        if parameter_catalog_sha256 != retrieval_parameter_catalog_sha256(current_parameters):
            raise ValueError("Checksum katalog parameter retrieval feedback sudah stale.")
        parameters_by_key = {
            (str(item["kk_id"]), str(item["kode"]), str(item["detail_kode"])): item
            for item in current_parameters
        }

        with self.db.connect() as conn:
            existing = conn.execute(
                """
                SELECT * FROM retrieval_feedback_snapshots
                WHERE dataset_sha256 = ? AND pipeline_version = ? AND learning_version = ?
                  AND parameter_catalog_sha256 = ?
                """,
                (
                    dataset_sha256,
                    pipeline_version,
                    learning_version,
                    parameter_catalog_sha256,
                ),
            ).fetchone()
            if existing:
                if (
                    str(existing["registry_sha256"]) != registry_sha256
                    or int(existing["term_count"]) != len(terms)
                ):
                    raise RuntimeError(
                        "Snapshot retrieval feedback immutable berbeda untuk dataset/version yang sama."
                    )
                conn.execute(
                    "UPDATE retrieval_feedback_snapshots SET is_active = 0 WHERE is_active = 1"
                )
                conn.execute(
                    "UPDATE retrieval_feedback_snapshots SET is_active = 1 WHERE id = ?",
                    (int(existing["id"]),),
                )
                snapshot_id = int(existing["id"])
            else:
                conn.execute(
                    "UPDATE retrieval_feedback_snapshots SET is_active = 0 WHERE is_active = 1"
                )
                cursor = conn.execute(
                    """
                    INSERT INTO retrieval_feedback_snapshots (
                        dataset_sha256, pipeline_version, learning_version,
                        parameter_catalog_sha256, registry_sha256,
                        expert_gold_case_count, source_label_count,
                        term_count, minimum_document_support, minimum_precision,
                        is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        dataset_sha256,
                        pipeline_version,
                        learning_version,
                        parameter_catalog_sha256,
                        registry_sha256,
                        max(0, int(expert_gold_case_count)),
                        max(0, int(compiled.get("source_label_count") or 0)),
                        len(terms),
                        int(compiled.get("minimum_document_support") or 0),
                        float(compiled.get("minimum_precision") or 0),
                    ),
                )
                snapshot_id = int(cursor.lastrowid)
                for item in terms:
                    term = str(item.get("normalized_term") or "")
                    if not re.fullmatch(r"[a-z]{4,32}", term):
                        raise ValueError("Term retrieval feedback tidak lolos normalisasi aman.")
                    expected_term_sha256 = hashlib.sha256(term.encode("utf-8")).hexdigest()
                    if str(item.get("term_sha256") or "") != expected_term_sha256:
                        raise ValueError("Checksum term retrieval feedback tidak cocok.")
                    parameter_key = (
                        str(item.get("kk_id") or ""),
                        str(item.get("kode") or ""),
                        str(item.get("detail_kode") or ""),
                    )
                    current_parameter = parameters_by_key.get(parameter_key)
                    if not current_parameter:
                        raise ValueError("Parameter retrieval feedback tidak ada pada katalog aktif.")
                    conn.execute(
                        """
                        INSERT INTO retrieval_feedback_terms (
                            snapshot_id, parameter_id, kk_id, kode, detail_kode,
                            term_sha256, document_support,
                            observed_document_count, precision
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            int(current_parameter["id"]),
                            str(item["kk_id"]),
                            str(item["kode"]),
                            str(item["detail_kode"]),
                            str(item["term_sha256"]),
                            int(item["document_support"]),
                            int(item["observed_document_count"]),
                            float(item["precision"]),
                        ),
                    )
            row = conn.execute(
                "SELECT * FROM retrieval_feedback_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            raise RuntimeError("Snapshot retrieval feedback gagal disimpan.")
        return {
            **dict(row),
            "is_active": bool(row["is_active"]),
            "dataset_matches": True,
            "learning_gold_case_count": int(row["expert_gold_case_count"] or 0),
            "contains_raw_claims": False,
            "contains_normalized_feedback_terms": False,
            "contains_term_fingerprints": bool(row["term_count"]),
        }

    def retrieval_feedback_summary(self) -> dict[str, Any]:
        from app.analysis.learning import retrieval_parameter_catalog_sha256

        dataset = self.expert_dataset_summary()
        expected_dataset_sha256 = dataset.get("learning_dataset_sha256")
        expected_parameter_catalog_sha256 = retrieval_parameter_catalog_sha256(
            self.parameter_index()
        )
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM retrieval_feedback_snapshots
                WHERE is_active = 1
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            return {
                "active": False,
                "dataset_matches": expected_dataset_sha256 is None,
                "parameter_catalog_matches": False,
                "dataset_sha256": expected_dataset_sha256,
                "expected_parameter_catalog_sha256": expected_parameter_catalog_sha256,
                "term_count": 0,
                "contains_raw_claims": False,
                "contains_normalized_feedback_terms": False,
                "contains_term_fingerprints": False,
            }
        item = dict(row)
        dataset_matches = bool(
            expected_dataset_sha256
            and str(item["dataset_sha256"]) == str(expected_dataset_sha256)
        )
        parameter_catalog_matches = bool(
            str(item["parameter_catalog_sha256"]) == expected_parameter_catalog_sha256
        )
        return {
            **item,
            "active": bool(
                item["is_active"] and dataset_matches and parameter_catalog_matches
            ),
            "is_active": bool(item["is_active"]),
            "learning_gold_case_count": int(item["expert_gold_case_count"] or 0),
            "dataset_matches": dataset_matches,
            "expected_dataset_sha256": expected_dataset_sha256,
            "parameter_catalog_matches": parameter_catalog_matches,
            "expected_parameter_catalog_sha256": expected_parameter_catalog_sha256,
            "contains_raw_claims": False,
            "contains_normalized_feedback_terms": False,
            "contains_term_fingerprints": bool(item["term_count"]),
        }

    def active_retrieval_feedback_terms(self) -> list[dict[str, Any]]:
        from app.analysis.learning import retrieval_parameter_catalog_sha256

        dataset_sha256 = self.expert_dataset_summary().get("learning_dataset_sha256")
        if not dataset_sha256:
            return []
        parameter_catalog_sha256 = retrieval_parameter_catalog_sha256(
            self.parameter_index()
        )
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT terms.*, snapshots.registry_sha256,
                       snapshots.dataset_sha256, snapshots.learning_version
                FROM retrieval_feedback_terms AS terms
                JOIN retrieval_feedback_snapshots AS snapshots
                  ON snapshots.id = terms.snapshot_id
                WHERE snapshots.is_active = 1
                  AND snapshots.dataset_sha256 = ?
                  AND snapshots.pipeline_version = ?
                  AND snapshots.parameter_catalog_sha256 = ?
                ORDER BY terms.kk_id, terms.kode, terms.detail_kode, terms.term_sha256
                """,
                (dataset_sha256, PIPELINE_VERSION, parameter_catalog_sha256),
            ).fetchall()
        return [dict(row) for row in rows]

    def document_payload(self, run_id: int) -> bytes | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT documents.pending_bytes, documents.storage_status,
                       documents.sha256, documents.size_bytes,
                       documents.payload_storage_backend,
                       documents.payload_storage_key,
                       documents.payload_storage_sha256,
                       documents.payload_storage_size_bytes
                FROM analysis_runs
                JOIN documents ON documents.id = analysis_runs.document_id
                WHERE analysis_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
        if not row or row["storage_status"] == "purged":
            return None
        return self._payload_from_row(
            row,
            blob_field="pending_bytes",
            fallback_sha256=str(row["sha256"]),
            fallback_size_bytes=int(row["size_bytes"]),
        )

    def save_controlled_upload_action(self, action: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO controlled_upload_actions (
                    run_id, mapping_candidate_id, legacy_review_id, reviewer_id,
                    status, destination_json, message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action["run_id"], action["mapping_candidate_id"],
                    action.get("legacy_review_id"), action["reviewer_id"],
                    action["status"], _json(action.get("destination") or {}), action["message"],
                ),
            )
            return int(cursor.lastrowid)

    def get_controlled_upload_action(self, action_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM controlled_upload_actions WHERE id = ?",
                (int(action_id),),
            ).fetchone()
        return _decode_json_fields(dict(row)) if row else None

    @staticmethod
    def controlled_upload_idempotency_key(run_id: int, mapping_candidate_id: int) -> str:
        material = f"controlled-upload-primary:{int(run_id)}:{int(mapping_candidate_id)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def reserve_controlled_upload_action(
        self,
        *,
        run_id: int,
        mapping_candidate_id: int,
        reviewer_id: str,
        destination: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        idempotency_key = self.controlled_upload_idempotency_key(
            run_id,
            mapping_candidate_id,
        )
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM controlled_upload_actions
                WHERE idempotency_key = ?
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
            if existing:
                return _decode_json_fields(dict(existing)), False
            cursor = conn.execute(
                """
                INSERT INTO controlled_upload_actions (
                    run_id, mapping_candidate_id, legacy_review_id, reviewer_id,
                    status, destination_json, message, idempotency_key
                ) VALUES (?, ?, NULL, ?, 'uploading', ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(mapping_candidate_id),
                    reviewer_id,
                    _json(destination),
                    "Reservation controlled upload dibuat sebelum side effect eksternal.",
                    idempotency_key,
                ),
            )
            row = conn.execute(
                "SELECT * FROM controlled_upload_actions WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        if not row:
            raise RuntimeError("Reservation controlled upload gagal disimpan.")
        return _decode_json_fields(dict(row)), True

    def finalize_controlled_upload_action(
        self,
        action_id: int,
        *,
        status: str,
        legacy_review_id: int | None,
        destination: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        normalized = str(status or "").strip()
        if normalized not in {"uploaded_primary", "blocked_ambiguous"}:
            raise ValueError("Status final controlled upload tidak didukung.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE controlled_upload_actions
                SET legacy_review_id = ?, status = ?, destination_json = ?, message = ?
                WHERE id = ? AND status = 'uploading'
                """,
                (
                    legacy_review_id,
                    normalized,
                    _json(destination),
                    message[:1000],
                    int(action_id),
                ),
            )
            row = conn.execute(
                "SELECT * FROM controlled_upload_actions WHERE id = ?",
                (int(action_id),),
            ).fetchone()
        if not row:
            raise KeyError(f"Controlled upload action #{action_id} tidak ditemukan.")
        result = _decode_json_fields(dict(row))
        result["_write_applied"] = cursor.rowcount == 1
        return result

    def list_controlled_upload_reconciliation_events(
        self,
        action_id: int,
    ) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM controlled_upload_reconciliation_events
                WHERE action_id = ? ORDER BY id
                """,
                (int(action_id),),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    @staticmethod
    def _controlled_upload_reconciliation_summary(
        action: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_by_reviewer: dict[str, dict[str, Any]] = {}
        for event in events:
            latest_by_reviewer[str(event["reviewer_id"]).casefold()] = event
        latest = sorted(latest_by_reviewer.values(), key=lambda item: int(item["id"]))
        outcome_counts: dict[str, int] = {}
        for event in latest:
            outcome = str(event["outcome"])
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        terminal_counts = {
            outcome: total
            for outcome, total in outcome_counts.items()
            if outcome in {"confirmed_uploaded", "confirmed_not_uploaded"}
        }
        investigation_open = bool(outcome_counts.get("needs_investigation"))
        conflict = len(terminal_counts) > 1
        matching_reviewer_count = max(terminal_counts.values(), default=0)
        effective = bool(
            len(latest) >= 2
            and not investigation_open
            and not conflict
            and len(terminal_counts) == 1
            and matching_reviewer_count >= 2
        )
        if effective:
            reconciliation_status = "resolved"
        elif investigation_open:
            reconciliation_status = "needs_investigation"
        elif conflict:
            reconciliation_status = "conflict"
        else:
            reconciliation_status = "pending"
        return {
            "action_id": int(action["id"]),
            "run_id": int(action["run_id"]),
            "action_status": action["status"],
            "status": reconciliation_status,
            "effective": effective,
            "outcome": next(iter(terminal_counts)) if effective else None,
            "required_reviewers": 2,
            "reviewer_count": len(latest),
            "matching_reviewer_count": matching_reviewer_count,
            "conflict": conflict,
            "investigation_open": investigation_open,
            "latest_event_id": int(events[-1]["id"]) if events else None,
            "events": events,
        }

    def controlled_upload_reconciliation_summary(self, action_id: int) -> dict[str, Any]:
        action = self.get_controlled_upload_action(action_id)
        if not action:
            raise KeyError(f"Controlled upload action #{action_id} tidak ditemukan.")
        return self._controlled_upload_reconciliation_summary(
            action,
            self.list_controlled_upload_reconciliation_events(action_id),
        )

    def list_controlled_upload_reconciliation_summaries(
        self,
        run_id: int,
    ) -> list[dict[str, Any]]:
        actions = self.list_controlled_upload_actions(run_id)
        if not actions:
            return []
        action_by_id = {int(action["id"]): action for action in actions}
        placeholders = ",".join("?" for _ in action_by_id)
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM controlled_upload_reconciliation_events
                WHERE action_id IN ({placeholders})
                ORDER BY id
                """,
                tuple(action_by_id),
            ).fetchall()
        events_by_action: dict[int, list[dict[str, Any]]] = {
            action_id: [] for action_id in action_by_id
        }
        for row in rows:
            event = _decode_json_fields(dict(row))
            events_by_action[int(event["action_id"])].append(event)
        return [
            self._controlled_upload_reconciliation_summary(
                action_by_id[action_id],
                events_by_action[action_id],
            )
            for action_id in sorted(action_by_id)
        ]

    def save_controlled_upload_reconciliation_event(
        self,
        *,
        action_id: int,
        reviewer_id: str,
        outcome: str,
        reason: str,
        expected_latest_event_id: int | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        normalized_reviewer = str(reviewer_id).strip().casefold()
        normalized_outcome = str(outcome).strip()
        if normalized_outcome not in {
            "confirmed_uploaded",
            "confirmed_not_uploaded",
            "needs_investigation",
        }:
            raise ValueError("Outcome rekonsiliasi controlled upload tidak didukung.")
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            action_row = conn.execute(
                "SELECT * FROM controlled_upload_actions WHERE id = ?",
                (int(action_id),),
            ).fetchone()
            if not action_row:
                raise KeyError(f"Controlled upload action #{action_id} tidak ditemukan.")
            action = _decode_json_fields(dict(action_row))
            if action.get("status") != "blocked_ambiguous":
                raise ValueError(
                    "Hanya controlled upload berstatus blocked_ambiguous yang dapat direkonsiliasi."
                )
            rows = conn.execute(
                """
                SELECT * FROM controlled_upload_reconciliation_events
                WHERE action_id = ? ORDER BY id
                """,
                (int(action_id),),
            ).fetchall()
            events = [_decode_json_fields(dict(row)) for row in rows]
            before = self._controlled_upload_reconciliation_summary(action, events)
            if before["effective"]:
                raise ValueError(
                    "Rekonsiliasi dua reviewer sudah final dan tidak dapat diubah."
                )
            current_latest = int(events[-1]["id"]) if events else None
            if expected_latest_event_id != current_latest:
                raise ValueError(
                    "Riwayat rekonsiliasi berubah; muat ulang sebelum menyimpan keputusan."
                )
            previous = next(
                (
                    event for event in reversed(events)
                    if str(event["reviewer_id"]).casefold() == normalized_reviewer
                ),
                None,
            )
            cursor = conn.execute(
                """
                INSERT INTO controlled_upload_reconciliation_events (
                    action_id, reviewer_id, outcome, reason, attested,
                    supersedes_event_id
                ) VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    int(action_id),
                    normalized_reviewer,
                    normalized_outcome,
                    str(reason).strip(),
                    int(previous["id"]) if previous else None,
                ),
            )
            event_row = conn.execute(
                """
                SELECT * FROM controlled_upload_reconciliation_events WHERE id = ?
                """,
                (int(cursor.lastrowid),),
            ).fetchone()
        if not event_row:
            raise RuntimeError("Event rekonsiliasi controlled upload gagal disimpan.")
        event = _decode_json_fields(dict(event_row))
        after = self._controlled_upload_reconciliation_summary(
            action,
            [*events, event],
        )
        return event, before, after

    def list_controlled_upload_actions(self, run_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM controlled_upload_actions WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [_decode_json_fields(dict(row)) for row in rows]

    def create_package(
        self,
        *,
        name: str,
        run_ids: list[int],
        organization: str | None = None,
        period: str | None = None,
    ) -> int:
        unique_run_ids = list(dict.fromkeys(int(item) for item in run_ids))
        if len(unique_run_ids) < 2:
            raise ValueError("Paket evidence membutuhkan minimal dua analysis run.")
        with self.db.connect() as conn:
            placeholders = ",".join("?" for _ in unique_run_ids)
            rows = conn.execute(
                f"SELECT id FROM analysis_runs WHERE id IN ({placeholders})",
                tuple(unique_run_ids),
            ).fetchall()
            if len(rows) != len(unique_run_ids):
                raise ValueError("Sebagian analysis run paket tidak ditemukan.")
            cursor = conn.execute(
                """
                INSERT INTO analysis_packages (name, status, organization, period)
                VALUES (?, 'queued', ?, ?)
                """,
                (name.strip() or "Paket Evidence", organization, period),
            )
            package_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO analysis_package_members (package_id, run_id) VALUES (?, ?)",
                [(package_id, run_id) for run_id in unique_run_ids],
            )
        return package_id

    def update_package(self, package_id: int, **values: Any) -> None:
        allowed = {"status", "primary_blocked", "block_reasons_json", "finished_at"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Field package tidak diizinkan: {sorted(unknown)}")
        if not values:
            return
        normalized = dict(values)
        if "primary_blocked" in normalized:
            normalized["primary_blocked"] = int(bool(normalized["primary_blocked"]))
        if "block_reasons_json" in normalized and not isinstance(normalized["block_reasons_json"], str):
            normalized["block_reasons_json"] = _json(normalized["block_reasons_json"])
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE analysis_packages SET {assignments} WHERE id = ?",
                (*normalized.values(), package_id),
            )

    def save_package_assessments(self, package_id: int, assessments: list[dict[str, Any]]) -> list[int]:
        ids = []
        with self.db.connect() as conn:
            for item in assessments:
                cursor = conn.execute(
                    """
                    INSERT INTO package_assessments (
                        package_id, kk_id, kode, detail_kode, organization, period,
                        chain_json, supporting_run_ids_json, supporting_fact_ids_json,
                        safe_grade, contradictions_json, missing_requirements_json, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package_id,
                        item["kk_id"],
                        item["kode"],
                        item["detail_kode"],
                        item.get("organization") or "unknown",
                        item.get("period") or "unknown",
                        _json(item.get("chain") or {}),
                        _json(item.get("supporting_run_ids") or []),
                        _json(item.get("supporting_fact_ids") or []),
                        item.get("safe_grade"),
                        _json(item.get("contradictions") or []),
                        _json(item.get("missing_requirements") or []),
                        item.get("status") or "needs_human_review",
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    def save_package_engine_result(
        self,
        package_id: int,
        *,
        engine_name: str,
        engine_version: str,
        status: str,
        output: dict[str, Any],
        warnings: list[str],
        metrics: dict[str, Any],
    ) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO package_engine_results (
                    package_id, engine_name, engine_version, status,
                    output_json, warnings_json, metrics_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (package_id, engine_name, engine_version, status, _json(output), _json(warnings), _json(metrics)),
            )
            return int(cursor.lastrowid)

    def save_package_review_decision(self, package_id: int, decision: dict[str, Any]) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO package_review_decisions (
                    package_id, reviewer_id, decision, reason,
                    assessment_snapshot_json, pipeline_version
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    package_id,
                    decision["reviewer_id"],
                    decision["decision"],
                    decision["reason"],
                    _json(decision.get("assessment_snapshot") or []),
                    decision["pipeline_version"],
                ),
            )
            return int(cursor.lastrowid)

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM analysis_packages WHERE id = ?", (package_id,)).fetchone()
            if not row:
                return None
            members = conn.execute(
                """
                SELECT analysis_runs.*, documents.file_name, documents.sha256
                FROM analysis_package_members
                JOIN analysis_runs ON analysis_runs.id = analysis_package_members.run_id
                JOIN documents ON documents.id = analysis_runs.document_id
                WHERE analysis_package_members.package_id = ?
                ORDER BY analysis_runs.id
                """,
                (package_id,),
            ).fetchall()
            assessments = conn.execute(
                "SELECT * FROM package_assessments WHERE package_id = ? ORDER BY id",
                (package_id,),
            ).fetchall()
            engines = conn.execute(
                "SELECT * FROM package_engine_results WHERE package_id = ? ORDER BY id",
                (package_id,),
            ).fetchall()
            decisions = conn.execute(
                "SELECT * FROM package_review_decisions WHERE package_id = ? ORDER BY id",
                (package_id,),
            ).fetchall()
        package = _decode_json_fields(dict(row))
        package["members"] = [_decode_json_fields(dict(item)) for item in members]
        package["assessments"] = [_decode_json_fields(dict(item)) for item in assessments]
        package["engines"] = [_decode_json_fields(dict(item)) for item in engines]
        package["review_decisions"] = [_decode_json_fields(dict(item)) for item in decisions]
        return package
