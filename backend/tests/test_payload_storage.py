from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import time

from app.analysis.jobs import AnalysisJobManager
from app.analysis.payload_storage import (
    FilesystemPayloadStore,
    PayloadIntegrityError,
    PayloadStorageError,
)
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database
from app.migrations import backfill_payload_storage_metadata
from scripts.analysis_db_backup import backup, restore_verified, verify_payload_storage


class FilesystemPayloadStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(str(self.root / "analysis.db"))
        self.db.ensure_mapping()
        self.store = FilesystemPayloadStore(self.root / "payloads", fsync=False)
        self.repository = AnalysisRepository(self.db, payload_store=self.store)
        self.payload = b"restricted SPIP evidence payload"
        self.sha256 = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_content_addressed_store_is_private_atomic_and_checksum_bound(self) -> None:
        first = self.store.put(self.payload, expected_sha256=self.sha256)
        second = self.store.put(self.payload, expected_sha256=self.sha256)
        self.assertEqual(first, second)
        path = self.store.root / first.key
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertEqual(
            self.store.get(
                first.key,
                expected_sha256=self.sha256,
                expected_size_bytes=len(self.payload),
            ),
            self.payload,
        )
        self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_store_rejects_unsafe_key_symlink_permissions_and_tampering(self) -> None:
        stored = self.store.put(self.payload, expected_sha256=self.sha256)
        with self.assertRaises(PayloadStorageError):
            self.store.get(
                "../outside.blob",
                expected_sha256=self.sha256,
                expected_size_bytes=len(self.payload),
            )
        path = self.store.root / stored.key
        path.chmod(0o644)
        with self.assertRaises(PayloadIntegrityError):
            self.store.get(
                stored.key,
                expected_sha256=self.sha256,
                expected_size_bytes=len(self.payload),
            )
        path.chmod(0o600)
        path.write_bytes(b"x" * len(self.payload))
        with self.assertRaises(PayloadIntegrityError):
            self.store.get(
                stored.key,
                expected_sha256=self.sha256,
                expected_size_bytes=len(self.payload),
            )

    def test_job_and_document_use_external_payload_without_database_blob(self) -> None:
        job = self.repository.enqueue_job(
            file_name="evidence.txt",
            content_type="text/plain",
            payload=self.payload,
            analysis_mode="full_audit",
        )
        with self.db.connect() as conn:
            job_row = conn.execute(
                "SELECT * FROM analysis_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
        self.assertIsNone(job_row["payload"])
        self.assertEqual(job_row["payload_storage_backend"], "filesystem")
        self.assertEqual(self.repository.job_payload(job["id"]), self.payload)

        document = self.repository.upsert_document(
            file_name="evidence.txt",
            content_type="text/plain",
            size_bytes=len(self.payload),
            sha256=self.sha256,
            payload=self.payload,
            ttl_hours=1,
        )
        run_id = self.repository.create_run(
            document_id=int(document["id"]),
            analysis_mode="full_audit",
            pipeline_version="2.0.0",
            parser_version="2.0.0",
            rule_version="2026.2-draft",
            prompt_version="2026.2",
            provider="local_only",
            model=None,
            configuration_hash="f" * 64,
            external_ai_allowed=False,
        )
        with self.db.connect() as conn:
            document_row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (document["id"],)
            ).fetchone()
        self.assertIsNone(document_row["pending_bytes"])
        self.assertEqual(document_row["payload_storage_key"], job_row["payload_storage_key"])
        self.assertEqual(self.repository.document_payload(run_id), self.payload)

        self.repository.complete_job(job["id"], run_id)
        self.assertTrue((self.store.root / document_row["payload_storage_key"]).is_file())
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE documents SET purge_after = '2000-01-01 00:00:00' WHERE id = ?",
                (document["id"],),
            )
        self.assertEqual(self.repository.purge_expired_payloads(), 1)
        self.assertIsNone(self.repository.document_payload(run_id))
        self.assertFalse((self.store.root / document_row["payload_storage_key"]).exists())

    def test_orphan_cleanup_preserves_references_and_removes_only_orphans(self) -> None:
        referenced = self.repository.enqueue_job(
            file_name="evidence.txt",
            content_type="text/plain",
            payload=self.payload,
            analysis_mode="full_audit",
        )
        orphan_payload = b"unreferenced"
        orphan = self.store.put(orphan_payload, hashlib.sha256(orphan_payload).hexdigest())
        report = self.repository.cleanup_orphaned_payloads()
        self.assertEqual(report["orphan_count"], 1)
        self.assertEqual(report["deleted_count"], 1)
        self.assertFalse((self.store.root / orphan.key).exists())
        self.assertEqual(self.repository.job_payload(referenced["id"]), self.payload)

    def test_worker_fails_closed_when_external_payload_is_corrupt(self) -> None:
        settings = Settings(
            database_path=str(self.root / "analysis.db"),
            analysis_payload_storage_backend="filesystem",
            analysis_payload_storage_path=str(self.root / "worker-payloads"),
            analysis_payload_storage_fsync=False,
            analysis_pipeline_v2_enabled=True,
            analysis_worker_limit=1,
        )
        manager = AnalysisJobManager(self.db, settings)
        job = manager.repository.enqueue_job(
            file_name="evidence.txt",
            content_type="text/plain",
            payload=self.payload,
            analysis_mode="full_audit",
        )
        claimed = manager.repository.claim_next_job()
        self.assertEqual(claimed["id"], job["id"])
        with self.db.connect() as conn:
            key = conn.execute(
                "SELECT payload_storage_key FROM analysis_jobs WHERE id = ?",
                (job["id"],),
            ).fetchone()["payload_storage_key"]
        path = manager.repository.payload_store.root / key
        path.write_bytes(b"x" * len(self.payload))
        os.chmod(path, 0o600)
        manager._process_job(claimed)
        failed = manager.repository.get_job(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("integrity", failed["error_message"].lower())

    def test_database_and_payload_backup_restore_are_verified_together(self) -> None:
        document = self.repository.upsert_document(
            file_name="backup-evidence.txt",
            content_type="text/plain",
            size_bytes=len(self.payload),
            sha256=self.sha256,
            payload=self.payload,
            ttl_hours=72,
        )
        run_id = self.repository.create_run(
            document_id=int(document["id"]),
            analysis_mode="full_audit",
            pipeline_version="2.0.0",
            parser_version="2.0.0",
            rule_version="2026.2-draft",
            prompt_version="2026.2",
            provider="local_only",
            model=None,
            configuration_hash="e" * 64,
            external_ai_allowed=False,
        )
        database_backup = self.root / "backup.db"
        payload_backup = self.root / "payload-backup"
        with self.assertRaises(RuntimeError):
            backup(Path(self.db.path), self.root / "incomplete.db")
        report = backup(
            Path(self.db.path),
            database_backup,
            payload_source=self.store.root,
            payload_destination=payload_backup,
        )
        self.assertEqual(report["payload_storage"]["reference_count"], 1)
        self.assertEqual(report["payload_storage"]["orphan_count"], 0)

        restored_database = self.root / "restored.db"
        restored_payloads = self.root / "restored-payloads"
        restored = restore_verified(
            database_backup,
            restored_database,
            payload_backup=payload_backup,
            payload_target=restored_payloads,
        )
        self.assertEqual(
            restored["payload_storage"]["manifest_sha256"],
            report["payload_storage"]["manifest_sha256"],
        )
        restored_repository = AnalysisRepository(
            Database(str(restored_database)),
            payload_store=FilesystemPayloadStore(restored_payloads, fsync=False),
        )
        self.assertEqual(restored_repository.document_payload(run_id), self.payload)

        payload_file = next(payload_backup.rglob("*.blob"))
        payload_file.write_bytes(b"x" * len(self.payload))
        os.chmod(payload_file, 0o600)
        with self.assertRaises(PayloadIntegrityError):
            verify_payload_storage(database_backup, payload_backup)

    def test_job_manager_pipeline_uses_filesystem_profile_end_to_end(self) -> None:
        settings = Settings(
            _env_file=None,
            database_path=str(self.root / "analysis.db"),
            analysis_pipeline_v2_enabled=True,
            analysis_payload_storage_backend="filesystem",
            analysis_payload_storage_path=str(self.root / "pipeline-payloads"),
            analysis_payload_storage_fsync=False,
            analysis_worker_limit=1,
            analysis_structured_model_enabled=False,
            analysis_model_verifier_enabled=False,
        )
        manager = AnalysisJobManager(self.db, settings)
        try:
            job = manager.enqueue(
                file_name="pipeline.txt",
                content_type="text/plain",
                payload=self.payload,
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            deadline = time.monotonic() + 5
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = manager.describe(job["id"])
                if snapshot and snapshot["job"]["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.02)
            self.assertEqual(snapshot["job"]["status"], "completed")
            run_id = int(snapshot["job"]["run_id"])
            with self.db.connect() as conn:
                job_row = conn.execute(
                    "SELECT payload, payload_storage_key FROM analysis_jobs WHERE id = ?",
                    (job["id"],),
                ).fetchone()
                document_row = conn.execute(
                    """
                    SELECT documents.* FROM documents
                    JOIN analysis_runs ON analysis_runs.document_id = documents.id
                    WHERE analysis_runs.id = ?
                    """,
                    (run_id,),
                ).fetchone()
            self.assertIsNone(job_row["payload"])
            self.assertIsNone(job_row["payload_storage_key"])
            self.assertIsNone(document_row["pending_bytes"])
            self.assertEqual(document_row["payload_storage_backend"], "filesystem")
            self.assertEqual(manager.repository.document_payload(run_id), self.payload)
        finally:
            manager.stop()

    def test_v20_backfills_and_validates_existing_database_blobs(self) -> None:
        legacy_payload = b"pre-v20-payload"
        legacy_sha256 = hashlib.sha256(legacy_payload).hexdigest()
        with self.db.connect() as conn:
            document_cursor = conn.execute(
                """
                INSERT INTO documents (
                    file_name, size_bytes, sha256, storage_status, pending_bytes,
                    payload_storage_backend
                ) VALUES ('legacy.bin', ?, ?, 'pending', ?, 'database')
                """,
                (len(legacy_payload), legacy_sha256, legacy_payload),
            )
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, status, file_name, size_bytes, payload, analysis_mode,
                    payload_storage_backend
                ) VALUES ('legacy-job', 'queued', 'legacy.bin', ?, ?, 'full_audit', 'database')
                """,
                (len(legacy_payload), legacy_payload),
            )
            self.assertEqual(backfill_payload_storage_metadata(conn), 2)
            document = conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (int(document_cursor.lastrowid),),
            ).fetchone()
            job = conn.execute(
                "SELECT * FROM analysis_jobs WHERE id = 'legacy-job'"
            ).fetchone()
            self.assertEqual(document["payload_storage_sha256"], legacy_sha256)
            self.assertEqual(job["payload_storage_sha256"], legacy_sha256)
            conn.execute(
                """
                UPDATE documents
                SET pending_bytes = ?, payload_storage_sha256 = NULL,
                    payload_storage_size_bytes = NULL
                WHERE id = ?
                """,
                (b"tampered-payload", int(document_cursor.lastrowid)),
            )
            with self.assertRaises(RuntimeError):
                backfill_payload_storage_metadata(conn)


if __name__ == "__main__":
    unittest.main()
