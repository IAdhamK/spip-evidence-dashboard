from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import re
from typing import Mapping

from app.analysis import PIPELINE_VERSION


# The child inherits Uvicorn's configured stderr handler in deployed runtime,
# while remaining quiet under the default unittest/root WARNING configuration.
LOGGER_NAME = "uvicorn.error.spip.analysis"
LOG_SCHEMA_VERSION = "analysis-runtime-log-v1"

_EVENTS = {
    "worker_started",
    "worker_blocked",
    "worker_stopping",
    "worker_stopped",
    "job_enqueued",
    "job_claimed",
    "job_cancelled",
    "job_requeued",
    "job_completed",
    "job_failed",
    "run_attached",
    "claim_lost",
    "leader_lease_lost",
}
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_JOB_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def build_analysis_log_record(
    event: str,
    *,
    run_id: int | None = None,
    job_id: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    attempt: int | None = None,
    counters: Mapping[str, int | float | bool] | None = None,
) -> dict[str, object]:
    """Build a content-free analysis lifecycle log record.

    Arbitrary text is intentionally unsupported. Callers can only emit known
    lifecycle events, safe identifiers, slug-like state, and finite counters.
    Document names, source locations, prompts, provider responses, URLs, and
    exception messages therefore cannot accidentally enter runtime logs.
    """

    if event not in _EVENTS:
        raise ValueError(f"Unknown structured analysis log event: {event!r}")
    record: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": LOG_SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "event": event,
    }
    if run_id is not None:
        parsed_run_id = int(run_id)
        if parsed_run_id > 0:
            record["run_id"] = parsed_run_id
    if job_id is not None and _JOB_ID.fullmatch(str(job_id)):
        record["job_id"] = str(job_id)
    for key, value in (
        ("stage", stage),
        ("status", status),
        ("reason_code", reason_code),
    ):
        normalized = str(value or "").strip().lower()
        if normalized and _SLUG.fullmatch(normalized):
            record[key] = normalized
    if attempt is not None:
        record["attempt"] = max(0, int(attempt))
    safe_counters: dict[str, int | float] = {}
    for key, value in sorted((counters or {}).items()):
        normalized_key = str(key or "").strip().lower()
        if not _SLUG.fullmatch(normalized_key):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        safe_counters[normalized_key] = int(number) if number.is_integer() else number
    if safe_counters:
        record["counters"] = safe_counters
    return record


def emit_analysis_log(event: str, **fields: object) -> None:
    record = build_analysis_log_record(event, **fields)
    logging.getLogger(LOGGER_NAME).info(
        json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )
