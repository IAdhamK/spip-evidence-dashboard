from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.analysis.repository import AnalysisRepository
from app.database import Database
from app.smart_upload import SmartUploadError, SmartUploadService
from app.webdav_client import WebDavError


class LegacyBridgeError(RuntimeError):
    """Sanitized boundary error raised by the legacy upload integration."""

    def __init__(self, message: str, *, legacy_review_id: int):
        super().__init__(message)
        self.legacy_review_id = legacy_review_id


def legacy_review_candidates(db: Database, review_id: int) -> list[dict[str, Any]] | None:
    """Read only the candidate contract needed by V2 shadow comparison."""
    review = db.smart_upload_review(review_id)
    if not review:
        return None
    try:
        candidates = json.loads(review.get("candidates_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def execute_legacy_controlled_upload(
    db: Database,
    settings: Settings,
    *,
    run: dict[str, Any],
    candidate: dict[str, Any],
    source_bytes: bytes,
) -> dict[str, Any]:
    """Use legacy WebDAV transport only after V2 has completed every gate.

    This module is the sole V2-to-legacy boundary. It deliberately does not
    perform grading, verification, or approval; those decisions remain in V2.
    """
    AnalysisRepository(db).record_legacy_usage("controlled_upload", "v2_bridge")
    legacy_review_id = db.record_smart_upload_review(
        file_name=str(run["file_name"]),
        content_type=run.get("content_type"),
        size_bytes=int(run.get("size_bytes") or len(source_bytes)),
        file_sha256=run.get("sha256"),
        preview_text="",
        candidates=[candidate],
        ai_status="v2_controlled",
        ai_message=(
            f"Prepared from analysis run #{run['id']} after all V2 approval gates."
        ),
        payload=source_bytes,
    )
    try:
        upload = SmartUploadService(db, settings).confirm_upload(legacy_review_id, 0)
    except (SmartUploadError, WebDavError) as exc:
        raise LegacyBridgeError(str(exc), legacy_review_id=legacy_review_id) from exc
    return {
        "legacy_review_id": legacy_review_id,
        "upload": upload,
    }
