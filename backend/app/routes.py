from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.analysis.jobs import AnalysisJobManager
from app.analysis.repository import AnalysisRepository
from app.analysis.shadow import ShadowComparisonService
from app.database import Database
from app.evidence_link_crawler import EvidenceLinkCrawler
from app.evidence_structure import canonical_folder_path
from app.recommendations import attach_recommendations
from app.scanner import EvidenceScanner
from app.spip_mapping import EVIDENCE_CATEGORIES, KK_LIST, STATUS_EXPLANATIONS
from app.smart_upload import SmartUploadError, SmartUploadService
from app.sync_manager import SyncManager
from app.webdav_client import WebDavError, canonical_public_folder_url, public_folder_link


class SmartUploadConfirmRequest(BaseModel):
    review_id: int = Field(gt=0)
    candidate_index: int = Field(ge=0)


class SmartUploadActionRequest(BaseModel):
    review_id: int = Field(gt=0)
    candidate_index: int | None = Field(default=None, ge=0)
    action_type: str = Field(default="upload_primary")


sync_manager = SyncManager()
evidence_link_crawler = EvidenceLinkCrawler()


def current_folder_record(item: dict) -> dict:
    """Return an API-safe record whose path matches the physical folder."""
    enriched = dict(item)
    if item.get("folder_path"):
        enriched["folder_path"] = canonical_folder_path(item["folder_path"])
    if item.get("public_url"):
        enriched["public_url"] = canonical_public_folder_url(
            item["public_url"],
            enriched.get("folder_path") or item.get("folder_path"),
        )
    return enriched


def current_public_folder_link(settings, item: dict) -> str | None:
    """Resolve a fresh link instead of trusting a URL cached by an older release."""
    if not settings.has_share_token:
        return canonical_public_folder_url(item.get("public_url"), item.get("folder_path"))
    return public_folder_link(
        settings.lumbung_host,
        settings.lumbung_share_token,
        item["folder_path"],
    )


