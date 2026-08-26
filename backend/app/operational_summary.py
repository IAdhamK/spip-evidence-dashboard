from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath


def _timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return float("-inf")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def _document_item(record: dict) -> dict:
    stored_name = str(record.get("name") or "").strip("/")
    return {
        "id": record.get("id"),
        "kk_id": record.get("kk_id"),
        "kode": record.get("kode"),
        "subunsur_name": record.get("subunsur_name"),
        "base_name": PurePosixPath(stored_name).name,
        "modified_at": record.get("modified_at"),
        "size_bytes": max(int(record.get("size_bytes") or 0), 0),
        "mime_type": record.get("mime_type"),
    }


def build_operational_summary(
    folders: list[dict],
    recent_files: list[dict],
    *,
    item_limit: int = 8,
) -> dict:
    """Derive a content-minimized operational snapshot from canonical metadata."""

    safe_limit = min(max(int(item_limit or 8), 1), 100)
    scanned = [folder for folder in folders if str(folder.get("last_scanned_at") or "").strip()]
    last_checked_at = max(
        (folder.get("last_scanned_at") for folder in scanned),
        key=_timestamp,
        default=None,
    )
    latest_documents = [
        _document_item(record)
        for record in sorted(
            recent_files,
            key=lambda item: _timestamp(item.get("modified_at")),
            reverse=True,
        )[:safe_limit]
        if str(record.get("modified_at") or "").strip()
    ]

    return {
        "last_checked_at": last_checked_at,
        "latest_document_modified_at": latest_documents[0]["modified_at"] if latest_documents else None,
        "latest_documents": latest_documents,
        "source": "synchronized_metadata",
    }
