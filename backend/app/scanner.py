from __future__ import annotations

from datetime import datetime, timezone

from app.classifier import classify_folder
from app.config import Settings
from app.database import Database
from app.webdav_client import PublicShareWebDavClient, WebDavError, public_folder_link


class EvidenceScanner:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.client = PublicShareWebDavClient(
            host=settings.lumbung_host,
            share_token=settings.lumbung_share_token,
            timeout_seconds=settings.scan_timeout_seconds,
        )

    def sync_all(self) -> dict:
        folders = self.db.folders()
        result = {"total": len(folders), "synced": 0, "failed": 0, "errors": []}
        for folder in folders:
            try:
                self.sync_folder(folder["kk_id"], folder["kode"])
                result["synced"] += 1
            except WebDavError as exc:
                result["failed"] += 1
                result["errors"].append(
                    {"kk_id": folder["kk_id"], "kode": folder["kode"], "message": str(exc)}
                )
            except Exception as exc:
                result["failed"] += 1
                result["errors"].append(
                    {"kk_id": folder["kk_id"], "kode": folder["kode"], "message": f"Error tidak terduga: {exc}"}
                )
        return result

    def sync_folder(self, kk_id: str, kode: str) -> dict:
        folder = self.db.folder(kk_id, kode)
        if not folder:
            raise WebDavError(f"Folder {kk_id}/{kode} tidak ditemukan di mapping.")

        scanned_at = datetime.now(timezone.utc).isoformat()
        try:
            webdav_items = self.client.list_folder(folder["folder_path"])
        except WebDavError as exc:
            self.db.mark_scan_error(kk_id, kode, str(exc), scanned_at)
            raise

        files = [
            {
                "name": item.name,
                "href": item.href,
                "is_folder": item.is_folder,
                "size_bytes": item.size_bytes,
                "mime_type": item.mime_type,
                "modified_at": item.modified_at,
            }
            for item in webdav_items
        ]
        file_names = [item["name"] for item in files if not item["is_folder"]]
        status = classify_folder(file_names)
        public_url = public_folder_link(
            self.settings.lumbung_host,
            self.settings.lumbung_share_token,
            folder["folder_path"],
        )
        self.db.replace_scan_result(
            kk_id=kk_id,
            kode=kode,
            files=files,
            status=status.status,
            status_reason=status.reason,
            public_url=public_url,
            scanned_at=scanned_at,
        )
        updated = self.db.folder(kk_id, kode)
        return updated or {}