def create_router(db: Database, analysis_job_manager: AnalysisJobManager | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")
    legacy_telemetry = AnalysisRepository(db)

    def with_public_url(folder: dict) -> dict:
        settings = get_settings()
        enriched = current_folder_record(folder)
        parameter_entry = db.parameter_folder_entry(folder["kk_id"], folder["kode"])
        if parameter_entry:
            parameter_entry = current_folder_record(parameter_entry)
            enriched["parameter_entry_folder_path"] = parameter_entry["folder_path"]
            enriched["parameter_entry_detail_kode"] = parameter_entry["detail_kode"]
            enriched["parameter_count"] = parameter_entry["parameter_count"]

        if not settings.has_share_token:
            return enriched

        # Selalu bentuk ulang dari folder_path. Nilai public_url di database
        # dapat berasal dari sinkronisasi versi lama dan masih menunjuk ke
        # segmen panjang yang tidak ada secara fisik di LumbungFile.
        enriched["public_url"] = current_public_folder_link(settings, enriched)
        if parameter_entry:
            enriched["parameter_entry_public_url"] = public_folder_link(
                settings.lumbung_host,
                settings.lumbung_share_token,
                parameter_entry["folder_path"],
            )
        return enriched

    def hide_smart_upload_preview(result: dict) -> dict:
        if not isinstance(result, dict):
            return result
        cleaned = dict(result)
        cleaned.pop("preview_text", None)
        return cleaned

    def maybe_start_evidence_link_crawler(result: dict, settings) -> None:
        if isinstance(result, dict) and (result.get("link_crawl") or {}).get("needs_crawl"):
            evidence_link_crawler.start(db, settings)

    def maybe_enqueue_v2_shadow(
        result: dict,
        settings,
        *,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        analysis_mode: str,
    ) -> None:
        if not (
            settings.analysis_pipeline_v2_enabled
            and settings.analysis_pipeline_v2_shadow
            and analysis_job_manager
        ):
            return
        job = analysis_job_manager.enqueue(
            file_name=file_name,
            content_type=content_type,
            payload=payload,
            analysis_mode=analysis_mode,
        )
        legacy_review_id = int(result.get("review_id") or 0)
        if legacy_review_id:
            ShadowComparisonService(db).track(legacy_review_id, str(job["id"]))
        result["v2_shadow"] = {
            "job_id": job["id"],
            "status": job["status"],
            "comparison_tracked": bool(legacy_review_id),
            "decision_authority": "none",
        }

    @router.get("/health")
    def health() -> dict:
        settings = get_settings()
        return {
            "ok": True,
            "service": "spip-evidence-dashboard",
            "webdav_configured": settings.has_share_token,
            "analysis_pipeline_v2_enabled": settings.analysis_pipeline_v2_enabled,
            "analysis_pipeline_v2_shadow": settings.analysis_pipeline_v2_shadow,
            "batch_intake": {
                "enabled": settings.analysis_pipeline_v2_enabled,
                "default_review_limit": min(50, settings.analysis_batch_max_files),
                "max_files": settings.analysis_batch_max_files,
                "max_archive_bytes": settings.analysis_batch_max_archive_bytes,
                "max_entry_bytes": settings.analysis_batch_max_entry_bytes,
                "max_uncompressed_bytes": settings.analysis_batch_max_uncompressed_bytes,
                "default_local_only": True,
            },
        }

    @router.get("/health/live")
    def health_live() -> dict:
        """Process liveness only; dependency and rollout gates are intentionally excluded."""

        return {
            "ok": True,
            "status": "live",
            "service": "spip-evidence-dashboard",
        }

    @router.get("/health/ready")
    def health_ready(response: Response) -> dict:
        """Traffic readiness; fail closed while the V2 worker cannot accept new jobs."""

        settings = get_settings()
        pipeline_required = bool(settings.analysis_pipeline_v2_enabled)
        worker = analysis_job_manager.status() if analysis_job_manager else None
        reasons: list[str] = []
        if pipeline_required:
            if worker is None:
                reasons.append("worker_manager_unavailable")
            else:
                if worker.get("stopping"):
                    reasons.append("worker_stopping")
                if worker.get("draining"):
                    reasons.append("worker_draining")
                if not worker.get("started"):
                    reasons.append("worker_not_started")
                if not worker.get("leader_lease_active"):
                    reasons.append("worker_leader_lease_inactive")
                if worker.get("blocked_reason"):
                    reasons.append("worker_blocked")
                if not reasons and not worker.get("accepting_jobs"):
                    reasons.append("worker_not_accepting_jobs")
        ready = not reasons
        if not ready:
            response.status_code = 503
        return {
            "ok": ready,
            "status": "ready" if ready else "not_ready",
            "service": "spip-evidence-dashboard",
            "analysis_pipeline_v2_required": pipeline_required,
            "reason_codes": reasons,
            "worker": (
                {
                    "started": bool(worker.get("started")),
                    "stopping": bool(worker.get("stopping")),
                    "draining": bool(worker.get("draining")),
                    "accepting_jobs": bool(worker.get("accepting_jobs")),
                    "leader_lease_active": bool(worker.get("leader_lease_active")),
                    "queue_backend": worker.get("queue_backend") or "unknown",
                }
                if worker is not None
                else None
            ),
        }

    @router.get("/smart-upload/config")
    def smart_upload_config() -> dict:
        settings = get_settings()
        return {
            "enabled": settings.smart_upload_enabled,
            "allow_real_upload": settings.smart_upload_allow_real_upload,
            "require_confirmation": settings.smart_upload_require_confirmation,
            "ai_reasoning_enabled": settings.ai_reasoning_enabled,
            "require_ai": settings.smart_upload_require_ai,
            "ai_configured": settings.has_ai_key,
            "ai_provider": settings.ai_provider,
            "ai_model": settings.deepseek_model,
            "legacy_enabled": settings.legacy_smart_upload_enabled,
            "analysis_pipeline_v2_enabled": settings.analysis_pipeline_v2_enabled,
            "analysis_pipeline_v2_shadow": settings.analysis_pipeline_v2_shadow,
            "advanced_rag": {
                "enabled": settings.analysis_advanced_rag_enabled,
                "deepseek_enabled": settings.analysis_advanced_rag_deepseek_enabled,
                "model": (
                    settings.deepseek_model
                    if settings.analysis_advanced_rag_deepseek_enabled
                    else None
                ),
                "retrieval": "bm25_cosine_semantic_vector_rrf",
                "ai_authority": "query_expansion_and_demotion_only",
                "grade_authority": "domain_rule_only",
            },
            "batch_intake": {
                "enabled": settings.analysis_pipeline_v2_enabled,
                "default_review_limit": min(50, settings.analysis_batch_max_files),
                "max_files": settings.analysis_batch_max_files,
                "max_archive_bytes": settings.analysis_batch_max_archive_bytes,
                "max_entry_bytes": settings.analysis_batch_max_entry_bytes,
                "max_uncompressed_bytes": settings.analysis_batch_max_uncompressed_bytes,
                "default_local_only": True,
            },
        }

    @router.get("/smart-upload/ai-diagnostics")
    def smart_upload_ai_diagnostics() -> dict:
        settings = get_settings()
        legacy_telemetry.record_legacy_usage("ai_diagnostics", "legacy_api")
        service = SmartUploadService(db, settings)
        return service.test_ai_connection()

    @router.get("/smart-upload/evidence-links/status")
    def smart_upload_evidence_links_status() -> dict:
        return evidence_link_crawler.status(db)

    @router.post("/smart-upload/evidence-links/crawl")
    def smart_upload_evidence_links_crawl() -> dict:
        legacy_telemetry.record_legacy_usage("evidence_link_crawl", "legacy_api")
        return evidence_link_crawler.start(db, get_settings())

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
        slots = [with_slot_public_url(slot) for slot in db.evidence_slots(kk_id, kode)]
        attach_slots(parameters, slots)
        attach_recommendations(parameters)
        matrix_subunsur_name = parameters[0]["matrix_subunsur_name"] if parameters else None
        return {
            **with_public_url(folder),
            "matrix_subunsur_name": matrix_subunsur_name,
            "parameters": parameters,
            "evidence_slots": slots,
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

    @router.post("/sync/background")
    def sync_background() -> dict:
        settings = get_settings()
        return sync_manager.start_full(db, settings)

    @router.post("/sync/background/{kk_id}/{kode}")
    def sync_background_one(kk_id: str, kode: str) -> dict:
        settings = get_settings()
        try:
            return sync_manager.start_folder(db, settings, kk_id, kode)
        except WebDavError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/sync/status")
    def sync_status() -> dict:
        return sync_manager.status()

    @router.post("/sync/{kk_id}/{kode}")
    def sync_one(kk_id: str, kode: str) -> dict:
        settings = get_settings()
        scanner = EvidenceScanner(db, settings)
        try:
            return scanner.sync_folder(kk_id, kode)
        except WebDavError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/smart-upload/recommendations")
    async def smart_upload_recommendations(
        file: UploadFile = File(...),
        analysis_mode: str = Form("fast"),
        candidate_limit: int | None = Form(None),
    ) -> dict:
        settings = get_settings()
        if not settings.smart_upload_enabled or not settings.legacy_smart_upload_enabled:
            raise HTTPException(status_code=403, detail="Upload Evidence Pintar belum diaktifkan di environment ini.")
        legacy_telemetry.record_legacy_usage("recommendation", "legacy_api")
        payload = await read_smart_upload_payload(file, settings.smart_upload_max_bytes)
        service = SmartUploadService(db, settings)
        result = await run_in_threadpool(
            service.recommend,
            file_name=file.filename or "evidence",
            content_type=file.content_type,
            payload=payload,
            analysis_mode=analysis_mode,
            candidate_limit=candidate_limit,
        )
        maybe_enqueue_v2_shadow(
            result,
            settings,
            file_name=file.filename or "evidence",
            content_type=file.content_type,
            payload=payload,
            analysis_mode=analysis_mode,
        )
        maybe_start_evidence_link_crawler(result, settings)
        return hide_smart_upload_preview(result)

    @router.post("/smart-upload/recommendations/batch")
    async def smart_upload_recommendations_batch(
        files: list[UploadFile] = File(...),
        analysis_mode: str = Form("fast"),
        candidate_limit: int | None = Form(None),
    ) -> dict:
        settings = get_settings()
        if not settings.smart_upload_enabled or not settings.legacy_smart_upload_enabled:
            raise HTTPException(status_code=403, detail="Upload Evidence Pintar belum diaktifkan di environment ini.")
        if not files:
            raise HTTPException(status_code=400, detail="Pilih minimal satu file evidence.")
        legacy_telemetry.record_legacy_usage("batch_recommendation", "legacy_api")
        service = SmartUploadService(db, settings)
        results = []
        skip_ai_message = None
        for upload in files:
            payload = await read_smart_upload_payload(upload, settings.smart_upload_max_bytes)
            result = await run_in_threadpool(
                service.recommend,
                file_name=upload.filename or "evidence",
                content_type=upload.content_type,
                payload=payload,
                skip_ai_message=skip_ai_message,
                analysis_mode=analysis_mode,
                candidate_limit=candidate_limit,
            )
            maybe_enqueue_v2_shadow(
                result,
                settings,
                file_name=upload.filename or "evidence",
                content_type=upload.content_type,
                payload=payload,
                analysis_mode=analysis_mode,
            )
            maybe_start_evidence_link_crawler(result, settings)
            results.append(result)
            if result.get("ai", {}).get("status") == "unavailable" and not skip_ai_message and not settings.smart_upload_require_ai:
                skip_ai_message = "AI gateway sementara tidak tersedia; file berikutnya memakai rekomendasi lokal tanpa memanggil AI ulang."
        batch_ai = await run_in_threadpool(service.interpret_batch, results, analysis_mode, candidate_limit)
        return {
            "count": len(results),
            "results": [hide_smart_upload_preview(item) for item in results],
            "batch_ai": batch_ai,
            "batch_analysis": batch_ai.get("analysis"),
        }

    @router.post("/smart-upload/action")
    def smart_upload_action(payload: SmartUploadActionRequest) -> dict:
        settings = get_settings()
        if not settings.smart_upload_enabled or not settings.legacy_smart_upload_enabled:
            raise HTTPException(status_code=403, detail="Upload Evidence Pintar belum diaktifkan di environment ini.")
        legacy_telemetry.record_legacy_usage("action", "legacy_api")
        service = SmartUploadService(db, settings)
        try:
            return service.perform_action(payload.review_id, payload.candidate_index, payload.action_type)
        except SmartUploadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WebDavError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/smart-upload/confirm-upload")
    def smart_upload_confirm_upload(payload: SmartUploadConfirmRequest) -> dict:
        settings = get_settings()
        if not settings.smart_upload_enabled or not settings.legacy_smart_upload_enabled:
            raise HTTPException(status_code=403, detail="Upload Evidence Pintar belum diaktifkan di environment ini.")
        legacy_telemetry.record_legacy_usage("confirm_upload", "legacy_api")
        service = SmartUploadService(db, settings)
        try:
            return service.confirm_upload(payload.review_id, payload.candidate_index)
        except SmartUploadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WebDavError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router


def attach_slots(parameters: list[dict], slots: list[dict]) -> None:
    slot_map: dict[tuple[str, str], list[dict]] = {}
    for slot in slots:
        slot_map.setdefault((slot["detail_kode"], slot["grade"]), []).append(slot)

    for parameter in parameters:
        detail_kode = parameter.get("detail_kode")
        for grade in parameter.get("grades", []):
            grade_value = str(grade.get("grade") or "").strip().upper()
            grade["evidence_folders"] = slot_map.get((detail_kode, grade_value), [])


def with_slot_public_url(slot: dict) -> dict:
    settings = get_settings()
    enriched = current_folder_record(slot)
    if not settings.has_share_token:
        return enriched
    return {
        **enriched,
        "public_url": current_public_folder_link(settings, enriched),
    }


async def read_smart_upload_payload(file: UploadFile, max_bytes: int) -> bytes:
    if max_bytes > 0:
        payload = await file.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Ukuran file melebihi batas persiapan {max_bytes} byte.",
            )
        return payload
    return await file.read()
