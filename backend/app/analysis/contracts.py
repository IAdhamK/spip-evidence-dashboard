from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EngineStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SecurityFinding:
    severity: str
    code: str
    message: str
    blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DocumentIdentity:
    file_name: str
    content_type: str | None
    size_bytes: int
    sha256: str
    file_kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EngineResult:
    engine_name: str
    engine_version: str
    status: EngineStatus
    input_checksum: str = ""
    input_refs: list[str] = field(default_factory=list)
    output_refs: list[str] = field(default_factory=list)
    coverage: dict[str, int | float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | float] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None

    def finish(self) -> "EngineResult":
        if self.completed_at is None:
            self.completed_at = utc_now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload
