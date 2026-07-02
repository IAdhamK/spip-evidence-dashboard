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
        slot_totals = self.sync_evidence_slots(kk_id, kode, scanned_at)
        if slot_totals["file_count"] > 0:
            slot_file_names = [f"evidence-{index}.dat" for index in range(slot_totals["file_count"])]
            rollup_status = classify_folder(slot_file_names)
            self.db.update_folder_rollup(
                kk_id=kk_id,
                kode=kode,
                status=rollup_status.status,
                status_reason=f"Terbaca {slot_totals['file_count']} file dari folder detail grade/kategori.",
                file_count=slot_totals["file_count"],
                total_size_bytes=slot_totals["total_size_bytes"],
                scanned_at=scanned_at,
            )
        updated = self.db.folder(kk_id, kode)
        return updated or {}

    def sync_evidence_slots(self, kk_id: str, kode: str, scanned_at: str) -> dict:
        totals = {"file_count": 0, "total_size_bytes": 0}
        for slot in self.db.evidence_slots(kk_id, kode):
            public_url = public_folder_link(
                self.settings.lumbung_host,
                self.settings.lumbung_share_token,
                slot["folder_path"],
            )
            try:
                items = self.client.list_folder(slot["folder_path"])
            except WebDavError as exc:
                self.db.update_evidence_slot_scan(
                    kk_id=kk_id,
                    kode=kode,
                    detail_kode=slot["detail_kode"],
                    grade=slot["grade"],
                    category_name=slot["category_name"],
                    public_url=public_url,
                    file_count=0,
                    total_size_bytes=0,
                    scanned_at=scanned_at,
                    error_message=str(exc),
                )
                continue

            file_items = [item for item in items if not item.is_folder]
            total_size = sum(item.size_bytes or 0 for item in file_items)
            totals["file_count"] += len(file_items)
            totals["total_size_bytes"] += total_size
            self.db.update_evidence_slot_scan(
                kk_id=kk_id,
                kode=kode,
                detail_kode=slot["detail_kode"],
                grade=slot["grade"],
                category_name=slot["category_name"],
                public_url=public_url,
                file_count=len(file_items),
                total_size_bytes=total_size,
                scanned_at=scanned_at,
                error_message=None,
            )
        return totals
