from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.analysis.repository import AnalysisRepository


class AnalysisQueueBackend(Protocol):
    """Worker coordination whose authority remains in canonical persistence."""

    name: str
    adapter_known: bool

    @property
    def multi_instance_supported(self) -> bool: ...

    def validate_configuration(self, expected_replicas: int) -> str | None: ...
    def acquire_leader(self, owner_id: str, lease_seconds: int) -> bool: ...
    def renew_leader(self, owner_id: str, lease_seconds: int) -> bool: ...
    def release_leader(self, owner_id: str) -> None: ...
    def notify_job(self, job_id: str) -> None: ...
    def claim_next_job(self, lease_minutes: int) -> dict[str, Any] | None: ...
    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int,
        expected_attempt: int | None = None,
    ) -> bool: ...
    def diagnostics(self) -> dict[str, Any]: ...


class RedisJobSignal(Protocol):
    def ping(self) -> bool: ...
    def notify(self, job_id: str) -> None: ...
    def next_job_id(self) -> str | None: ...


_SHARED_REQUIREMENTS = (
    "canonical_backend_postgresql",
    "shared_across_replicas",
    "atomic_distributed_claims",
    "shared_payload_storage",
)
_NAMESPACE = re.compile(r"^[A-Za-z0-9:_-]{1,80}$")


def _capabilities(repository: AnalysisRepository) -> dict[str, Any]:
    provider = getattr(repository, "canonical_persistence_capabilities", None)
    raw = dict(provider()) if callable(provider) else {}
    return {
        "backend_name": str(raw.get("backend_name") or "unknown").lower(),
        "shared_across_replicas": bool(raw.get("shared_across_replicas")),
        "atomic_distributed_claims": bool(raw.get("atomic_distributed_claims")),
        "shared_payload_storage": bool(raw.get("shared_payload_storage")),
    }


def _shared_postgresql_ready(repository: AnalysisRepository) -> bool:
    capabilities = _capabilities(repository)
    return bool(
        capabilities["backend_name"] == "postgresql"
        and capabilities["shared_across_replicas"]
        and capabilities["atomic_distributed_claims"]
        and capabilities["shared_payload_storage"]
    )


def _diagnostics(
    *,
    name: str,
    adapter_known: bool,
    repository: AnalysisRepository,
    multi_instance_supported: bool,
    mode: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "adapter_known": adapter_known,
        "mode": mode,
        "multi_instance_supported": multi_instance_supported,
        "canonical_persistence": _capabilities(repository),
        "activation_requirements": list(_SHARED_REQUIREMENTS),
    }
    payload.update(extra or {})
    return payload


@dataclass
class SQLiteAnalysisQueue:
    repository: AnalysisRepository
    name: str = "sqlite"
    adapter_known: bool = True

    @property
    def multi_instance_supported(self) -> bool:
        return False

    def validate_configuration(self, expected_replicas: int) -> str | None:
        if expected_replicas > 1:
            return (
                "SQLite queue hanya boleh dipakai oleh satu app replica; "
                "shared queue saja tidak cukup karena canonical analysis state "
                "masih berada pada SQLite lokal."
            )
        return None

    def acquire_leader(self, owner_id: str, lease_seconds: int) -> bool:
        return self.repository.acquire_worker_leader(owner_id, lease_seconds)

    def renew_leader(self, owner_id: str, lease_seconds: int) -> bool:
        return self.repository.renew_worker_leader(owner_id, lease_seconds)

    def release_leader(self, owner_id: str) -> None:
        self.repository.release_worker_leader(owner_id)

    def notify_job(self, job_id: str) -> None:
        del job_id

    def claim_next_job(self, lease_minutes: int) -> dict[str, Any] | None:
        return self.repository.claim_next_job(lease_minutes=lease_minutes)

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int,
        expected_attempt: int | None = None,
    ) -> bool:
        return self.repository.renew_job_lease(
            job_id,
            lease_minutes=lease_minutes,
            expected_attempt=expected_attempt,
        )

    def diagnostics(self) -> dict[str, Any]:
        return _diagnostics(
            name=self.name,
            adapter_known=self.adapter_known,
            repository=self.repository,
            multi_instance_supported=False,
            mode="sqlite_singleton",
        )


