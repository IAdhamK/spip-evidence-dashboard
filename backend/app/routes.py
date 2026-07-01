from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.database import Database
from app.scanner import EvidenceScanner
from app.spip_mapping import EVIDENCE_CATEGORIES, KK_LIST, STATUS_EXPLANATIONS
from app.webdav_client import WebDavError, public_folder_link


def create_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api")

    def with_public_url(folder: dict) -> dict:
        settings = get_settings()
        if not settings.has_share_token:
            return folder
        return {
            **folder,
            "public_url": folder.get("public_url")
            or public_folder_link(settings.lumbung_host, settings.lumbung_share_token, folder["folder_path"]),
        }

    @router.get("/health")
    def health() -> dict:
        settings = get_settings()
        return {
            "ok": True,
            "service": "spip-evidence-dashboard",
            "webdav_configured": settings.has_share_token,
        }

    @router.get("/meta")
    def meta() -> dict:
        return {
            "status_explanations": STATUS_EXPLANATIONS,
            "evidence_categories": EVIDENCE_CATEGORIES,
        }

    @router.get("/dashboard")
    def dashboard() -> dict:
        folders = [with_public_url(folder) for folder in db.folders()]
        status_counts: dict[str, int] = {}
        kk_summary: dict[str, dict] = {}

        for folder in folders:
            status_counts[folder["status"]] = status_counts.get(folder["status"], 0) + 1
            summary = kk_summary.setdefault(
                folder["kk_id"],
                {
                    "kk_id": folder["kk_id"],
                    "title": folder["kk_title"],
                    "total": 0,
                    "file_count": 0,
                    "total_size_bytes": 0,
                    "status_counts": {},
                },
            )
            summary["total"] += 1
            summary["file_count"] += folder["file_count"]
            summary["total_size_bytes"] += folder["total_size_bytes"]
            summary["status_counts"][folder["status"]] = summary["status_counts"].get(folder["status"], 0) + 1

        return {
            "total_folders": len(folders),
            "total_files": sum(folder["file_count"] for folder in folders),
            "total_size_bytes": sum(folder["total_size_bytes"] for folder in folders),
            "status_counts": status_counts,
            "kk_summary": list(kk_summary.values()),
            "folders": folders,
        }

    @router.get("/kk")
    def kk_list() -> list[dict]:
        folders = [with_public_url(folder) for folder in db.folders()]
        return [
            {
                "id": kk.id,
                "title": kk.title,
                "folder_name": kk.folder_name,
                "description": kk.description,
                "folders": [folder for folder in folders if folder["kk_id"] == kk.id],
            }
            for kk in KK_LIST
        ]

    @router.get("/kk/{kk_id}")
    def kk_detail(kk_id: str) -> dict:
        folders = [with_public_url(folder) for folder in db.folders(kk_id)]
        if not folders:
            raise HTTPException(status_code=404, detail="KK tidak ditemukan.")
        return {
            "kk_id": kk_id,
            "title": folders[0]["kk_title"],
            "folders": folders,
        }

    @router.get("/subunsur/{kk_id}/{kode}")
    def subunsur_detail(kk_id: str, kode: str) -> dict:
        folder = db.folder(kk_id, kode)
        if not folder:
            raise HTTPException(status_code=404, detail="Subunsur tidak ditemukan.")
        parameters = db.parameters(kk_id, kode)
        matrix_subunsur_name = parameters[0]["matrix_subunsur_name"] if parameters else None
        return {
            **with_public_url(folder),
            "matrix_subunsur_name": matrix_subunsur_name,
            "parameters": parameters,
            "files": db.files(kk_id, kode),
        }

    @router.get("/subunsur/{kk_id}/{kode}/files")
    def subunsur_files(kk_id: str, kode: str) -> list[dict]:
        if not db.folder(kk_id, kode):
            raise HTTPException(status_code=404, detail="Subunsur tidak ditemukan.")
        return db.files(kk_id, kode)

    @router.post("/sync")
    def sync_all() -> dict:
        settings = get_settings()
        scanner = EvidenceScanner(db, settings)
        return scanner.sync_all()

    @router.post("/sync/{kk_id}/{kode}")
    def sync_one(kk_id: str, kode: str) -> dict:
        settings = get_settings()
        scanner = EvidenceScanner(db, settings)
        try:
            return scanner.sync_folder(kk_id, kode)
        except WebDavError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router
