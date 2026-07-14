from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from typing import Any
import uuid

from app.analysis.orchestrator import AnalysisOrchestrator, configuration_hash, normalize_analysis_mode
from app.analysis.contracts import utc_now_iso
from app.analysis.payload_storage import PayloadStorageError
from app.analysis.queue_backend import configured_queue_backend
from app.analysis.repository import AnalysisRepository
from app.analysis.shadow import ShadowComparisonService
from app.analysis.structured_logging import emit_analysis_log
from app.config import Settings
from app.database import Database


class WorkerShutdownForRecovery(RuntimeError):
    pass


class WorkerClaimLost(RuntimeError):
    pass


class AnalysisJobManager:
    """Durable leased worker; SQLite mode is intentionally limited to one app replica."""

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.repository = AnalysisRepository(db, settings=settings)
        self.queue = configured_queue_backend(
            self.repository,
            settings.analysis_queue_backend,
            redis_url=settings.analysis_queue_redis_url,
            redis_namespace=settings.analysis_queue_redis_namespace,
            redis_connect_timeout_seconds=(
                settings.analysis_queue_redis_connect_timeout_seconds
            ),
            redis_require_tls=settings.analysis_queue_redis_require_tls,
        )
        self._owner_id = uuid.uuid4().hex
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._blocked_reason: str | None = None
        self._leader_thread: threading.Thread | None = None
        self._leader_stop_event = threading.Event()
        self._leader_acquired = False
        self._stopping = False
        self._draining = False
        self._drain_started_monotonic: float | None = None
        self._drain_thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._stopping or self._draining:
                self._blocked_reason = (
                    "Worker sedang graceful shutdown; tunggu seluruh job aktif berhenti."
                )
                return
            if self._started:
                return
            replicas = max(1, int(self.settings.analysis_expected_replicas or 1))
            invalid_reason = self.queue.validate_configuration(replicas)
            if invalid_reason:
                self._blocked_reason = invalid_reason
                emit_analysis_log(
                    "worker_blocked",
                    stage="queue",
                    status="blocked",
                    reason_code="queue_configuration",
                    counters={"expected_replicas": replicas},
                )
                return
            leader_lease_seconds = max(
                10,
                min(300, int(self.settings.analysis_worker_leader_lease_seconds or 30)),
            )
            if not self.queue.acquire_leader(self._owner_id, leader_lease_seconds):
                self._blocked_reason = (
                    "Worker leader lain masih aktif untuk database ini; instance kedua "
                    "ditahan agar job tidak diproses oleh dua manager."
                )
                emit_analysis_log(
                    "worker_blocked",
                    stage="queue",
                    status="blocked",
                    reason_code="leader_unavailable",
                )
                return
            self._leader_acquired = True
            self._started = True
            self._stopping = False
            self._drain_started_monotonic = None
            self._blocked_reason = None
            self._stop_event.clear()
            self._leader_stop_event.clear()
            self.repository.purge_expired_payloads()
            self.repository.recover_expired_jobs()
            self.repository.cleanup_orphaned_payloads()
            self._leader_thread = threading.Thread(
                target=self._leader_heartbeat_loop,
                daemon=True,
                name="analysis-v2-leader-heartbeat",
            )
            self._leader_thread.start()
            worker_count = max(1, min(8, int(self.settings.analysis_worker_limit or 1)))
            for index in range(worker_count):
                thread = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name=f"analysis-v2-worker-{index + 1}",
                )
                self._threads.append(thread)
                thread.start()
            emit_analysis_log(
                "worker_started",
                stage="queue",
                status="running",
                counters={"worker_count": worker_count, "expected_replicas": replicas},
            )

    def stop(self, timeout: float = 3.0) -> None:
        with self._condition:
            was_active = bool(self._started or self._stopping or self._draining)
            self._stopping = True
            self._blocked_reason = (
                "Worker sedang graceful shutdown; job baru ditolak sampai shutdown selesai."
            )
            self._stop_event.set()
            self._condition.notify_all()
            drain_thread = self._drain_thread
            workers = list(self._threads)
        if was_active:
            emit_analysis_log(
                "worker_stopping",
                stage="queue",
                status="stopping",
                counters={"active_workers": sum(thread.is_alive() for thread in workers)},
            )
        safe_timeout = max(0.0, float(timeout))
        if drain_thread and drain_thread.is_alive():
            drain_thread.join(timeout=safe_timeout)
            return
        deadline = time.monotonic() + safe_timeout
        for thread in workers:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = [thread for thread in workers if thread.is_alive()]
        if alive:
            with self._condition:
                self._started = False
                self._draining = True
                self._drain_started_monotonic = time.monotonic()
                self._blocked_reason = (
                    "Worker sedang graceful shutdown; leader lease dipertahankan "
                    "sampai job aktif kembali ke antrean."
                )
                drain = threading.Thread(
                    target=self._drain_workers,
                    args=(alive,),
                    daemon=True,
                    name="analysis-v2-worker-drain",
                )
                self._drain_thread = drain
                drain.start()
            return
        self._finalize_stop()

    def _drain_workers(self, workers: list[threading.Thread]) -> None:
        for thread in workers:
            thread.join()
        self._finalize_stop()

    def _finalize_stop(self) -> None:
        self._leader_stop_event.set()
        leader_thread = self._leader_thread
        if leader_thread and leader_thread is not threading.current_thread():
            leader_thread.join(timeout=3.0)
        if self._leader_acquired:
            try:
                self.queue.release_leader(self._owner_id)
            except (OSError, sqlite3.Error):
                # Storage may already be unavailable during process teardown.
                # The bounded lease will expire without transferring ownership.
                pass
        with self._condition:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            self._leader_thread = None
            self._leader_acquired = False
            self._started = False
            self._stopping = False
            self._draining = False
            self._drain_started_monotonic = None
            self._drain_thread = None
            self._blocked_reason = None
        emit_analysis_log(
            "worker_stopped",
            stage="queue",
            status="stopped",
        )

    def enqueue(
        self,
        *,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        analysis_mode: str,
        force: bool = False,
        resume_from_run_id: int | None = None,
        external_ai_allowed: bool = True,
    ) -> dict[str, Any]:
        self.start()
        if self._blocked_reason:
            raise RuntimeError(self._blocked_reason)
        mode = normalize_analysis_mode(analysis_mode)
        dedupe_key = None
        if not force:
            document_hash = hashlib.sha256(payload).hexdigest()
            dedupe_material = (
                f"{document_hash}:{configuration_hash(self.settings, mode)}:"
                f"external-ai={int(bool(external_ai_allowed))}"
            )
            dedupe_key = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()
        job = self.repository.enqueue_job(
            file_name=file_name,
            content_type=content_type,
            payload=payload,
            analysis_mode=mode,
            dedupe_key=dedupe_key,
            resume_from_run_id=resume_from_run_id,
            external_ai_allowed=external_ai_allowed,
        )
        emit_analysis_log(
            "job_enqueued",
            job_id=str(job.get("id") or ""),
            run_id=(
                int(resume_from_run_id) if resume_from_run_id is not None else None
            ),
            stage="queue",
            status=str(job.get("status") or "queued"),
            reason_code="deduplicated" if job.get("deduplicated") else "accepted",
        )
        if job.get("status") == "queued":
            self.queue.notify_job(str(job.get("id") or ""))
        with self._condition:
            self._condition.notify()
        return job

    def describe(self, job_id: str, include_result: bool = True) -> dict[str, Any] | None:
        job = self.repository.get_job(job_id)
        if not job:
            return None
        payload: dict[str, Any] = {"job": job}
        run_id = job.get("run_id")
        if include_result and run_id:
            payload["result"] = AnalysisOrchestrator(self.db, self.settings).describe(int(run_id))
        return payload

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.repository.cancel_job(job_id)
        if job:
            emit_analysis_log(
                "job_cancelled",
                job_id=job_id,
                run_id=int(job["run_id"]) if job.get("run_id") is not None else None,
                stage="queue",
                status=str(job.get("status") or "cancelled"),
                reason_code="cancel_request",
            )
            with self._condition:
                self._condition.notify_all()
        return job

    def status(self) -> dict[str, Any]:
        drain_started = self._drain_started_monotonic
        draining_seconds = (
            max(0.0, time.monotonic() - drain_started)
            if self._draining and drain_started is not None
            else 0.0
        )
        singleton_leader_enforced = self.queue.name == "sqlite"
        leader_lease_active = bool(
            (self._started or self._stopping or self._draining)
            and (
                self.repository.worker_leader_status()["active"]
                if singleton_leader_enforced
                else self._leader_acquired
            )
        )
        accepting_jobs = bool(
            self._started
            and not self._stopping
            and not self._draining
            and not self._blocked_reason
            and leader_lease_active
        )
        return {
            "started": self._started,
            "stopping": self._stopping,
            "draining": self._draining,
            "draining_seconds": draining_seconds,
            "accepting_jobs": accepting_jobs,
            "worker_count": len(self._threads),
            "alive_workers": sum(thread.is_alive() for thread in self._threads),
            "queue_backend": self.queue.name,
            "queue_adapter": self.queue.diagnostics(),
            "expected_replicas": max(1, int(self.settings.analysis_expected_replicas or 1)),
            "multi_instance_supported": self.queue.multi_instance_supported,
            "single_leader_enforced": singleton_leader_enforced,
            "leader_lease_active": leader_lease_active,
            "blocked_reason": self._blocked_reason,
        }

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            job = self.queue.claim_next_job(
                lease_minutes=max(1, int(self.settings.analysis_job_lease_minutes or 15))
            )
            if not job:
                with self._condition:
                    self._condition.wait(timeout=1.0)
                continue
            self._process_job(job)

    def _process_job(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        claim_attempt = int(job.get("attempt_count") or 0)
        emit_analysis_log(
            "job_claimed",
            job_id=job_id,
            run_id=int(job["run_id"]) if job.get("run_id") is not None else None,
            stage="queue",
            status="running",
            attempt=claim_attempt,
        )
        try:
            payload = self.repository.job_payload(job_id)
        except PayloadStorageError as exc:
            self.repository.fail_job(
                job_id,
                f"Payload storage integrity gagal: {exc}",
                expected_attempt=claim_attempt,
            )
            emit_analysis_log(
                "job_failed",
                job_id=job_id,
                stage="payload_storage",
                status="failed",
                reason_code="payload_integrity",
                attempt=claim_attempt,
            )
            return
        if payload is None:
            self.repository.fail_job(
                job_id,
                "Payload job tidak tersedia.",
                expected_attempt=claim_attempt,
            )
            emit_analysis_log(
                "job_failed",
                job_id=job_id,
                stage="payload_storage",
                status="failed",
                reason_code="payload_missing",
                attempt=claim_attempt,
            )
            return
        previous_run_id = job.get("run_id")
        resume_from_run_id = job.get("resume_from_run_id")
        if previous_run_id:
            previous_run = self.repository.get_run(int(previous_run_id))
            if previous_run and previous_run.get("status") in {
                "blocked", "cancelled", "screening_complete", "review_required",
                "approved", "rejected", "uploaded", "failed",
            }:
                completed = self.repository.complete_job(
                    job_id,
                    int(previous_run_id),
                    expected_attempt=claim_attempt,
                )
                if not completed.get("_write_applied"):
                    return
                emit_analysis_log(
                    "job_completed",
                    job_id=job_id,
                    run_id=int(previous_run_id),
                    stage="orchestration",
                    status=str(previous_run.get("status") or "completed"),
                    reason_code="terminal_run_reused",
                    attempt=claim_attempt,
                )
                self._finalize_shadow_job(completed, int(previous_run_id))
                return
            if previous_run:
                if not self.repository.supersede_nonterminal_job_run(
                    job_id,
                    int(previous_run_id),
                    expected_attempt=claim_attempt,
                ):
                    refreshed_run = self.repository.get_run(int(previous_run_id))
                    if refreshed_run and refreshed_run.get("status") in {
                        "blocked", "cancelled", "screening_complete", "review_required",
                        "approved", "rejected", "uploaded", "failed",
                    }:
                        completed = self.repository.complete_job(
                            job_id,
                            int(previous_run_id),
                            expected_attempt=claim_attempt,
                        )
                        if completed.get("_write_applied"):
                            emit_analysis_log(
                                "job_completed",
                                job_id=job_id,
                                run_id=int(previous_run_id),
                                stage="orchestration",
                                status=str(refreshed_run.get("status") or "completed"),
                                reason_code="terminal_run_recovered",
                                attempt=claim_attempt,
                            )
                            self._finalize_shadow_job(completed, int(previous_run_id))
                    else:
                        self.repository.fail_job(
                            job_id,
                            "Transisi lease recovery gagal dipersistenkan; job ditahan.",
                            int(previous_run_id),
                            expected_attempt=claim_attempt,
                        )
                        emit_analysis_log(
                            "job_failed",
                            job_id=job_id,
                            run_id=int(previous_run_id),
                            stage="lease_recovery",
                            status="failed",
                            reason_code="recovery_transition_failed",
                            attempt=claim_attempt,
                        )
                    return
                resume_from_run_id = int(previous_run_id)
        heartbeat_stop = threading.Event()
        claim_lost = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job_id, claim_attempt, heartbeat_stop, claim_lost),
            daemon=True,
            name=f"analysis-v2-heartbeat-{job_id[:8]}",
        )
        heartbeat.start()
        try:
            external_ai_allowed = bool(job.get("external_ai_allowed", True))
            effective_settings = self.settings
            if not external_ai_allowed:
                effective_settings = self.settings.model_copy(
                    update={
                        "analysis_structured_model_enabled": False,
                        "analysis_model_verifier_enabled": False,
                        "vision_analysis_enabled": False,
                    }
                )
            def cancellation_requested() -> bool:
                if self.repository.job_cancel_requested(job_id):
                    return True
                if claim_lost.is_set():
                    raise WorkerClaimLost(
                        "Worker tidak lagi memiliki claim attempt job ini."
                    )
                if self._stop_event.is_set():
                    raise WorkerShutdownForRecovery(
                        "Worker shutdown diminta pada batas aman pipeline."
                    )
                return False

            def attach_run(run_id: int) -> None:
                if self.repository.attach_job_run(
                    job_id,
                    run_id,
                    expected_attempt=claim_attempt,
                ):
                    emit_analysis_log(
                        "run_attached",
                        job_id=job_id,
                        run_id=run_id,
                        stage="orchestration",
                        status="running",
                        reason_code="claim_bound",
                        attempt=claim_attempt,
                    )
                    return
                self.repository.update_run(
                    run_id,
                    status="failed",
                    primary_blocked=True,
                    block_reasons_json=["Worker kehilangan claim sebelum run terikat."],
                    error_message="Stale worker claim ditolak saat attach run.",
                    finished_at=utc_now_iso(),
                )
                self.repository.add_event(
                    run_id,
                    event_type="stale_worker_claim_rejected",
                    stage="orchestration",
                    progress=100,
                    message="Run orphan ditahan karena claim worker tidak lagi aktif.",
                )
                raise WorkerClaimLost(
                    "Claim berubah sebelum run baru dapat diikat ke job."
                )

            result = AnalysisOrchestrator(self.db, effective_settings).start(
                file_name=str(job.get("file_name") or "evidence"),
                content_type=job.get("content_type"),
                payload=payload,
                analysis_mode=str(job.get("analysis_mode") or "full_audit"),
                resume_from_run_id=(
                    int(resume_from_run_id) if resume_from_run_id is not None else None
                ),
                cancellation_check=cancellation_requested,
                run_created_callback=attach_run,
                external_ai_allowed=external_ai_allowed,
            )
            run_id = int(result["run"]["id"])
            completed = self.repository.complete_job(
                job_id,
                run_id,
                expected_attempt=claim_attempt,
            )
            if not completed.get("_write_applied"):
                raise WorkerClaimLost(
                    "Stale worker tidak diizinkan menutup job claim yang lebih baru."
                )
            emit_analysis_log(
                "job_completed",
                job_id=job_id,
                run_id=run_id,
                stage="orchestration",
                status=str((result.get("run") or {}).get("status") or "completed"),
                reason_code="pipeline_terminal",
                attempt=claim_attempt,
                counters={
                    "coverage_percentage": float(
                        (result.get("run") or {}).get("coverage_percentage") or 0
                    ),
                },
            )
            self._finalize_shadow_job(completed, run_id)
        except WorkerClaimLost:
            emit_analysis_log(
                "claim_lost",
                job_id=job_id,
                stage="queue",
                status="blocked",
                reason_code="attempt_fence",
                attempt=claim_attempt,
            )
        except WorkerShutdownForRecovery:
            self.repository.requeue_job_after_worker_shutdown(
                job_id,
                expected_attempt=claim_attempt,
            )
            emit_analysis_log(
                "job_requeued",
                job_id=job_id,
                stage="queue",
                status="queued",
                reason_code="worker_shutdown",
                attempt=claim_attempt,
            )
        except Exception as exc:
            self.repository.fail_job(
                job_id,
                f"Worker analysis gagal: {exc}",
                expected_attempt=claim_attempt,
            )
            emit_analysis_log(
                "job_failed",
                job_id=job_id,
                stage="orchestration",
                status="failed",
                reason_code="pipeline_exception",
                attempt=claim_attempt,
            )
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1.0)

    def _heartbeat_loop(
        self,
        job_id: str,
        claim_attempt: int,
        stop_event: threading.Event,
        claim_lost: threading.Event,
    ) -> None:
        interval = max(1, int(self.settings.analysis_job_heartbeat_seconds or 30))
        lease_minutes = max(1, int(self.settings.analysis_job_lease_minutes or 15))
        while not stop_event.wait(interval):
            try:
                renewed = self.queue.renew_job_lease(
                    job_id,
                    lease_minutes=lease_minutes,
                    expected_attempt=claim_attempt,
                )
            except (OSError, sqlite3.Error):
                renewed = False
            if not renewed:
                claim_lost.set()
                return

    def _finalize_shadow_job(self, job: dict[str, Any], run_id: int) -> None:
        job_id = str(job.get("id") or "")
        if not job_id:
            return
        if job.get("status") != "completed":
            self.repository.mark_shadow_job_status(job_id, "cancelled")
            return
        try:
            ShadowComparisonService(self.db, self.repository).finalize_job(job_id, run_id)
        except Exception:
            # Shadow telemetry has no decision authority and must not turn a
            # completed analysis into a failed run. The pair remains explicit.
            self.repository.mark_shadow_job_status(job_id, "failed")

    def _leader_heartbeat_loop(self) -> None:
        lease_seconds = max(
            10,
            min(300, int(self.settings.analysis_worker_leader_lease_seconds or 30)),
        )
        interval = max(
            1,
            min(
                lease_seconds // 2,
                int(self.settings.analysis_worker_leader_heartbeat_seconds or 10),
            ),
        )
        while not self._leader_stop_event.wait(interval):
            try:
                renewed = self.queue.renew_leader(self._owner_id, lease_seconds)
            except (OSError, sqlite3.Error):
                renewed = False
            if renewed:
                continue
            self._blocked_reason = (
                "Worker kehilangan leader lease; pemrosesan dihentikan fail-closed."
            )
            emit_analysis_log(
                "leader_lease_lost",
                stage="queue",
                status="blocked",
                reason_code="lease_renewal_failed",
            )
            self._leader_acquired = False
            self._started = False
            self._stop_event.set()
            self._leader_stop_event.set()
            with self._condition:
                self._condition.notify_all()
            return
