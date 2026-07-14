from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.jobs import AnalysisJobManager
from app.analysis.governance import synthetic_probe_png
from app.analysis.local_ocr import LocalOCRItem, LocalOCRResponse
from app.analysis.orchestrator import AnalysisOrchestrator, configuration_hash
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database


class AnalysisJobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.db"
        self.db = Database(str(self.db_path))
        self.db.ensure_mapping()
        self.db.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=str(self.db_path),
            analysis_pipeline_v2_enabled=True,
            analysis_worker_limit=1,
        )
        self.manager = AnalysisJobManager(self.db, self.settings)
        self.extra_managers: list[AnalysisJobManager] = []

    def tearDown(self) -> None:
        for manager in reversed(self.extra_managers):
            manager.stop()
        self.manager.stop()
        self.temp_dir.cleanup()

    def test_job_runs_durably_and_exposes_backend_events(self) -> None:
        job = self.manager.enqueue(
            file_name="evidence.txt",
            content_type="text/plain",
            payload=b"Kebijakan indikator kinerja telah ditetapkan dan dievaluasi secara berkala tahun 2026.",
            analysis_mode="full_audit",
        )
        deadline = time.monotonic() + 5
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = self.manager.describe(job["id"])
            if snapshot and snapshot["job"]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["job"]["status"], "completed")
        self.assertIsNotNone(snapshot["job"]["run_id"])
        self.assertEqual(snapshot["result"]["events"][-1]["progress"], 100)
        self.assertEqual(snapshot["result"]["run"]["status"], "review_required")

    def test_runtime_lifecycle_logs_are_structured_run_bound_and_content_free(self) -> None:
        secret_text = "ISI DOKUMEN SANGAT RAHASIA 2026"
        with self.assertLogs("uvicorn.error.spip.analysis", level="INFO") as captured:
            job = self.manager.enqueue(
                file_name="structured-secret-name.txt",
                content_type="text/plain",
                payload=secret_text.encode(),
                analysis_mode="full_audit",
            )
            deadline = time.monotonic() + 5
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = self.manager.describe(job["id"], include_result=False)
                terminal = snapshot and snapshot["job"]["status"] in {
                    "completed", "failed", "cancelled"
                }
                completed_logged = any(
                    '"event":"job_completed"' in line for line in captured.output
                )
                if terminal and completed_logged:
                    break
                time.sleep(0.02)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["job"]["status"], "completed")
        serialized_logs = "\n".join(captured.output)
        self.assertNotIn("structured-secret-name", serialized_logs)
        self.assertNotIn(secret_text, serialized_logs)
        records = [json.loads(line.split(":", 2)[-1]) for line in captured.output]
        events = {record["event"] for record in records}
        self.assertTrue({"job_enqueued", "job_claimed", "run_attached", "job_completed"} <= events)
        completed = next(record for record in records if record["event"] == "job_completed")
        self.assertEqual(completed["run_id"], snapshot["job"]["run_id"])

    def test_local_only_job_can_use_local_ocr_without_external_ai(self) -> None:
        class LocalProvider:
            name = "test_local"

            def analyze_images(self, images):
                return LocalOCRResponse(items=[LocalOCRItem(
                    unit_key=images[0]["unit_key"],
                    text="Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                    confidence=0.95,
                    regions=[{
                        "text": "Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                        "confidence": 0.95,
                        "bbox": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.1},
                        "coordinate_space": "normalized_top_left",
                    }],
                    method="local_test_v1",
                )])

        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=LocalProvider(),
        ):
            job = self.manager.enqueue(
                file_name="restricted.png",
                content_type="image/png",
                payload=synthetic_probe_png(),
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            deadline = time.monotonic() + 5
            snapshot = None
            while time.monotonic() < deadline:
                snapshot = self.manager.describe(job["id"])
                if snapshot and snapshot["job"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.02)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["job"]["status"], "completed")
        self.assertFalse(snapshot["result"]["run"]["external_ai_allowed"])
        self.assertEqual(snapshot["result"]["run"]["coverage_status"], "partial")
        unit = snapshot["result"]["document_units"][0]
        self.assertEqual(unit["status"], "partial")
        self.assertEqual(unit["metadata"]["ocr_provider"], "local")
        self.assertEqual(unit["metadata"]["ocr_method"], "local_test_v1")
        self.assertEqual(
            unit["metadata"]["visual_semantics_status"],
            "pending_review_or_vision",
        )

    def test_queued_job_can_be_cancelled_without_running(self) -> None:
        repository = AnalysisRepository(self.db)
        job = repository.enqueue_job(
            file_name="queued.txt",
            content_type="text/plain",
            payload=b"queued",
            analysis_mode="screening",
        )
        cancelled = repository.cancel_job(job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(repository.job_payload(job["id"]))

    def test_cancel_closes_requeued_and_expired_requested_runs(self) -> None:
        repository = AnalysisRepository(self.db)

        def attached_run(name: str) -> tuple[dict, dict, int]:
            payload = f"payload {name}".encode()
            job = repository.enqueue_job(
                file_name=f"{name}.txt",
                content_type="text/plain",
                payload=payload,
                analysis_mode="full_audit",
            )
            claimed = repository.claim_next_job(lease_minutes=5)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["id"], job["id"])
            document = repository.upsert_document(
                file_name=f"{name}.txt",
                content_type="text/plain",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                payload=payload,
                ttl_hours=24,
            )
            run_id = repository.create_run(
                document_id=int(document["id"]),
                analysis_mode="full_audit",
                pipeline_version=PIPELINE_VERSION,
                parser_version=PARSER_VERSION,
                rule_version=RULE_VERSION,
                prompt_version=PROMPT_VERSION,
                provider=None,
                model=None,
                configuration_hash=f"cancel-{name}",
            )
            self.assertTrue(repository.attach_job_run(
                job["id"],
                run_id,
                expected_attempt=int(claimed["attempt_count"]),
            ))
            return job, claimed, run_id

        requeued_job, requeued_claim, requeued_run_id = attached_run("requeued")
        self.assertTrue(repository.requeue_job_after_worker_shutdown(
            requeued_job["id"],
            expected_attempt=int(requeued_claim["attempt_count"]),
        ))
        cancelled = repository.cancel_job(requeued_job["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(repository.get_run(requeued_run_id)["status"], "cancelled")
        self.assertIsNone(repository.job_payload(requeued_job["id"]))
        self.assertEqual(
            repository.find_job_for_run(requeued_run_id)["id"],
            requeued_job["id"],
        )
        requeued_events = repository.list_events(requeued_run_id)
        self.assertEqual(
            [event["event_type"] for event in requeued_events],
            ["run_cancelled"],
        )
        self.assertEqual(requeued_events[0]["payload"]["source"], "queued_job_cancel")

        expired_job, expired_claim, expired_run_id = attached_run("expired")
        requested = repository.cancel_job(expired_job["id"])
        self.assertEqual(requested["status"], "cancel_requested")
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET lease_until = '2000-01-01 00:00:00'
                WHERE id = ? AND attempt_count = ?
                """,
                (expired_job["id"], int(expired_claim["attempt_count"])),
            )
        self.assertEqual(repository.recover_expired_jobs(), 1)
        self.assertEqual(repository.get_job(expired_job["id"])["status"], "cancelled")
        self.assertEqual(repository.get_run(expired_run_id)["status"], "cancelled")
        self.assertIsNone(repository.job_payload(expired_job["id"]))
        expired_events = repository.list_events(expired_run_id)
        self.assertEqual(expired_events[-1]["event_type"], "run_cancelled")
        self.assertEqual(expired_events[-1]["payload"]["source"], "lease_recovery")

        raced_job = repository.enqueue_job(
            file_name="cancel-claim-race.txt",
            content_type="text/plain",
            payload=b"cancel and claim must serialize",
            analysis_mode="screening",
        )
        second_repository = AnalysisRepository(self.db)
        barrier = threading.Barrier(2)

        def cancel_raced_job():
            barrier.wait()
            return repository.cancel_job(raced_job["id"])

        def claim_raced_job():
            barrier.wait()
            return second_repository.claim_next_job(lease_minutes=5)

        with ThreadPoolExecutor(max_workers=2) as executor:
            cancel_future = executor.submit(cancel_raced_job)
            claim_future = executor.submit(claim_raced_job)
            raced_cancel = cancel_future.result()
            raced_claim = claim_future.result()
        final_raced = repository.get_job(raced_job["id"])
        self.assertIn(final_raced["status"], {"cancelled", "cancel_requested"})
        if raced_claim:
            self.assertEqual(raced_claim["id"], raced_job["id"])
            self.assertEqual(raced_cancel["status"], "cancel_requested")
            self.assertIsNotNone(repository.job_payload(raced_job["id"]))
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE analysis_jobs SET lease_until = '2000-01-01 00:00:00'
                    WHERE id = ?
                    """,
                    (raced_job["id"],),
                )
            self.assertEqual(repository.recover_expired_jobs(), 1)
        else:
            self.assertEqual(raced_cancel["status"], "cancelled")
        self.assertEqual(repository.get_job(raced_job["id"])["status"], "cancelled")
        self.assertIsNone(repository.job_payload(raced_job["id"]))

    def test_expired_running_job_is_recovered(self) -> None:
        repository = AnalysisRepository(self.db)
        job = repository.enqueue_job(
            file_name="recover.txt",
            content_type="text/plain",
            payload=b"recover",
            analysis_mode="screening",
        )
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', lease_until = '2000-01-01 00:00:00'
                WHERE id = ?
                """,
                (job["id"],),
            )
        recovered = repository.recover_expired_jobs()
        self.assertEqual(recovered, 1)
        self.assertEqual(repository.get_job(job["id"])["status"], "queued")

    def test_repeated_crash_recovers_from_durable_ocr_batch_without_repeating_success(self) -> None:
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.add_blank_page(width=300, height=300)
        writer.write(output)
        payload = output.getvalue()
        checksum = hashlib.sha256(payload).hexdigest()
        repository = AnalysisRepository(self.db, settings=self.settings)
        document = repository.upsert_document(
            file_name="recovery.pdf",
            content_type="application/pdf",
            size_bytes=len(payload),
            sha256=checksum,
            payload=payload,
            ttl_hours=1,
        )
        source_run_id = repository.create_run(
            document_id=document["id"],
            analysis_mode="full_audit",
            pipeline_version=PIPELINE_VERSION,
            parser_version=PARSER_VERSION,
            rule_version=RULE_VERSION,
            prompt_version=PROMPT_VERSION,
            provider=None,
            model=None,
            configuration_hash=configuration_hash(self.settings, "full_audit"),
            external_ai_allowed=True,
        )
        units = [
            {
                "unit_key": "page-1",
                "unit_type": "page",
                "ordinal": 1,
                "heading_path": [],
                "source_location": {"page": 1},
                "text": "Hasil OCR halaman pertama sudah durable.",
                "status": "processed",
                "warnings": [],
                "metadata": {
                    "ocr_provider": "local",
                    "ocr_confidence": 0.95,
                },
            },
            {
                "unit_key": "page-2",
                "unit_type": "page",
                "ordinal": 2,
                "heading_path": [],
                "source_location": {"page": 2},
                "text": "",
                "status": "ocr_required",
                "warnings": ["OCR diperlukan."],
                "metadata": {},
            },
        ]
        repository.save_document_units(source_run_id, units)
        source_orchestrator = AnalysisOrchestrator(self.db, self.settings)
        source_orchestrator._checkpoint_units(
            source_run_id, "visual_ocr_manifest", units
        )
        repository.save_unit_checkpoint(
            source_run_id,
            unit_key="page-1",
            stage="visual_ocr_batch",
            status="completed",
            input_checksum=source_orchestrator._unit_checkpoint_checksum(units[0]),
        )

        job = repository.enqueue_job(
            file_name="recovery.pdf",
            content_type="application/pdf",
            payload=payload,
            analysis_mode="full_audit",
            external_ai_allowed=True,
        )
        first_claim = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(first_claim["id"], job["id"])
        repository.attach_job_run(job["id"], source_run_id)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE analysis_jobs SET lease_until = '2000-01-01 00:00:00' WHERE id = ?",
                (job["id"],),
            )
        self.assertEqual(repository.recover_expired_jobs(), 1)
        recovery_claim = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(recovery_claim["attempt_count"], 2)

        with patch(
            "app.analysis.jobs.AnalysisOrchestrator.start",
            side_effect=SystemExit("simulated process death before retry run creation"),
        ):
            with self.assertRaises(SystemExit):
                self.manager._process_job(recovery_claim)

        interrupted = repository.get_job(job["id"])
        self.assertEqual(interrupted["status"], "running")
        self.assertIsNone(interrupted["run_id"])
        self.assertEqual(interrupted["resume_from_run_id"], source_run_id)
        self.assertIsNotNone(repository.job_payload(job["id"]))
        self.assertEqual(repository.get_run(source_run_id)["status"], "failed")
        self.assertTrue(any(
            event["event_type"] == "run_superseded_after_recovery"
            for event in repository.list_events(source_run_id)
        ))

        with self.db.connect() as conn:
            conn.execute(
                "UPDATE analysis_jobs SET lease_until = '2000-01-01 00:00:00' WHERE id = ?",
                (job["id"],),
            )
        self.assertEqual(repository.recover_expired_jobs(), 1)
        final_claim = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(final_claim["attempt_count"], 3)

        class RecordingProvider:
            name = "recording_local"

            def __init__(self):
                self.unit_keys = []

            def analyze_images(self, images):
                self.unit_keys.extend(str(image["unit_key"]) for image in images)
                return LocalOCRResponse(items=[
                    LocalOCRItem(
                        unit_key=str(image["unit_key"]),
                        text="Hasil OCR halaman kedua setelah recovery.",
                        confidence=0.96,
                        regions=[],
                        method="recovery_test_v1",
                    )
                    for image in images
                ])

        provider = RecordingProvider()
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=provider,
        ):
            self.manager._process_job(final_claim)

        completed = repository.get_job(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempt_count"], 3)
        self.assertEqual(completed["resume_from_run_id"], source_run_id)
        self.assertNotEqual(completed["run_id"], source_run_id)
        self.assertEqual(provider.unit_keys, ["page-2"])
        resumed = AnalysisOrchestrator(self.db, self.settings).describe(
            int(completed["run_id"])
        )
        self.assertEqual(resumed["run"]["resumed_from_run_id"], source_run_id)
        self.assertTrue(any(
            event["event_type"] == "visual_ocr_partial_resume"
            for event in resumed["events"]
        ))
        resumed_by_key = {
            unit["unit_key"]: repository.get_document_unit(
                int(completed["run_id"]), unit["unit_key"]
            )
            for unit in resumed["document_units"]
        }
        self.assertEqual(
            resumed_by_key["page-1"]["text"],
            "Hasil OCR halaman pertama sudah durable.",
        )
        self.assertEqual(resumed_by_key["page-2"]["status"], "processed")
        metrics = repository.operational_metrics()["job_recovery"]
        self.assertEqual(metrics["recovered_job_count"], 1)
        self.assertEqual(metrics["lease_retry_attempt_count"], 2)
        self.assertEqual(metrics["resume_lineage_job_count"], 1)
        self.assertEqual(metrics["active_recovery_loop_count"], 0)

    def test_graceful_shutdown_keeps_leader_until_ocr_batch_requeues_and_resumes(self) -> None:
        output = BytesIO()
        writer = PdfWriter()
        for _ in range(4):
            writer.add_blank_page(width=300, height=300)
        writer.write(output)
        payload = output.getvalue()
        started = threading.Event()
        release = threading.Event()

        class BlockingFirstBatchProvider:
            name = "blocking_first_batch"

            def __init__(self):
                self.unit_keys = []

            def analyze_images(self, images):
                self.unit_keys.extend(str(image["unit_key"]) for image in images)
                started.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test provider release timeout")
                return LocalOCRResponse(items=[
                    LocalOCRItem(
                        unit_key=str(image["unit_key"]),
                        text=f"OCR durable {image['unit_key']}.",
                        confidence=0.96,
                        method="shutdown_batch_v1",
                    )
                    for image in images
                ])

        first_provider = BlockingFirstBatchProvider()
        shutdown_settings = self.settings.model_copy(update={
            "analysis_local_ocr_render_batch_units": 2,
            "analysis_job_heartbeat_seconds": 1,
            "analysis_worker_leader_heartbeat_seconds": 1,
        })
        first = AnalysisJobManager(self.db, shutdown_settings)
        second = AnalysisJobManager(self.db, shutdown_settings)
        self.extra_managers.extend([first, second])
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=first_provider,
        ):
            job = first.enqueue(
                file_name="shutdown.pdf",
                content_type="application/pdf",
                payload=payload,
                analysis_mode="full_audit",
                force=True,
            )
            self.assertTrue(started.wait(timeout=5))
            stop_returned = threading.Event()

            def stop_first_manager() -> None:
                first.stop(timeout=0.5)
                stop_returned.set()

            stopper = threading.Thread(target=stop_first_manager)
            stopper.start()
            stopping_deadline = time.monotonic() + 0.4
            while time.monotonic() < stopping_deadline and not first.status()["stopping"]:
                time.sleep(0.005)
            self.assertTrue(first.status()["stopping"])
            self.assertFalse(first.status()["accepting_jobs"])
            self.assertFalse(stop_returned.is_set())
            with self.assertRaisesRegex(RuntimeError, "shutdown"):
                first.enqueue(
                    file_name="must-not-enter-during-shutdown.txt",
                    content_type="text/plain",
                    payload=b"job baru wajib ditolak",
                    analysis_mode="full_audit",
                    force=True,
                )
            stopper.join(timeout=2)
            self.assertTrue(stop_returned.is_set())
            draining = first.status()
            self.assertTrue(draining["draining"])
            self.assertTrue(draining["stopping"])
            self.assertFalse(draining["accepting_jobs"])
            self.assertGreaterEqual(draining["draining_seconds"], 0)
            self.assertTrue(draining["leader_lease_active"])
            second.start()
            self.assertFalse(second.status()["started"])
            self.assertIn("leader lain masih aktif", second.status()["blocked_reason"])
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and first.status()["draining"]:
                time.sleep(0.02)

        self.assertFalse(first.status()["draining"])
        self.assertFalse(first.status()["stopping"])
        self.assertEqual(first.status()["draining_seconds"], 0)
        repository = AnalysisRepository(self.db, settings=shutdown_settings)
        interrupted = repository.get_job(job["id"])
        self.assertEqual(interrupted["status"], "queued")
        source_run_id = int(interrupted["run_id"])
        self.assertEqual(first_provider.unit_keys, ["page-1", "page-2"])
        self.assertIsNotNone(repository.job_payload(job["id"]))

        class ResumeProvider:
            name = "resume_provider"

            def __init__(self):
                self.unit_keys = []

            def analyze_images(self, images):
                self.unit_keys.extend(str(image["unit_key"]) for image in images)
                return LocalOCRResponse(items=[
                    LocalOCRItem(
                        unit_key=str(image["unit_key"]),
                        text=f"OCR resumed {image['unit_key']}.",
                        confidence=0.96,
                        method="shutdown_resume_v1",
                    )
                    for image in images
                ])

        resume_provider = ResumeProvider()
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=resume_provider,
        ):
            second.start()
            self.assertTrue(second.status()["started"])
            deadline = time.monotonic() + 8
            completed = None
            while time.monotonic() < deadline:
                completed = repository.get_job(job["id"])
                if completed["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.02)

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempt_count"], 2)
        self.assertEqual(completed["resume_from_run_id"], source_run_id)
        self.assertEqual(resume_provider.unit_keys, ["page-3", "page-4"])
        self.assertTrue(any(
            event["event_type"] == "run_superseded_after_recovery"
            for event in repository.list_events(source_run_id)
        ))

    def test_retention_purges_bytes_but_preserves_document_record(self) -> None:
        repository = AnalysisRepository(self.db)
        document = repository.upsert_document(
            file_name="retained.txt",
            content_type="text/plain",
            size_bytes=6,
            sha256=hashlib.sha256(b"secret").hexdigest(),
            payload=b"secret",
            ttl_hours=1,
        )
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE documents SET purge_after = '2000-01-01 00:00:00' WHERE id = ?",
                (document["id"],),
            )
        self.assertEqual(repository.purge_expired_payloads(), 1)
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT pending_bytes, storage_status, sha256 FROM documents WHERE id = ?",
                (document["id"],),
            ).fetchone()
        self.assertIsNone(row["pending_bytes"])
        self.assertEqual(row["storage_status"], "purged")
        self.assertEqual(row["sha256"], hashlib.sha256(b"secret").hexdigest())

    def test_operational_metrics_do_not_include_document_content(self) -> None:
        repository = AnalysisRepository(self.db)
        metrics = repository.operational_metrics()
        self.assertIn("queue_by_status", metrics)
        self.assertIn("job_recovery", metrics)
        self.assertIn("verification", metrics)
        self.assertIn("batch_intakes_by_status", metrics)
        self.assertIn("batch_members_by_status", metrics)
        self.assertIn("vision_probes_by_status", metrics)
        self.assertIn("active_vision_decisions", metrics)
        self.assertNotIn("payload", metrics)

    def test_enqueue_is_idempotent_for_same_dedupe_key(self) -> None:
        repository = AnalysisRepository(self.db)
        first = repository.enqueue_job(
            file_name="same.txt",
            content_type="text/plain",
            payload=b"same",
            analysis_mode="full_audit",
            dedupe_key="same-config-key",
        )
        second = repository.enqueue_job(
            file_name="renamed.txt",
            content_type="text/plain",
            payload=b"same",
            analysis_mode="full_audit",
            dedupe_key="same-config-key",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["deduplicated"])

    def test_two_repositories_claim_jobs_atomically_without_duplicate(self) -> None:
        repository_a = AnalysisRepository(self.db)
        repository_b = AnalysisRepository(self.db)
        job = repository_a.enqueue_job(
            file_name="atomic.txt",
            content_type="text/plain",
            payload=b"atomic claim",
            analysis_mode="full_audit",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(
                lambda repository: repository.claim_next_job(lease_minutes=5),
                (repository_a, repository_b),
            ))
        claimed = [item for item in claims if item]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], job["id"])
        self.assertEqual(repository_a.get_job(job["id"])["attempt_count"], 1)

    def test_multi_replica_sqlite_worker_is_fail_closed(self) -> None:
        settings = self.settings.model_copy(update={"analysis_expected_replicas": 2})
        manager = AnalysisJobManager(self.db, settings)
        self.extra_managers.append(manager)
        manager.start()
        status = manager.status()
        self.assertFalse(status["started"])
        self.assertFalse(status["multi_instance_supported"])
        self.assertIn("satu app replica", status["blocked_reason"])
        with self.assertRaisesRegex(RuntimeError, "satu app replica"):
            manager.enqueue(
                file_name="blocked.txt",
                content_type="text/plain",
                payload=b"blocked",
                analysis_mode="full_audit",
            )

    def test_second_worker_manager_is_blocked_by_database_leader_lease(self) -> None:
        heartbeat_settings = self.settings.model_copy(update={
            "analysis_worker_leader_heartbeat_seconds": 1,
        })
        first = AnalysisJobManager(self.db, heartbeat_settings)
        second = AnalysisJobManager(self.db, heartbeat_settings)
        self.extra_managers.extend([first, second])
        first.start()
        self.assertTrue(first.status()["leader_lease_active"])

        second.start()
        blocked = second.status()
        self.assertFalse(blocked["started"])
        self.assertTrue(blocked["single_leader_enforced"])
        self.assertIn("leader lain masih aktif", blocked["blocked_reason"])

        first.stop()
        second.start()
        self.assertTrue(second.status()["started"])
        self.assertTrue(second.status()["leader_lease_active"])
        with patch.object(
            second.queue,
            "renew_leader",
            side_effect=sqlite3.OperationalError("simulated storage outage"),
        ):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and second.status()["started"]:
                time.sleep(0.02)
        self.assertFalse(second.status()["started"])
        self.assertIn("kehilangan leader lease", second.status()["blocked_reason"])

    def test_unknown_queue_backend_is_fail_closed_without_false_readiness(self) -> None:
        settings = self.settings.model_copy(update={"analysis_queue_backend": "postgresql"})
        manager = AnalysisJobManager(self.db, settings)
        self.extra_managers.append(manager)
        manager.start()
        status = manager.status()
        self.assertFalse(status["started"])
        self.assertFalse(status["multi_instance_supported"])
        self.assertFalse(status["single_leader_enforced"])
        self.assertIn("canonical persistence", status["blocked_reason"])
        self.assertTrue(status["queue_adapter"]["adapter_known"])
        self.assertEqual(status["queue_adapter"]["mode"], "canonical_postgresql")

    def test_redis_queue_name_is_recognized_but_sqlite_remains_fail_closed(self) -> None:
        settings = self.settings.model_copy(
            update={
                "analysis_queue_backend": "redis",
                "analysis_queue_redis_url": "rediss://example.invalid/0",
                "analysis_expected_replicas": 2,
            }
        )
        manager = AnalysisJobManager(self.db, settings)
        self.extra_managers.append(manager)
        manager.start()
        status = manager.status()
        self.assertFalse(status["started"])
        self.assertFalse(status["multi_instance_supported"])
        self.assertTrue(status["queue_adapter"]["adapter_known"])
        self.assertEqual(
            status["queue_adapter"]["mode"], "postgresql_with_redis_signal"
        )
        self.assertIn("canonical persistence PostgreSQL", status["blocked_reason"])

    def test_expired_worker_leader_can_be_reclaimed_but_active_owner_is_protected(self) -> None:
        repository = AnalysisRepository(self.db)
        self.assertTrue(repository.acquire_worker_leader("owner-a", lease_seconds=30))
        self.assertFalse(repository.acquire_worker_leader("owner-b", lease_seconds=30))
        self.assertFalse(repository.release_worker_leader("owner-b"))
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_worker_leases
                SET lease_until = '2000-01-01 00:00:00'
                WHERE lease_name = 'analysis-v2-worker'
                """
            )
        self.assertTrue(repository.acquire_worker_leader("owner-b", lease_seconds=30))
        self.assertFalse(repository.renew_worker_leader("owner-a", lease_seconds=30))
        self.assertTrue(repository.renew_worker_leader("owner-b", lease_seconds=30))
        self.assertTrue(repository.release_worker_leader("owner-b"))

    def test_running_job_heartbeat_renews_lease(self) -> None:
        repository = AnalysisRepository(self.db)
        job = repository.enqueue_job(
            file_name="heartbeat.txt",
            content_type="text/plain",
            payload=b"heartbeat",
            analysis_mode="screening",
        )
        claimed = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(claimed["id"], job["id"])
        self.assertTrue(repository.renew_job_lease(job["id"], lease_minutes=5))
        renewed = repository.get_job(job["id"])
        self.assertIsNotNone(renewed["heartbeat_at"])
        self.assertIsNotNone(renewed["lease_until"])

    def test_stale_claim_attempt_cannot_renew_attach_complete_fail_or_requeue(self) -> None:
        repository = AnalysisRepository(self.db)
        payload = b"attempt-fence"
        job = repository.enqueue_job(
            file_name="attempt.txt",
            content_type="text/plain",
            payload=payload,
            analysis_mode="full_audit",
        )
        first = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(first["attempt_count"], 1)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE analysis_jobs SET lease_until = '2000-01-01 00:00:00' WHERE id = ?",
                (job["id"],),
            )
        self.assertEqual(repository.recover_expired_jobs(), 1)
        self.assertFalse(repository.complete_job(
            job["id"], run_id=0, expected_attempt=1
        )["_write_applied"])
        self.assertFalse(repository.fail_job(
            job["id"], "stale queued failure", expected_attempt=1
        )["_write_applied"])
        self.assertIsNotNone(repository.job_payload(job["id"]))
        second = repository.claim_next_job(lease_minutes=1)
        self.assertEqual(second["attempt_count"], 2)

        document = repository.upsert_document(
            file_name="attempt.txt",
            content_type="text/plain",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
            ttl_hours=1,
        )
        run_id = repository.create_run(
            document_id=document["id"],
            analysis_mode="full_audit",
            pipeline_version=PIPELINE_VERSION,
            parser_version=PARSER_VERSION,
            rule_version=RULE_VERSION,
            prompt_version=PROMPT_VERSION,
            provider=None,
            model=None,
            configuration_hash=configuration_hash(self.settings, "full_audit"),
        )

        self.assertFalse(repository.renew_job_lease(
            job["id"], lease_minutes=5, expected_attempt=1
        ))
        self.assertTrue(repository.renew_job_lease(
            job["id"], lease_minutes=5, expected_attempt=2
        ))
        self.assertFalse(repository.attach_job_run(
            job["id"], run_id, expected_attempt=1
        ))
        self.assertTrue(repository.attach_job_run(
            job["id"], run_id, expected_attempt=2
        ))
        self.assertFalse(repository.supersede_nonterminal_job_run(
            job["id"], run_id, expected_attempt=1
        ))
        self.assertFalse(repository.requeue_job_after_worker_shutdown(
            job["id"], expected_attempt=1
        ))
        self.assertFalse(repository.complete_job(
            job["id"], run_id, expected_attempt=1
        )["_write_applied"])
        self.assertFalse(repository.fail_job(
            job["id"], "stale failure", expected_attempt=1
        )["_write_applied"])

        current = repository.get_job(job["id"])
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["attempt_count"], 2)
        self.assertEqual(current["run_id"], run_id)
        self.assertIsNotNone(repository.job_payload(job["id"]))
        self.assertTrue(repository.complete_job(
            job["id"], run_id, expected_attempt=2
        )["_write_applied"])


if __name__ == "__main__":
    unittest.main()