@dataclass
class PostgreSQLAnalysisQueue:
    """Queue adapter for a repository whose canonical state is PostgreSQL.

    Atomic distributed claims are owned by that repository (normally using
    row locks/SKIP LOCKED). No process-wide singleton leader is used.
    """

    repository: AnalysisRepository
    name: str = "postgresql"
    adapter_known: bool = True

    @property
    def multi_instance_supported(self) -> bool:
        return _shared_postgresql_ready(self.repository)

    def validate_configuration(self, expected_replicas: int) -> str | None:
        del expected_replicas
        if self.multi_instance_supported:
            return None
        return (
            "Adapter PostgreSQL dikenali tetapi canonical persistence belum "
            "membuktikan shared PostgreSQL, atomic distributed claim, dan shared payload."
        )

    def acquire_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return self.multi_instance_supported

    def renew_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return self.multi_instance_supported

    def release_leader(self, owner_id: str) -> None:
        del owner_id

    def notify_job(self, job_id: str) -> None:
        del job_id

    def claim_next_job(self, lease_minutes: int) -> dict[str, Any] | None:
        if not self.multi_instance_supported:
            return None
        return self.repository.claim_next_job(lease_minutes=lease_minutes)

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int,
        expected_attempt: int | None = None,
    ) -> bool:
        if not self.multi_instance_supported:
            return False
        return self.repository.renew_job_lease(
            job_id,
            lease_minutes=lease_minutes,
            expected_attempt=expected_attempt,
        )

    def diagnostics(self) -> dict[str, Any]:
        return _diagnostics(
            name=self.name,
            adapter_known=self.adapter_known,
            repository=self.repository,
            multi_instance_supported=self.multi_instance_supported,
            mode="canonical_postgresql",
        )


class RedisPyJobSignal:
    """Non-authoritative Redis FIFO signal; PostgreSQL remains durable authority."""

    def __init__(
        self,
        url: str,
        *,
        namespace: str,
        connect_timeout_seconds: float,
        require_tls: bool,
    ):
        parsed = urlsplit(str(url or ""))
        allowed_schemes = {"rediss"} if require_tls else {"redis", "rediss"}
        if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
            raise ValueError("redis_url_policy")
        if parsed.fragment:
            raise ValueError("redis_url_fragment")
        if not _NAMESPACE.fullmatch(namespace):
            raise ValueError("redis_namespace")
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - production dependency gate
            raise RuntimeError("redis_driver_missing") from exc
        timeout = max(0.2, min(10.0, float(connect_timeout_seconds or 2.0)))
        self._client = redis.Redis.from_url(
            url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            decode_responses=True,
            protocol=2,
        )
        self._key = f"{namespace}:ready"

    def ping(self) -> bool:
        return bool(self._client.ping())

    def notify(self, job_id: str) -> None:
        pipeline = self._client.pipeline(transaction=True)
        pipeline.rpush(self._key, job_id)
        pipeline.ltrim(self._key, -10_000, -1)
        pipeline.execute()

    def next_job_id(self) -> str | None:
        value = self._client.lpop(self._key)
        return str(value) if value else None


