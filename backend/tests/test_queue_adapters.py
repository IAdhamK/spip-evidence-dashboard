from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.analysis.queue_backend import (
    PostgreSQLAnalysisQueue,
    RedisAnalysisQueue,
    RedisPyJobSignal,
    configured_queue_backend,
)
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database


class FakeSharedRepository:
    def __init__(self):
        self.jobs = [
            {"id": "job-00000001", "status": "queued", "attempt_count": 0},
            {"id": "job-00000002", "status": "queued", "attempt_count": 0},
        ]

    def canonical_persistence_capabilities(self):
        return {
            "backend_name": "postgresql",
            "shared_across_replicas": True,
            "atomic_distributed_claims": True,
            "shared_payload_storage": True,
        }

    def claim_job(self, job_id: str, lease_minutes: int = 15):
        del lease_minutes
        for job in self.jobs:
            if job["id"] == job_id and job["status"] == "queued":
                job["status"] = "running"
                job["attempt_count"] += 1
                return dict(job)
        return None

    def claim_next_job(self, lease_minutes: int = 15):
        del lease_minutes
        for job in self.jobs:
            claimed = self.claim_job(job["id"])
            if claimed:
                return claimed
        return None

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int = 15,
        expected_attempt: int | None = None,
    ):
        del lease_minutes
        return any(
            job["id"] == job_id
            and job["status"] == "running"
            and (
                expected_attempt is None
                or job["attempt_count"] == expected_attempt
            )
            for job in self.jobs
        )


class FakeRedisSignal:
    def __init__(self):
        self.ready = []
        self.fail_next = False

    def ping(self):
        return True

    def notify(self, job_id: str):
        self.ready.append(job_id)

    def next_job_id(self):
        if self.fail_next:
            raise OSError("simulated signal outage")
        return self.ready.pop(0) if self.ready else None


class QueueAdapterContractTests(unittest.TestCase):
    def test_redis_url_policy_requires_tls_and_safe_namespace_by_default(self):
        with self.assertRaisesRegex(ValueError, "redis_url_policy"):
            RedisPyJobSignal(
                "redis://127.0.0.1:6379/0",
                namespace="spip:analysis:v2",
                connect_timeout_seconds=1,
                require_tls=True,
            )
        with self.assertRaisesRegex(ValueError, "redis_namespace"):
            RedisPyJobSignal(
                "rediss://redis.example.invalid/0",
                namespace="unsafe namespace",
                connect_timeout_seconds=1,
                require_tls=True,
            )

    def test_postgresql_adapter_activates_only_for_proven_shared_canonical_state(self):
        repository = FakeSharedRepository()
        queue = PostgreSQLAnalysisQueue(repository)  # type: ignore[arg-type]
        self.assertIsNone(queue.validate_configuration(expected_replicas=3))
        self.assertTrue(queue.multi_instance_supported)
        self.assertTrue(queue.acquire_leader("replica-a", 30))
        claimed = queue.claim_next_job(lease_minutes=5)
        self.assertEqual(claimed["id"], "job-00000001")
        self.assertTrue(queue.renew_job_lease(claimed["id"], 5, expected_attempt=1))
        diagnostics = queue.diagnostics()
        self.assertEqual(diagnostics["mode"], "canonical_postgresql")
        self.assertTrue(diagnostics["multi_instance_supported"])

    def test_redis_signal_uses_notified_id_then_falls_back_to_canonical_polling(self):
        repository = FakeSharedRepository()
        signal = FakeRedisSignal()
        queue = RedisAnalysisQueue(  # type: ignore[arg-type]
            repository,
            signal=signal,
            signal_configured=True,
            driver_available=True,
        )
        self.assertIsNone(queue.validate_configuration(expected_replicas=4))
        queue.notify_job("job-00000002")
        claimed = queue.claim_next_job(lease_minutes=5)
        self.assertEqual(claimed["id"], "job-00000002")
        # A duplicate/stale signal cannot reclaim the same job. The durable
        # PostgreSQL fallback safely claims the remaining queued job.
        queue.notify_job("job-00000002")
        fallback = queue.claim_next_job(lease_minutes=5)
        self.assertEqual(fallback["id"], "job-00000001")
        diagnostics = queue.diagnostics()
        self.assertFalse(diagnostics["redis_signal"]["authoritative"])
        self.assertEqual(
            diagnostics["redis_signal"]["fallback"],
            "canonical_postgresql_polling",
        )
        signal.fail_next = True
        self.assertIsNone(queue.claim_next_job(lease_minutes=5))
        self.assertTrue(queue.multi_instance_supported)
        self.assertFalse(queue.diagnostics()["redis_signal"]["reachable"])

    def test_sqlite_never_connects_to_redis_or_exposes_url(self):
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                _env_file=None,
                database_path=str(Path(temp_dir) / "queue.db"),
                analysis_queue_backend="redis",
                analysis_queue_redis_url="rediss://user:secret@example.invalid/0",
            )
            database = Database(settings.database_path)
            repository = AnalysisRepository(database, settings=settings)
            with patch(
                "app.analysis.queue_backend.RedisPyJobSignal",
                side_effect=AssertionError("Redis must not initialize for SQLite"),
            ) as constructor:
                queue = configured_queue_backend(
                    repository,
                    settings.analysis_queue_backend,
                    redis_url=settings.analysis_queue_redis_url,
                    redis_namespace=settings.analysis_queue_redis_namespace,
                    redis_connect_timeout_seconds=(
                        settings.analysis_queue_redis_connect_timeout_seconds
                    ),
                    redis_require_tls=settings.analysis_queue_redis_require_tls,
                )
            constructor.assert_not_called()
            reason = queue.validate_configuration(expected_replicas=2)
            self.assertIn("canonical persistence PostgreSQL", reason)
            diagnostics = queue.diagnostics()
            serialized = json.dumps(diagnostics)
            self.assertTrue(diagnostics["adapter_known"])
            self.assertFalse(diagnostics["multi_instance_supported"])
            self.assertNotIn("example.invalid", serialized)
            self.assertNotIn("secret", serialized)

    def test_sqlite_claim_by_notified_id_is_atomic_and_duplicate_safe(self):
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                _env_file=None,
                database_path=str(Path(temp_dir) / "claim.db"),
            )
            database = Database(settings.database_path)
            repository = AnalysisRepository(database, settings=settings)
            job = repository.enqueue_job(
                file_name="claim.txt",
                content_type="text/plain",
                payload=b"claim payload",
                analysis_mode="screening",
            )
            first = repository.claim_job(job["id"], lease_minutes=5)
            second = repository.claim_job(job["id"], lease_minutes=5)
            self.assertIsNotNone(first)
            self.assertEqual(first["attempt_count"], 1)
            self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