@dataclass
class RedisAnalysisQueue:
    repository: AnalysisRepository
    signal: RedisJobSignal | None = None
    signal_configured: bool = False
    driver_available: bool = False
    signal_initialization_failed: bool = False
    name: str = "redis"
    adapter_known: bool = True
    _signal_ready: bool = field(default=False, init=False)
    _activated: bool = field(default=False, init=False)

    @property
    def canonical_ready(self) -> bool:
        return _shared_postgresql_ready(self.repository)

    @property
    def multi_instance_supported(self) -> bool:
        return bool(self.canonical_ready and self._activated)

    def validate_configuration(self, expected_replicas: int) -> str | None:
        del expected_replicas
        if not self.canonical_ready:
            return (
                "Adapter Redis dikenali, tetapi Redis hanya sinyal wake-up; canonical "
                "persistence PostgreSQL dan shared payload wajib aktif lebih dahulu."
            )
        if not self.signal_configured:
            return "Adapter Redis memerlukan ANALYSIS_QUEUE_REDIS_URL."
        if not self.driver_available:
            return "Adapter Redis memerlukan package redis-py yang tervalidasi."
        if self.signal_initialization_failed or self.signal is None:
            return "Adapter Redis gagal diinisialisasi oleh policy URL/TLS/namespace."
        try:
            self._signal_ready = bool(self.signal.ping())
        except Exception:
            self._signal_ready = False
        if not self._signal_ready:
            return "Adapter Redis tidak dapat mencapai signal backend saat startup."
        self._activated = True
        return None

    def acquire_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return self.multi_instance_supported

    def renew_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return self.multi_instance_supported

    def release_leader(self, owner_id: str) -> None:
        del owner_id

    def notify_job(self, job_id: str) -> None:
        if not self._signal_ready or self.signal is None:
            return
        try:
            self.signal.notify(job_id)
        except Exception:
            # Notification is non-authoritative; shared PostgreSQL polling remains safe.
            self._signal_ready = False

    def claim_next_job(self, lease_minutes: int) -> dict[str, Any] | None:
        if not self.canonical_ready:
            return None
        if self._signal_ready and self.signal is not None:
            for _ in range(32):
                try:
                    job_id = self.signal.next_job_id()
                except Exception:
                    self._signal_ready = False
                    break
                if not job_id:
                    break
                claimed = self.repository.claim_job(
                    job_id, lease_minutes=lease_minutes
                )
                if claimed:
                    return claimed
        # Redis loss or lost notification cannot lose a durable PostgreSQL job.
        return self.repository.claim_next_job(lease_minutes=lease_minutes)

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int,
        expected_attempt: int | None = None,
    ) -> bool:
        if not self.canonical_ready:
            return False
        return self.repository.renew_job_lease(
            job_id,
            lease_minutes=lease_minutes,
            expected_attempt=expected_attempt,
        )

    def diagnostics(self) -> dict[str, Any]:
        return _diagnostics(
            name=self.name,
            adapter_known=self.adapter_known,
            repository=self.repository,
            multi_instance_supported=self.multi_instance_supported,
            mode="postgresql_with_redis_signal",
            extra={
                "redis_signal": {
                    "configured": self.signal_configured,
                    "driver_available": self.driver_available,
                    "reachable": self._signal_ready,
                    "authoritative": False,
                    "fallback": "canonical_postgresql_polling",
                }
            },
        )


@dataclass
class UnsupportedAnalysisQueue:
    repository: AnalysisRepository
    name: str
    adapter_known: bool = False

    @property
    def multi_instance_supported(self) -> bool:
        return False

    def validate_configuration(self, expected_replicas: int) -> str | None:
        del expected_replicas
        return f"Queue backend {self.name!r} tidak dikenal dan ditolak fail-closed."

    def acquire_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return False

    def renew_leader(self, owner_id: str, lease_seconds: int) -> bool:
        del owner_id, lease_seconds
        return False

    def release_leader(self, owner_id: str) -> None:
        del owner_id

    def notify_job(self, job_id: str) -> None:
        del job_id

    def claim_next_job(self, lease_minutes: int) -> dict[str, Any] | None:
        del lease_minutes
        return None

    def renew_job_lease(
        self,
        job_id: str,
        lease_minutes: int,
        expected_attempt: int | None = None,
    ) -> bool:
        del job_id, lease_minutes, expected_attempt
        return False

    def diagnostics(self) -> dict[str, Any]:
        return _diagnostics(
            name=self.name,
            adapter_known=self.adapter_known,
            repository=self.repository,
            multi_instance_supported=False,
            mode="unsupported",
        )


def configured_queue_backend(
    repository: AnalysisRepository,
    configured_name: str | None,
    *,
    redis_url: str = "",
    redis_namespace: str = "spip:analysis:v2",
    redis_connect_timeout_seconds: float = 2.0,
    redis_require_tls: bool = True,
) -> AnalysisQueueBackend:
    name = str(configured_name or "sqlite").strip().lower()
    if name == "sqlite":
        return SQLiteAnalysisQueue(repository)
    if name == "postgresql":
        return PostgreSQLAnalysisQueue(repository)
    if name == "redis":
        driver_available = importlib.util.find_spec("redis") is not None
        signal: RedisJobSignal | None = None
        initialization_failed = False
        signal_configured = bool(str(redis_url or "").strip())
        # Never touch Redis while canonical state is still local SQLite.
        if signal_configured and driver_available and _shared_postgresql_ready(repository):
            try:
                signal = RedisPyJobSignal(
                    redis_url,
                    namespace=redis_namespace,
                    connect_timeout_seconds=redis_connect_timeout_seconds,
                    require_tls=redis_require_tls,
                )
            except (RuntimeError, ValueError):
                initialization_failed = True
        return RedisAnalysisQueue(
            repository,
            signal=signal,
            signal_configured=signal_configured,
            driver_available=driver_available,
            signal_initialization_failed=initialization_failed,
        )
    return UnsupportedAnalysisQueue(repository=repository, name=name or "unknown")
