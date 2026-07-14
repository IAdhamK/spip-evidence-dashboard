from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import shutil

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.batch_intake import (
    UnsafeBatchArchive,
    diverse_member_order,
    inspect_batch_archive,
    read_and_validate_member,
)
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.domain.retrieval import infer_document_role
from app.analysis.jobs import AnalysisJobManager
from app.analysis.legacy_bridge import (
    LegacyBridgeError,
    execute_legacy_controlled_upload,
    legacy_review_candidates,
)
from app.analysis.orchestrator import (
    CHECKPOINT_POLICY_VERSION,
    AnalysisOrchestrator,
    normalize_analysis_mode,
)
from app.analysis.domain.grading import build_rule_catalog
from app.analysis.expert_evaluation import (
    SERVER_DERIVED_GENERATION_METHOD,
    build_expert_gold_evaluation,
)
from app.analysis.governance import VISION_POLICY_VERSION, run_synthetic_vision_probe
from app.analysis.repository import AnalysisRepository
from app.analysis.reviewer_identity import (
    authorization_contract_summary,
    authorize_api_request,
)
from app.analysis.learning import EvaluationLearningEngine, RetrievalFeedbackLearningEngine
from app.analysis.local_ocr import local_ocr_runtime_status
from app.analysis.observability import OperationalAlertEngine
from app.analysis.office_render import office_slide_renderer_status
from app.analysis.payload_storage import PayloadStorageError
from app.analysis.prometheus import CONTENT_TYPE as PROMETHEUS_CONTENT_TYPE, render_prometheus_metrics
from app.analysis.rollout import RolloutGuardEngine
from app.analysis.routing import ROUTING_POLICY_VERSION
from app.analysis.shadow import (
    ShadowComparisonService,
    build_shadow_comparison,
)
from app.analysis.storage_evidence import storage_encryption_attestation_status
from app.analysis.visual_review import VisualPreviewError, extract_visual_preview
from app.config import get_settings
from app.database import Database


class HumanReviewDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    decision: str = Field(pattern="^(approve|correct|reject)$")
    mapping_candidate_id: int | None = Field(default=None, gt=0)
    final_mapping: dict = Field(default_factory=dict)
    reason: str = Field(min_length=8, max_length=2000)


class RuleApprovalRequest(BaseModel):
    kk_id: str = Field(min_length=2, max_length=30)
    kode: str = Field(min_length=1, max_length=30)
    detail_kode: str = Field(min_length=1, max_length=40)
    grade: str = Field(pattern="^[A-E]$")
    rule_checksum: str = Field(pattern="^[a-f0-9]{64}$")
    status: str = Field(pattern="^(approved|rejected)$")
    reviewer_id: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=8, max_length=2000)


class GovernanceRuleDecisionItem(BaseModel):
    kk_id: str = Field(min_length=2, max_length=30)
    kode: str = Field(min_length=1, max_length=30)
    detail_kode: str = Field(min_length=1, max_length=40)
    grade: str = Field(pattern="^[A-E]$")
    rule_checksum: str = Field(pattern="^[a-f0-9]{64}$")
    status: str = Field(pattern="^(approved|rejected)$")


class GovernanceRuleDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=8, max_length=2000)
    attested: bool
    decisions: list[GovernanceRuleDecisionItem] = Field(min_length=1, max_length=25)


class VisionProbeRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)


class VisionGovernanceDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    scope: str = Field(pattern="^(capability_validation|external_data_processing)$")
    status: str = Field(pattern="^(approved|rejected|revoked)$")
    sensitivity_scope: str = Field(default="restricted", pattern="^(public|internal|restricted)$")
    evidence_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    expires_in_days: int = Field(default=365, ge=1, le=365)
    reason: str = Field(min_length=8, max_length=2000)
    attested: bool


class ControlledUploadRequest(BaseModel):
    mapping_candidate_id: int = Field(gt=0)
    reviewer_id: str = Field(min_length=2, max_length=120)
    category_name: str | None = Field(default=None, max_length=200)


class ControlledUploadReconciliationRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    outcome: str = Field(
        pattern="^(confirmed_uploaded|confirmed_not_uploaded|needs_investigation)$"
    )
    reason: str = Field(min_length=8, max_length=2000)
    attested: bool
    expected_latest_event_id: int | None = Field(default=None, gt=0)


class EvaluationReportRequest(BaseModel):
    dataset_name: str = Field(min_length=3, max_length=200)
    dataset_status: str = Field(pattern="^expert_gold$")
    case_count: int = Field(gt=0, le=100000)
    retrieval_recall_at_5: float = Field(ge=0, le=1)
    source_accuracy: float = Field(ge=0, le=1)
    overgrade_rate: float = Field(ge=0, le=1)
    grade_label_coverage: float = Field(default=0, ge=0, le=1)
    grade_assessment_coverage: float = Field(default=0, ge=0, le=1)
    report_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    dataset_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    reviewer_id: str = Field(min_length=2, max_length=120)
    notes: str = Field(default="", max_length=2000)


class GeneratedEvaluationReportRequest(BaseModel):
    dataset_name: str = Field(min_length=3, max_length=200)
    reviewer_id: str = Field(min_length=2, max_length=120)
    notes: str = Field(default="", max_length=2000)
    attested: bool


class ReleaseEvidenceRequest(BaseModel):
    release_cycle_id: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
    release_version: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    stage: str = Field(pattern="^(shadow|pilot|canary|general)$")
    decision: str = Field(pattern="^(planned|started|passed|failed|rolled_back)$")
    dataset_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    comparison_report_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    evaluation_report_id: int | None = Field(default=None, gt=0)
    stable_cycle: bool = False
    rollback_rehearsed: bool = False
    critical_incident_count: int = Field(default=0, ge=0, le=100000)
    reviewer_id: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=8, max_length=2000)
    evidence: dict = Field(default_factory=dict)
    attested: bool


class CandidateExpansionRequest(BaseModel):
    limit: int = Field(default=30, ge=11, le=50)


class GuidedExpertReviewRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    outcome: str = Field(pattern="^(confirmed|corrected|not_evidence|unsure)$")
    selected_mapping_candidate_id: int | None = Field(default=None, gt=0)
    selected_source_fact_ids: list[int] = Field(default_factory=list, max_length=100)
    expected_mapping: dict = Field(default_factory=dict)
    expected_evidence_role: str | None = Field(
        default=None,
        pattern="^(primary|supporting|context|contradictory)$",
    )
    expected_template_status: str = Field(
        default="not_assessed",
        pattern="^(template_only|substantive|not_assessed)$",
    )
    reason: str = Field(min_length=8, max_length=2000)


class ExpertDatasetDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    decision: str = Field(pattern="^(approve|return)$")
    dataset_partition: str = Field(default="evaluation", pattern="^(evaluation|learning)$")
    reason: str = Field(min_length=8, max_length=2000)
    attested: bool


class NormalizedVisualBoundingBox(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class VisualSemanticRegionRequest(BaseModel):
    region_type: str = Field(
        pattern="^(picture|chart|diagram|signature|stamp|table|other)$"
    )
    label: str = Field(default="", max_length=300)
    bbox: NormalizedVisualBoundingBox


class VisualReviewDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    review_kind: str = Field(pattern="^(visual_semantics|ocr_rescue)$")
    decision: str = Field(pattern="^(confirmed|corrected|not_evidence|unsure)$")
    unit_text_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    source_image_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    ocr_candidate_text_sha256: str | None = Field(
        default=None,
        pattern="^[a-f0-9]{64}$",
    )
    reviewed_text: str = Field(default="", max_length=20000)
    semantic_description: str = Field(default="", max_length=2000)
    semantic_regions: list[VisualSemanticRegionRequest] = Field(
        default_factory=list,
        max_length=50,
    )
    reason: str = Field(min_length=8, max_length=2000)
    expected_latest_decision_id: int | None = Field(default=None, gt=0)
    attested: bool


class VisualReviewApplyRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    visual_review_checksum: str = Field(pattern="^[a-f0-9]{64}$")
    reason: str = Field(min_length=8, max_length=2000)
    attested: bool


def create_analysis_router(db: Database, job_manager: AnalysisJobManager | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/analysis-runs", tags=["Document Intelligence V2"])
    manager = job_manager or AnalysisJobManager(db, get_settings())

    def require_guided_review_access(request: Request) -> None:
        settings = get_settings()
        if settings.analysis_require_reviewer_identity or settings.analysis_require_reviewer_role:
            authorize_api_request(request, settings, "")

    def require_analysis_access(request: Request) -> None:
        settings = get_settings()
        if settings.analysis_require_reviewer_identity or settings.analysis_require_reviewer_role:
            authorize_api_request(request, settings, "")

    def require_governance_access(request: Request) -> None:
        settings = get_settings()
        if settings.analysis_require_reviewer_identity or settings.analysis_require_reviewer_role:
            authorize_api_request(request, settings, "")

    def load_document_payload(repository: AnalysisRepository, run_id: int) -> bytes | None:
        try:
            return repository.document_payload(run_id)
        except PayloadStorageError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Payload dokumen gagal diverifikasi pada storage; "
                    "run dipertahankan fail-closed dan memerlukan restore/unggah ulang."
                ),
            ) from exc

    def visual_review_binding(unit: dict) -> dict:
        metadata = unit.get("metadata") or {}
        current_text = str(unit.get("text") or "")
        unit_text_sha256 = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
        if metadata.get("visual_semantics_status") == "pending_review_or_vision":
            review_kind = "visual_semantics"
            review_text = current_text
            candidate_sha256 = None
        elif (
            unit.get("status") == "ocr_required"
            and metadata.get("ocr_source_image_sha256")
            and (
                metadata.get("ocr_review_candidate_text_sha256")
                or metadata.get("ocr_manual_review_required")
            )
        ):
            review_kind = "ocr_rescue"
            review_text = str(metadata.get("ocr_review_candidate_text") or "")
            candidate_sha256 = (
                str(metadata["ocr_review_candidate_text_sha256"])
                if metadata.get("ocr_review_candidate_text_sha256")
                else None
            )
        else:
            raise HTTPException(
                status_code=409,
                detail="Unit ini tidak lagi menunggu review visual atau OCR rescue.",
            )
        return {
            "review_kind": review_kind,
            "review_text": review_text,
            "unit_text_sha256": unit_text_sha256,
            "source_image_sha256": str(metadata.get("ocr_source_image_sha256") or ""),
            "ocr_candidate_text_sha256": candidate_sha256,
        }

    def current_promotion_snapshot(
        repository: AnalysisRepository,
        settings: object,
        reports: list[dict] | None = None,
    ) -> dict:
        catalog = build_rule_catalog(
            repository.parameter_index(), repository.list_rule_approvals()
        )
        metrics = repository.operational_metrics()
        security = metrics.get("security_findings_by_severity") or {}
        vision_governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        local_ocr = local_ocr_runtime_status(settings)
        return EvaluationLearningEngine().promotion_readiness(
            reports if reports is not None else repository.list_evaluation_reports(PIPELINE_VERSION),
            approved_rule_count=sum(
                item["approval_status"] == "approved" for item in catalog
            ),
            total_rule_count=len(catalog),
            high_security_findings=(
                int(security.get("high") or 0) + int(security.get("critical") or 0)
            ),
            vision_required=int(metrics.get("ocr_run_count") or 0) > 0,
            vision_ready=bool(local_ocr["available"] or vision_governance["effective"]),
            storage_ready=bool(storage_encryption_attestation_status(settings)["effective"]),
        )

    @router.get("/config")
    def analysis_config() -> dict:
        settings = get_settings()
        repository = AnalysisRepository(db, settings=settings)
        local_ocr = local_ocr_runtime_status(settings)
        office_renderer = office_slide_renderer_status()
        vision_governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        office_renderer_payload = {
            **office_renderer,
            "enabled": settings.analysis_office_rendering_enabled,
            "effective": bool(
                settings.analysis_office_rendering_enabled
                and office_renderer["available"]
            ),
            "max_pages_per_full_audit": max(
                1, min(1_000, settings.analysis_office_render_max_pages)
            ),
            "supported_formats": ["docx", "xlsx", "pptx"],
        }
        return {
            "enabled": settings.analysis_pipeline_v2_enabled,
            "shadow": settings.analysis_pipeline_v2_shadow,
            "pipeline_version": PIPELINE_VERSION,
            "parser_version": PARSER_VERSION,
            "rule_version": RULE_VERSION,
            "prompt_version": PROMPT_VERSION,
            "vision_enabled": settings.vision_analysis_enabled,
            "vision_provider_validated": settings.analysis_vision_provider_validated,
            "vision_governance": vision_governance,
            "local_ocr": local_ocr,
            "office_renderer": office_renderer_payload,
            "office_slide_renderer": office_renderer_payload,
            "verification_enabled": settings.verification_pass_enabled,
            "model_verifier_enabled": settings.analysis_model_verifier_enabled,
            "structured_model_enabled": settings.analysis_structured_model_enabled,
            "advanced_rag": {
                "enabled": settings.analysis_advanced_rag_enabled,
                "deepseek_enabled": settings.analysis_advanced_rag_deepseek_enabled,
                "model": settings.deepseek_model
                if settings.analysis_advanced_rag_deepseek_enabled else None,
                "retrieval": "bm25_cosine_semantic_vector_rrf",
                "query_expansion_authority": "retrieval_only",
                "reranker_authority": "rerank_and_demotion_only",
                "grade_authority": "domain_rule_only",
                "minimum_confidence": max(
                    0.0, min(1.0, settings.analysis_advanced_rag_min_confidence)
                ),
                "ambiguity_margin": max(
                    0.0, min(1.0, settings.analysis_advanced_rag_ambiguity_margin)
                ),
            },
            "compute_routing": {
                "policy_version": ROUTING_POLICY_VERSION,
                "mapping_reasoning_enabled": (
                    settings.analysis_mapping_reasoning_enabled
                    or (
                        settings.analysis_advanced_rag_enabled
                        and settings.analysis_advanced_rag_deepseek_enabled
                    )
                ),
                "structured_min_complexity": max(
                    0.0,
                    min(1.0, settings.analysis_routing_structured_min_complexity),
                ),
                "mapping_ambiguity_margin": max(
                    0.0,
                    min(1.0, settings.analysis_routing_mapping_margin),
                ),
                "model_verifier_min_risk": max(
                    0.0,
                    min(1.0, settings.analysis_routing_verifier_min_risk),
                ),
                "mapping_model_authority": "demotion_only",
                "grade_authority": "domain_rule_only",
            },
            "checkpointing": {
                "policy_version": CHECKPOINT_POLICY_VERSION,
                "visual_ocr_batch_durable": True,
                "partial_resume_checksum_bound": True,
            },
            "ai_provider": settings.ai_provider,
            "ai_model": settings.deepseek_model,
            "api_surface": settings.analysis_api_surface,
            "ai_configured": settings.has_ai_key,
            "allow_partial_primary": settings.allow_partial_primary,
            "rollout_stage": settings.analysis_rollout_stage,
            "canary_percentage": max(0, min(100, settings.analysis_canary_percentage)),
            "supported_modes": ["screening", "full_audit"],
            "job_manager": manager.status(),
            "observability": {
                "prometheus_metrics_enabled": settings.analysis_prometheus_metrics_enabled,
                "prometheus_path": "/api/analysis-runs/metrics/prometheus",
                "alertmanager_default_delivery_enabled": False,
                "alertmanager_webhook_profile": (
                    "ops/alertmanager/alertmanager.webhook.yml"
                ),
            },
            "reviewer_identity": {
                "required": settings.analysis_require_reviewer_identity,
                "trusted_header": settings.analysis_reviewer_identity_header,
                "role_required": settings.analysis_require_reviewer_role,
                "trusted_role_header": settings.analysis_reviewer_role_header,
                "authorization_mode": (
                    "trusted_role_rbac"
                    if settings.analysis_require_reviewer_role
                    else "identity_only"
                    if settings.analysis_require_reviewer_identity
                    else "development_payload_identity"
                ),
                "proxy_reference": "ops/reverse-proxy/nginx.conf",
                "direct_backend_access_must_be_blocked": True,
            },
            "authorization_contract": authorization_contract_summary(),
            "payload_storage": repository.payload_storage_status(),
            "batch_intake": {
                "enabled": True,
                "default_review_limit": min(50, settings.analysis_batch_max_files),
                "max_files": settings.analysis_batch_max_files,
                "max_archive_bytes": settings.analysis_batch_max_archive_bytes,
                "max_entry_bytes": settings.analysis_batch_max_entry_bytes,
                "max_uncompressed_bytes": settings.analysis_batch_max_uncompressed_bytes,
                "default_local_only": True,
            },
        }

    @router.post("", status_code=status.HTTP_202_ACCEPTED)
    async def create_analysis_run(
        request: Request,
        file: UploadFile = File(...),
        analysis_mode: str = Form("full_audit"),
        force: bool = Form(False),
    ) -> dict:
        settings = get_settings()
        authorize_api_request(request, settings, "")
        if not settings.analysis_pipeline_v2_enabled:
            raise HTTPException(
                status_code=403,
                detail="Document Intelligence Pipeline V2 belum diaktifkan.",
            )
        payload = await _read_payload(file, settings.smart_upload_max_bytes)
        job = manager.enqueue(
            file_name=file.filename or "evidence",
            content_type=file.content_type,
            payload=payload,
            analysis_mode=analysis_mode,
            force=force,
        )
        return {"job": job}

    @router.post("/batch-intakes", status_code=status.HTTP_202_ACCEPTED)
    async def create_batch_intake(
        request: Request,
        file: UploadFile = File(...),
        analysis_mode: str = Form("full_audit"),
        review_limit: int = Form(50),
        local_only: bool = Form(True),
        force: bool = Form(False),
    ) -> dict:
        settings = get_settings()
        authorize_api_request(request, settings, "")
        if not settings.analysis_pipeline_v2_enabled:
            raise HTTPException(
                status_code=403,
                detail="Document Intelligence Pipeline V2 belum diaktifkan.",
            )
        if review_limit < 1 or review_limit > settings.analysis_batch_max_files:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Jumlah dokumen harus 1-{settings.analysis_batch_max_files}."
                ),
            )
        archive_payload = await _read_payload(file, settings.analysis_batch_max_archive_bytes)
        archive_name = file.filename or "batch.zip"
        repository = AnalysisRepository(db)
        mode = normalize_analysis_mode(analysis_mode)
        try:
            audit, members = inspect_batch_archive(archive_payload, archive_name, settings)
        except UnsafeBatchArchive as exc:
            dedupe_key = None if force else _batch_dedupe_key(
                exc.audit["archive_sha256"], mode, review_limit, not local_only
            )
            batch, _ = repository.create_batch_intake(
                archive_file_name=archive_name,
                archive_sha256=exc.audit["archive_sha256"],
                archive_size_bytes=len(archive_payload),
                analysis_mode=mode,
                requested_limit=review_limit,
                external_ai_allowed=not local_only,
                dedupe_key=dedupe_key,
                audit=exc.audit,
                status="rejected",
                error_message="; ".join(exc.errors)[:2000],
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"ZIP ditolak sebelum dokumen dianalisis (batch {batch['id']}): "
                    + "; ".join(exc.errors[:3])
                ),
            ) from exc

        dedupe_key = None if force else _batch_dedupe_key(
            audit["archive_sha256"], mode, review_limit, not local_only
        )
        batch, created = repository.create_batch_intake(
            archive_file_name=archive_name,
            archive_sha256=audit["archive_sha256"],
            archive_size_bytes=len(archive_payload),
            analysis_mode=mode,
            requested_limit=review_limit,
            external_ai_allowed=not local_only,
            dedupe_key=dedupe_key,
            audit=audit,
        )
        if not created:
            return {"batch": batch, "deduplicated": True, "review_url": "#/guided-review"}

        ordered = diverse_member_order(members, review_limit)
        original_ordinals = {
            item["archive_path"]: index for index, item in enumerate(members, start=1)
        }
        handled_paths: set[str] = set()
        seen_hashes: set[str] = set()
        selected_count = 0
        enqueued_count = 0
        rejected_count = 0
        duplicate_count = 0

        for member in ordered:
            if enqueued_count >= review_limit:
                break
            handled_paths.add(member["archive_path"])
            selected_count += 1
            payload, content_type, member_error = read_and_validate_member(
                archive_payload, member, settings
            )
            if member_error:
                rejected_count += 1
                repository.add_batch_member(
                    batch["id"],
                    ordinal=original_ordinals[member["archive_path"]],
                    archive_path=member["archive_path"],
                    file_name=member["file_name"],
                    file_kind=member["file_kind"],
                    size_bytes=member["size_bytes"],
                    member_status="rejected",
                    reason=member_error,
                )
                continue
            member_sha256 = hashlib.sha256(payload).hexdigest()
            if member_sha256 in seen_hashes:
                duplicate_count += 1
                repository.add_batch_member(
                    batch["id"],
                    ordinal=original_ordinals[member["archive_path"]],
                    archive_path=member["archive_path"],
                    file_name=member["file_name"],
                    file_kind=member["file_kind"],
                    size_bytes=member["size_bytes"],
                    sha256=member_sha256,
                    member_status="duplicate_in_archive",
                    reason="Isi identik dengan dokumen lain yang sudah dipilih dari ZIP ini.",
                )
                continue
            seen_hashes.add(member_sha256)
            job = manager.enqueue(
                file_name=member["file_name"],
                content_type=content_type,
                payload=payload,
                analysis_mode=mode,
                force=force,
                external_ai_allowed=not local_only,
            )
            enqueued_count += 1
            repository.add_batch_member(
                batch["id"],
                ordinal=original_ordinals[member["archive_path"]],
                archive_path=member["archive_path"],
                file_name=member["file_name"],
                file_kind=member["file_kind"],
                size_bytes=member["size_bytes"],
                sha256=member_sha256,
                member_status=("deduplicated" if job.get("deduplicated") else "queued"),
                job_id=job["id"],
            )

        for member in members:
            if member["archive_path"] in handled_paths:
                continue
            member_status = "unsupported" if not member["supported"] else "not_selected"
            reason = (
                "Metadata sistem diabaikan."
                if member.get("ignored_metadata")
                else "Format file belum didukung pipeline V2."
                if not member["supported"]
                else f"Tidak dipilih karena batas review {review_limit} dokumen sudah terpenuhi."
            )
            repository.add_batch_member(
                batch["id"],
                ordinal=original_ordinals[member["archive_path"]],
                archive_path=member["archive_path"],
                file_name=member["file_name"],
                file_kind=member["file_kind"],
                size_bytes=member["size_bytes"],
                member_status=member_status,
                reason=reason,
            )

        skipped_count = len(members) - enqueued_count - rejected_count - duplicate_count
        error_message = None
        if not enqueued_count:
            error_message = "Tidak ada dokumen valid yang dapat dimasukkan ke antrean analisis."
        batch = repository.finalize_batch_intake(
            batch["id"],
            selected_count=selected_count,
            enqueued_count=enqueued_count,
            rejected_count=rejected_count,
            skipped_count=max(0, skipped_count),
            duplicate_count=duplicate_count,
            error_message=error_message,
        )
        return {"batch": batch, "deduplicated": False, "review_url": "#/guided-review"}

    @router.get("/batch-intakes/recent")
    def recent_batch_intakes(request: Request, limit: int = 10) -> dict:
        require_analysis_access(request)
        batches = AnalysisRepository(db).list_recent_batch_intakes(limit)
        return {"batches": batches, "count": len(batches)}

    @router.get("/batch-intakes/{batch_id}")
    def get_batch_intake(batch_id: str, request: Request) -> dict:
        require_analysis_access(request)
        batch = AnalysisRepository(db).describe_batch_intake(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch intake tidak ditemukan.")
        return {"batch": batch, "review_url": "#/guided-review"}

    @router.post("/batch-intakes/{batch_id}/cancel")
    def cancel_batch_intake(batch_id: str, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        batch = repository.describe_batch_intake(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch intake tidak ditemukan.")
        cancelled = 0
        for job_id in repository.list_batch_job_ids(batch_id):
            job = manager.cancel(job_id)
            if job and job.get("status") in {"cancelled", "cancel_requested"}:
                cancelled += 1
        return {
            "batch": repository.describe_batch_intake(batch_id),
            "cancelled_jobs": cancelled,
        }

    @router.get("/jobs/{job_id}")
    def analysis_job(job_id: str, request: Request, include_result: bool = True) -> dict:
        require_analysis_access(request)
        result = manager.describe(job_id, include_result=include_result)
        if not result:
            raise HTTPException(status_code=404, detail="Job analisis tidak ditemukan.")
        return result

    @router.post("/jobs/{job_id}/cancel")
    def cancel_analysis_job(job_id: str, request: Request) -> dict:
        require_analysis_access(request)
        job = manager.cancel(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job analisis tidak ditemukan.")
        return {"job": job}

    @router.get("/metrics")
    def analysis_metrics() -> dict:
        settings = get_settings()
        repository = AnalysisRepository(db, settings=settings)
        metrics = repository.operational_metrics()
        return {
            "pipeline_version": PIPELINE_VERSION,
            "worker": manager.status(),
            "metrics": metrics,
            "alerting": OperationalAlertEngine(
                cost_alert_usd_per_hour=settings.analysis_cost_alert_usd_per_hour
            ).evaluate(metrics),
        }

    @router.get("/metrics/prometheus", response_class=Response)
    def analysis_prometheus_metrics() -> Response:
        settings = get_settings()
        if not settings.analysis_prometheus_metrics_enabled:
            raise HTTPException(status_code=404, detail="Prometheus metrics dinonaktifkan.")
        repository = AnalysisRepository(db)
        metrics = repository.operational_metrics()
        alerting = OperationalAlertEngine(
            cost_alert_usd_per_hour=settings.analysis_cost_alert_usd_per_hour
        ).evaluate(metrics)
        payload = render_prometheus_metrics(
            metrics,
            alerting,
            manager.status(),
            pipeline_version=PIPELINE_VERSION,
            cost_alert_usd_per_hour=settings.analysis_cost_alert_usd_per_hour,
            storage_encryption_attestation=storage_encryption_attestation_status(
                settings
            ),
        )
        return Response(content=payload, media_type=PROMETHEUS_CONTENT_TYPE)

    @router.get("/rule-catalog")
    def rule_catalog(
        kk_id: str | None = None,
        kode: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        repository = AnalysisRepository(db)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        if kk_id:
            catalog = [item for item in catalog if item["kk_id"] == kk_id]
        if kode:
            catalog = [item for item in catalog if item["kode"] == kode]
        total = len(catalog)
        safe_offset = max(0, offset)
        safe_limit = max(1, min(1000, limit))
        return {
            "rule_version": RULE_VERSION,
            "rule_count": total,
            "approved_count": sum(item["approval_status"] == "approved" for item in catalog),
            "offset": safe_offset,
            "limit": safe_limit,
            "rules": catalog[safe_offset:safe_offset + safe_limit],
        }

    @router.post("/rule-approvals", status_code=status.HTTP_201_CREATED)
    def approve_rule(payload: RuleApprovalRequest, request: Request) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        rule = next(
            (
                item for item in catalog
                if item["kk_id"] == payload.kk_id
                and item["kode"] == payload.kode
                and item["detail_kode"] == payload.detail_kode
                and item["grade"] == payload.grade
            ),
            None,
        )
        if not rule:
            raise HTTPException(status_code=404, detail="Rule parameter-grade tidak ditemukan.")
        if rule["rule_checksum"] != payload.rule_checksum:
            raise HTTPException(
                status_code=409,
                detail="Checksum rule berubah; muat ulang katalog dan review versi terbaru.",
            )
        approval = repository.save_rule_approval(
            {
                **payload.model_dump(),
                "reviewer_id": authorize_api_request(
                    request, get_settings(), payload.reviewer_id
                ),
                "rule_version": RULE_VERSION,
                "rule_definition": rule["rule_definition"],
            }
        )
        return {"approval": approval}

    @router.get("/governance/rules")
    def governance_rules(
        request: Request,
        q: str = "",
        review_status: str = "all",
        offset: int = 0,
        limit: int = 200,
    ) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        groups = _group_rule_catalog(catalog)
        all_groups = list(groups)
        needle_tokens = [token for token in str(q or "").lower().split() if token]
        if needle_tokens:
            groups = [
                item for item in groups
                if all(token in item["search_text"] for token in needle_tokens)
            ]
        if review_status != "all":
            if review_status not in {"pending", "partial", "approved", "rejected"}:
                raise HTTPException(status_code=422, detail="Filter status governance tidak valid.")
            groups = [item for item in groups if item["review_state"] == review_status]
        safe_offset = max(0, offset)
        safe_limit = max(1, min(500, limit))
        summary = {
            state: sum(item["review_state"] == state for item in all_groups)
            for state in ("pending", "partial", "approved", "rejected")
        }
        return {
            "rule_version": RULE_VERSION,
            "parameter_count": len(all_groups),
            "rule_count": len(catalog),
            "approved_rule_count": sum(item["approval_status"] == "approved" for item in catalog),
            "summary": summary,
            "offset": safe_offset,
            "limit": safe_limit,
            "filtered_count": len(groups),
            "items": [
                {key: value for key, value in item.items() if key != "search_text"}
                for item in groups[safe_offset:safe_offset + safe_limit]
            ],
        }

    @router.get("/governance/rules/history")
    def governance_rule_history(
        request: Request,
        kk_id: str | None = None,
        kode: str | None = None,
        detail_kode: str | None = None,
        limit: int = 200,
    ) -> dict:
        require_governance_access(request)
        events = AnalysisRepository(db).list_rule_approval_events(
            kk_id=kk_id,
            kode=kode,
            detail_kode=detail_kode,
            limit=limit,
        )
        return {"events": events, "count": len(events)}

    @router.post("/governance/rules/decisions", status_code=status.HTTP_201_CREATED)
    def governance_rule_decisions(
        payload: GovernanceRuleDecisionRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        if not payload.attested:
            raise HTTPException(
                status_code=409,
                detail="Pernyataan bahwa rule telah diperiksa wajib dicentang.",
            )
        settings = get_settings()
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        repository = AnalysisRepository(db)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        by_key = {
            (item["kk_id"], item["kode"], item["detail_kode"], item["grade"]): item
            for item in catalog
        }
        seen: set[tuple[str, str, str, str]] = set()
        approvals = []
        for decision in payload.decisions:
            key = (decision.kk_id, decision.kode, decision.detail_kode, decision.grade)
            if key in seen:
                raise HTTPException(status_code=422, detail=f"Rule {key} dikirim lebih dari sekali.")
            seen.add(key)
            rule = by_key.get(key)
            if not rule:
                raise HTTPException(status_code=404, detail=f"Rule {key} tidak ditemukan.")
            if rule["rule_checksum"] != decision.rule_checksum:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Checksum rule {decision.detail_kode}/Grade {decision.grade} berubah; "
                        "muat ulang sebelum menyimpan."
                    ),
                )
            approvals.append({
                **decision.model_dump(),
                "reviewer_id": reviewer_id,
                "reason": payload.reason.strip(),
                "rule_version": RULE_VERSION,
                "rule_definition": rule["rule_definition"],
            })
        saved = repository.save_rule_approval_batch(approvals)
        refreshed = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        return {
            "approvals": saved,
            "saved_count": len(saved),
            "approved_rule_count": sum(item["approval_status"] == "approved" for item in refreshed),
            "total_rule_count": len(refreshed),
            "decision_authority": "domain_owner",
        }

    @router.get("/governance/expert-dataset")
    def governance_expert_dataset(request: Request) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        items = repository.list_expert_dataset_items()
        return {
            "summary": repository.expert_dataset_summary(),
            "retrieval_feedback": repository.retrieval_feedback_summary(),
            "candidates": [
                item for item in items
                if item.get("dataset_status") == "expert_candidate"
            ],
            "gold": [
                item for item in items
                if item.get("dataset_status") == "expert_gold"
            ],
            "two_person_review_required": True,
        }

    @router.post(
        "/governance/expert-dataset/{run_id}/decision",
        status_code=status.HTTP_201_CREATED,
    )
    def governance_expert_dataset_decision(
        run_id: int,
        payload: ExpertDatasetDecisionRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        if not payload.attested:
            raise HTTPException(
                status_code=409,
                detail="Pernyataan pemeriksaan dataset ahli wajib dicentang.",
            )
        settings = get_settings()
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        label = repository.active_expert_review_label(run_id)
        if not run or not label:
            raise HTTPException(status_code=404, detail="Kandidat label aktif tidak ditemukan.")
        if label.get("dataset_status") != "expert_candidate":
            raise HTTPException(
                status_code=409,
                detail="Hanya label expert_candidate aktif yang dapat diperiksa pada tahap ini.",
            )
        if str(label.get("reviewer_id") or "").strip().casefold() == reviewer_id.strip().casefold():
            raise HTTPException(
                status_code=409,
                detail="Reviewer pengesahan harus berbeda dari pembuat kandidat label.",
            )
        if payload.decision == "approve":
            if payload.dataset_partition == "learning" and label.get("outcome") not in {
                "confirmed", "corrected"
            }:
                raise HTTPException(
                    status_code=409,
                    detail="Partisi Learning hanya menerima kasus positif confirmed/corrected.",
                )
            opposite_partition = (
                "learning" if payload.dataset_partition == "evaluation" else "evaluation"
            )
            duplicate_partition = next(
                (
                    item for item in repository.list_expert_dataset_items()
                    if item.get("dataset_status") == "expert_gold"
                    and (item.get("dataset_partition") or "evaluation") == opposite_partition
                    and str(item.get("sha256") or "") == str(run.get("sha256") or "")
                    and int(item.get("run_id") or 0) != run_id
                ),
                None,
            )
            if duplicate_partition:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Dokumen yang sama sudah menjadi expert gold pada partisi "
                        f"{opposite_partition}; partisi evaluasi dan learning tidak boleh overlap."
                    ),
                )
            if label.get("outcome") == "unsure":
                raise HTTPException(status_code=409, detail="Label yang belum yakin tidak dapat disahkan.")
            if label.get("outcome") in {"confirmed", "corrected"} and (
                not label.get("expected_mappings")
                or not label.get("expected_source_locations")
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Mapping dan lokasi sumber wajib lengkap sebelum menjadi expert gold.",
                )
            if label.get("outcome") in {"confirmed", "corrected"} and any(
                str(item.get("evidence_role") or "")
                not in {"primary", "supporting", "context", "contradictory"}
                for item in (label.get("expected_mappings") or [])
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Peran evidence wajib diperiksa ulang sebelum label menjadi expert gold."
                    ),
                )
            if str(label.get("expected_template_status") or "not_assessed") not in {
                "template_only", "substantive"
            }:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Status template dokumen wajib diperiksa ulang sebelum label "
                        "menjadi expert gold."
                    ),
                )
            next_label = {
                "reviewer_id": reviewer_id,
                "outcome": label["outcome"],
                "selected_mapping_candidate_id": label.get("selected_mapping_candidate_id"),
                "selected_fact_ids": label.get("selected_fact_ids") or [],
                "expected_mappings": label.get("expected_mappings") or [],
                "expected_source_locations": label.get("expected_source_locations") or [],
                "reason": f"Pengesahan domain owner: {payload.reason.strip()}",
                "dataset_status": "expert_gold",
                "dataset_partition": payload.dataset_partition,
                "expected_template_status": label.get("expected_template_status"),
            }
        else:
            next_label = {
                "reviewer_id": reviewer_id,
                "outcome": "unsure",
                "selected_mapping_candidate_id": None,
                "selected_fact_ids": [],
                "expected_mappings": [],
                "expected_source_locations": [],
                "reason": f"Dikembalikan oleh domain owner: {payload.reason.strip()}",
                "dataset_status": "pilot_unlabelled",
                "dataset_partition": "evaluation",
                "expected_template_status": "not_assessed",
            }
        saved = repository.save_expert_review_label(run_id, next_label)
        feedback = RetrievalFeedbackLearningEngine().refresh_fail_closed(repository)
        repository.add_event(
            run_id,
            event_type="expert_dataset_decision_saved",
            stage="evaluation_learning",
            progress=100,
            message=(
                "Kandidat disahkan menjadi expert gold."
                if payload.decision == "approve"
                else "Kandidat dikembalikan untuk diperbaiki."
            ),
            payload={
                "label_id": saved["id"],
                "decision": payload.decision,
                "dataset_status": saved["dataset_status"],
                "dataset_partition": saved["dataset_partition"],
                "retrieval_feedback_registry_sha256": feedback.get("registry_sha256"),
                "retrieval_feedback_term_count": int(feedback.get("term_count") or 0),
                "retrieval_feedback_active": bool(feedback.get("active", feedback.get("is_active"))),
            },
        )
        return {
            "label": saved,
            "summary": repository.expert_dataset_summary(),
            "retrieval_feedback": feedback,
            "two_person_review_enforced": True,
        }

    @router.get("/governance/vision")
    def governance_vision(request: Request) -> dict:
        require_governance_access(request)
        settings = get_settings()
        repository = AnalysisRepository(db)
        governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        return {
            "governance": governance,
            "local_ocr": local_ocr_runtime_status(settings),
            "recent_probes": repository.list_vision_capability_probes(10),
            "decision_history": repository.list_vision_governance_decisions(50),
            "probe_uses_synthetic_image_only": True,
        }

    @router.post("/governance/vision/probe", status_code=status.HTTP_201_CREATED)
    async def governance_vision_probe(
        payload: VisionProbeRequest,
        request: Request,
    ) -> dict:
        settings = get_settings()
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        report = await asyncio.to_thread(run_synthetic_vision_probe, settings)
        probe = AnalysisRepository(db).save_vision_capability_probe(report, reviewer_id)
        return {
            "probe": probe,
            "synthetic_only": True,
            "user_document_sent": False,
        }

    @router.post("/governance/vision/decisions", status_code=status.HTTP_201_CREATED)
    def governance_vision_decision(
        payload: VisionGovernanceDecisionRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        if not payload.attested:
            raise HTTPException(
                status_code=409,
                detail="Pernyataan governance wajib dicentang sebelum keputusan disimpan.",
            )
        settings = get_settings()
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        repository = AnalysisRepository(db)
        provider = settings.ai_provider
        model = settings.deepseek_model
        api_surface = "chat_completions_vision"
        if payload.status == "approved" and payload.scope == "capability_validation":
            probes = repository.list_vision_capability_probes(200)
            valid_probe = next(
                (
                    item for item in probes
                    if item.get("status") == "passed"
                    and item.get("report_sha256") == payload.evidence_sha256
                    and item.get("provider") == provider
                    and item.get("model") == model
                    and item.get("api_surface") == api_surface
                ),
                None,
            )
            if not valid_probe:
                raise HTTPException(
                    status_code=409,
                    detail="Capability hanya dapat disetujui dari uji synthetic yang lulus pada provider/model aktif.",
                )
        if payload.status == "approved" and payload.scope == "external_data_processing":
            current = repository.vision_governance_status(
                settings,
                renderer_available=bool(shutil.which("pdftoppm")),
            )
            if not current["checks"]["capability_approved"]:
                raise HTTPException(
                    status_code=409,
                    detail="Setujui capability vision yang lulus uji sebelum consent pemrosesan data.",
                )
            if payload.sensitivity_scope != "restricted":
                raise HTTPException(
                    status_code=409,
                    detail="Korpus saat ini restricted; consent harus secara eksplisit mencakup restricted.",
                )
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        decision = repository.save_vision_governance_decision({
            **payload.model_dump(exclude={"attested", "expires_in_days"}),
            "provider": provider,
            "model": model,
            "api_surface": api_surface,
            "policy_version": VISION_POLICY_VERSION,
            "reviewer_id": reviewer_id,
            "reason": payload.reason.strip(),
            "expires_at": expires_at,
        })
        governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        return {"decision": decision, "governance": governance}

    @router.get("/shadow-comparison")
    def shadow_comparison(request: Request, legacy_review_id: int, run_id: int) -> dict:
        require_governance_access(request)
        legacy_candidates = legacy_review_candidates(db, legacy_review_id)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if legacy_candidates is None or not run:
            raise HTTPException(status_code=404, detail="Legacy review atau V2 run tidak ditemukan.")
        comparison, report_sha256 = build_shadow_comparison(
            legacy_review_id=legacy_review_id,
            v2_run_id=run_id,
            legacy_candidates=legacy_candidates,
            v2_candidates=repository.list_mapping_candidates(run_id),
        )
        return {**comparison, "report_sha256": report_sha256}

    @router.post("/shadow-comparisons/refresh")
    def refresh_shadow_comparisons(request: Request, limit: int = 500) -> dict:
        require_governance_access(request)
        service = ShadowComparisonService(db)
        pairs = service.refresh_all(limit=limit)
        return {"pairs": pairs, "report": service.report(limit=limit)}

    @router.get("/shadow-comparison-report")
    def shadow_comparison_report(request: Request, limit: int = 500) -> dict:
        require_governance_access(request)
        service = ShadowComparisonService(db)
        return {
            "report": service.report(limit=limit),
            "pairs": service.repository.list_shadow_pairs(limit=limit),
        }

    @router.post("/evaluation-reports", status_code=status.HTTP_201_CREATED)
    def register_evaluation_report(payload: EvaluationReportRequest, request: Request) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        report = repository.save_evaluation_report(
            {
                "pipeline_version": PIPELINE_VERSION,
                "dataset_name": payload.dataset_name.strip(),
                "dataset_status": payload.dataset_status,
                "case_count": payload.case_count,
                "metrics": {
                    "retrieval_recall_at_5": payload.retrieval_recall_at_5,
                    "source_accuracy": payload.source_accuracy,
                    "overgrade_rate": payload.overgrade_rate,
                    "grade_label_coverage": payload.grade_label_coverage,
                    "grade_assessment_coverage": payload.grade_assessment_coverage,
                },
                "report_sha256": payload.report_sha256,
                "dataset_sha256": payload.dataset_sha256,
                "generation_method": "manual_import",
                "release_authority": False,
                "details": {},
                "reviewer_id": authorize_api_request(
                    request, get_settings(), payload.reviewer_id
                ),
                "notes": payload.notes.strip(),
            }
        )
        return {"report": report}

    @router.get("/evaluation-reports")
    def evaluation_reports(request: Request) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        reports = repository.list_evaluation_reports(PIPELINE_VERSION)
        return {
            "reports": reports,
            "count": len(reports),
            "dataset_summary": repository.expert_dataset_summary(),
        }

    @router.post(
        "/evaluation-reports/from-expert-gold",
        status_code=status.HTTP_201_CREATED,
    )
    def generate_evaluation_report(
        payload: GeneratedEvaluationReportRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        if not payload.attested:
            raise HTTPException(
                status_code=409,
                detail="Pernyataan pemeriksaan evaluation report wajib dicentang.",
            )
        repository = AnalysisRepository(db)
        dataset_before_evaluation = repository.expert_dataset_summary()
        if int(dataset_before_evaluation.get("partition_overlap_count") or 0):
            raise HTTPException(
                status_code=409,
                detail="Dataset Evaluasi dan Learning overlap; perbaiki partisi sebelum membuat report.",
            )
        evaluation = build_expert_gold_evaluation(repository)
        dataset_summary = evaluation["dataset_summary"]
        if int(dataset_summary.get("expert_gold_case_count") or 0) == 0:
            raise HTTPException(
                status_code=409,
                detail="Belum ada expert gold yang dapat dievaluasi.",
            )
        reviewer_id = authorize_api_request(
            request, get_settings(), payload.reviewer_id
        )
        report = repository.save_evaluation_report({
            "pipeline_version": PIPELINE_VERSION,
            "dataset_name": payload.dataset_name.strip(),
            "dataset_status": "expert_gold",
            "case_count": int(dataset_summary["expert_gold_case_count"]),
            "metrics": evaluation["metrics"],
            "report_sha256": evaluation["report_sha256"],
            "dataset_sha256": dataset_summary["dataset_sha256"],
            "generation_method": SERVER_DERIVED_GENERATION_METHOD,
            "release_authority": True,
            "details": {
                "counters": evaluation["counters"],
                **evaluation["details"],
            },
            "reviewer_id": reviewer_id,
            "notes": payload.notes.strip(),
        })
        if not report.get("release_authority"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Nama/report checksum sudah tercatat sebagai report informasional. "
                    "Gunakan nama dataset lain untuk report server-derived."
                ),
            )
        return {
            "report": report,
            "dataset_summary": dataset_summary,
            "automatic_checksums": True,
            "document_content_included": False,
        }

    @router.get("/release-evidence")
    def release_evidence(request: Request, limit: int = 200) -> dict:
        require_governance_access(request)
        repository = AnalysisRepository(db)
        reports = repository.list_evaluation_reports(PIPELINE_VERSION)
        return {
            "events": repository.list_release_events(limit=limit),
            "summary": repository.release_evidence_summary(),
            "evaluation_reports": reports,
            "dataset_summary": repository.expert_dataset_summary(),
            "promotion": current_promotion_snapshot(repository, get_settings(), reports),
            "shadow_comparison": ShadowComparisonService(db, repository).report(),
            "automatic_checksum_derivation": True,
        }

    @router.post("/release-evidence", status_code=status.HTTP_201_CREATED)
    def register_release_evidence(
        payload: ReleaseEvidenceRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        settings = get_settings()
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        if not payload.attested:
            raise HTTPException(status_code=409, detail="Attestation release evidence wajib dicentang.")
        repository = AnalysisRepository(db)
        try:
            evidence_size = len(json.dumps(payload.evidence, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Evidence metadata harus berupa JSON valid.")
        if evidence_size > 10_000:
            raise HTTPException(status_code=413, detail="Evidence metadata maksimal 10 KB.")
        evaluation_report = None
        if payload.evaluation_report_id is not None:
            evaluation_report = repository.get_evaluation_report(payload.evaluation_report_id)
            if not evaluation_report:
                raise HTTPException(status_code=404, detail="Evaluation report tidak ditemukan.")
            if (
                evaluation_report.get("pipeline_version") != PIPELINE_VERSION
                or evaluation_report.get("dataset_status") != "expert_gold"
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Release evidence hanya boleh merujuk expert_gold report pada pipeline aktif.",
                )
            if (
                not evaluation_report.get("release_authority")
                or evaluation_report.get("generation_method")
                != SERVER_DERIVED_GENERATION_METHOD
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Evaluation report manual/legacy hanya informasional; keputusan rilis "
                        "memerlukan report server-derived partition-aware."
                    ),
                )
        dataset_summary = repository.expert_dataset_summary()
        shadow_report = ShadowComparisonService(db, repository).report()
        derived_dataset_sha256 = payload.dataset_sha256
        derived_comparison_sha256 = payload.comparison_report_sha256
        gate_snapshot: dict = {"validated": False}
        if payload.decision == "passed":
            if not evaluation_report:
                raise HTTPException(
                    status_code=409,
                    detail="Keputusan passed memerlukan expert-gold evaluation report.",
                )
            current_dataset_sha256 = dataset_summary.get("dataset_sha256")
            report_dataset_sha256 = evaluation_report.get("dataset_sha256")
            current_case_count = int(dataset_summary.get("expert_gold_case_count") or 0)
            recomputed_evaluation = build_expert_gold_evaluation(repository)
            if (
                not current_dataset_sha256
                or not report_dataset_sha256
                or report_dataset_sha256 != current_dataset_sha256
                or int(evaluation_report.get("case_count") or 0) != current_case_count
                or evaluation_report.get("report_sha256")
                != recomputed_evaluation.get("report_sha256")
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Evaluation report tidak cocok dengan dataset expert gold aktif. "
                        "Buat ulang report dari tab Bukti Rilis."
                    ),
                )
            if payload.dataset_sha256 and payload.dataset_sha256 != current_dataset_sha256:
                raise HTTPException(status_code=409, detail="Checksum dataset yang dikirim sudah stale.")
            if (
                payload.comparison_report_sha256
                and payload.comparison_report_sha256 != shadow_report.get("report_sha256")
            ):
                raise HTTPException(status_code=409, detail="Checksum comparison report tidak cocok.")
            if not shadow_report.get("review_target_reached"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Keputusan passed memerlukan minimal 50 shadow comparison terminal; "
                        f"baru {int(shadow_report.get('completed_count') or 0)}/50."
                    ),
                )
            derived_dataset_sha256 = current_dataset_sha256
            derived_comparison_sha256 = shadow_report["report_sha256"]
            promotion = current_promotion_snapshot(repository, settings, [evaluation_report])
            gate_snapshot = {
                "validated": True,
                "dataset_case_count": current_case_count,
                "dataset_sha256": current_dataset_sha256,
                "evaluation_report_id": int(evaluation_report["id"]),
                "evaluation_report_sha256": evaluation_report["report_sha256"],
                "evaluation_release_authority": True,
                "evaluation_recomputed_sha256": recomputed_evaluation["report_sha256"],
                "shadow_comparison_count": int(shadow_report["completed_count"]),
                "shadow_comparison_report_sha256": shadow_report["report_sha256"],
                "shadow_top_1_match_rate": shadow_report.get("top_1_match_rate"),
                "shadow_exact_set_match_rate": shadow_report.get("exact_set_match_rate"),
                "shadow_ready": bool(promotion["shadow"]["ready"]),
                "canary_ready": bool(promotion["canary"]["ready"]),
                "approved_rule_count": int(promotion["approved_rule_count"]),
                "total_rule_count": int(promotion["total_rule_count"]),
                "high_security_findings": int(promotion["high_security_findings"]),
            }
            required_gate = "shadow" if payload.stage in {"shadow", "pilot"} else "canary"
            if not bool((promotion.get(required_gate) or {}).get("ready")):
                reasons = (promotion.get(required_gate) or {}).get("reasons") or []
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Keputusan passed untuk {payload.stage} belum diizinkan: "
                        + ("; ".join(reasons) if reasons else "promotion gate belum siap.")
                    ),
                )
            if payload.stage == "general" and current_case_count < 200:
                raise HTTPException(
                    status_code=409,
                    detail="General release passed memerlukan minimal 200 expert-gold cases.",
                )
        if payload.stable_cycle and (
            payload.decision != "passed"
            or payload.stage not in {"canary", "general"}
            or payload.critical_incident_count != 0
            or not payload.rollback_rehearsed
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Stable cycle hanya sah untuk canary/general yang passed, tanpa critical incident, "
                    "dan rollback sudah direhearsal."
                ),
            )
        if payload.stable_cycle:
            if not gate_snapshot.get("canary_ready"):
                raise HTTPException(
                    status_code=409,
                    detail="Stable cycle memerlukan seluruh expert, rule, security, dan OCR gate canary.",
                )
            if payload.stage == "general" and int(gate_snapshot["dataset_case_count"]) < 200:
                raise HTTPException(
                    status_code=409,
                    detail="Stable general release memerlukan minimal 200 expert-gold cases.",
                )
        event_evidence = {
            **payload.evidence,
            "gate_snapshot": gate_snapshot,
            "checksum_derivation": (
                "server_derived_v2_partitioned_recomputed"
                if payload.decision == "passed" else "none"
            ),
        }
        event = repository.save_release_event({
            **payload.model_dump(
                exclude={
                    "attested", "reviewer_id", "dataset_sha256",
                    "comparison_report_sha256", "evidence",
                }
            ),
            "pipeline_version": PIPELINE_VERSION,
            "rule_version": RULE_VERSION,
            "model": settings.deepseek_model,
            "dataset_sha256": derived_dataset_sha256,
            "comparison_report_sha256": derived_comparison_sha256,
            "evidence": event_evidence,
            "reviewer_id": reviewer_id,
            "reason": payload.reason.strip(),
        })
        return {"event": event, "summary": repository.release_evidence_summary()}

    @router.get("/promotion-readiness")
    def promotion_readiness() -> dict:
        settings = get_settings()
        repository = AnalysisRepository(db)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        metrics = repository.operational_metrics()
        security = metrics.get("security_findings_by_severity") or {}
        vision_governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        local_ocr = local_ocr_runtime_status(settings)
        return EvaluationLearningEngine().promotion_readiness(
            repository.list_evaluation_reports(PIPELINE_VERSION),
            approved_rule_count=sum(item["approval_status"] == "approved" for item in catalog),
            total_rule_count=len(catalog),
            high_security_findings=int(security.get("high") or 0) + int(security.get("critical") or 0),
            vision_required=int(metrics.get("ocr_run_count") or 0) > 0,
            vision_ready=bool(local_ocr["available"] or vision_governance["effective"]),
            storage_ready=bool(storage_encryption_attestation_status(settings)["effective"]),
        )

    @router.get("/readiness-dashboard")
    def readiness_dashboard() -> dict:
        settings = get_settings()
        repository = AnalysisRepository(db, settings=settings)
        catalog = build_rule_catalog(repository.parameter_index(), repository.list_rule_approvals())
        metrics = repository.operational_metrics()
        alerting = OperationalAlertEngine(
            cost_alert_usd_per_hour=settings.analysis_cost_alert_usd_per_hour
        ).evaluate(metrics)
        security = metrics.get("security_findings_by_severity") or {}
        vision_governance = repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        local_ocr = local_ocr_runtime_status(settings)
        office_renderer = office_slide_renderer_status()
        promotion = EvaluationLearningEngine().promotion_readiness(
            repository.list_evaluation_reports(PIPELINE_VERSION),
            approved_rule_count=sum(item["approval_status"] == "approved" for item in catalog),
            total_rule_count=len(catalog),
            high_security_findings=int(security.get("high") or 0) + int(security.get("critical") or 0),
            vision_required=int(metrics.get("ocr_run_count") or 0) > 0,
            vision_ready=bool(local_ocr["available"] or vision_governance["effective"]),
            storage_ready=bool(storage_encryption_attestation_status(settings)["effective"]),
        )
        release_evidence = repository.release_evidence_summary()
        shadow_report = ShadowComparisonService(db, repository).report()
        rollout = RolloutGuardEngine().evaluate(
            requested_stage=settings.analysis_rollout_stage,
            canary_percentage=settings.analysis_canary_percentage,
            stable_release_cycles=release_evidence["stable_release_cycle_count"],
            promotion=promotion,
        )
        worker = manager.status()
        office_renderer_payload = {
            **office_renderer,
            "enabled": settings.analysis_office_rendering_enabled,
            "effective": bool(
                settings.analysis_office_rendering_enabled
                and office_renderer["available"]
            ),
            "max_pages_per_full_audit": max(
                1, min(1_000, settings.analysis_office_render_max_pages)
            ),
            "supported_formats": ["docx", "xlsx", "pptx"],
        }
        return {
            "pipeline_version": PIPELINE_VERSION,
            "provider": {
                "name": settings.ai_provider,
                "model": settings.deepseek_model,
                "api_surface": settings.analysis_api_surface,
                "configured": settings.has_ai_key,
            },
            "vision": {
                "feature_enabled": settings.vision_analysis_enabled,
                "provider_validated": settings.analysis_vision_provider_validated,
                "renderer_available": bool(shutil.which("pdftoppm")),
                "effective": vision_governance["effective"],
                "governance": vision_governance,
            },
            "ocr": {
                **local_ocr,
                "external_vision_effective": vision_governance["effective"],
                "effective": bool(local_ocr["available"] or vision_governance["effective"]),
                "processing_order": ["local_ocr", "external_vision_fallback"],
            },
            "office_renderer": office_renderer_payload,
            "office_slide_renderer": office_renderer_payload,
            "checkpointing": {
                "policy_version": CHECKPOINT_POLICY_VERSION,
                "visual_ocr_batch_durable": True,
                "partial_resume_checksum_bound": True,
            },
            "rule_review": {
                "approved": promotion["approved_rule_count"],
                "total": promotion["total_rule_count"],
                "provisional_preview_only": promotion["approved_rule_count"] < promotion["total_rule_count"],
            },
            "promotion": promotion,
            "rollout": rollout,
            "release_evidence": release_evidence,
            "shadow_comparison": shadow_report,
            "retrieval_feedback": repository.retrieval_feedback_summary(),
            "deployment": {
                "worker": worker,
                "prometheus_metrics_enabled": settings.analysis_prometheus_metrics_enabled,
                "prometheus_path": "/api/analysis-runs/metrics/prometheus",
                "multi_instance_ready": bool(worker.get("multi_instance_supported")),
                "reviewer_identity": {
                    "required": settings.analysis_require_reviewer_identity,
                    "trusted_header": settings.analysis_reviewer_identity_header,
                    "role_required": settings.analysis_require_reviewer_role,
                    "trusted_role_header": settings.analysis_reviewer_role_header,
                    "authorization_mode": (
                        "trusted_role_rbac"
                        if settings.analysis_require_reviewer_role
                        else "identity_only"
                        if settings.analysis_require_reviewer_identity
                        else "development_payload_identity"
                    ),
                    "proxy_reference": "ops/reverse-proxy/nginx.conf",
                    "direct_backend_access_must_be_blocked": True,
                },
                "authorization_contract": authorization_contract_summary(),
                "alertmanager": {
                    "default_delivery_enabled": False,
                    "webhook_profile": "ops/alertmanager/alertmanager.webhook.yml",
                    "secret_file_required": True,
                },
                "payload_storage": repository.payload_storage_status(),
            },
            "metrics": metrics,
            "alerting": alerting,
            "temporary_mitigations": [
                {
                    "gate": "domain_rules",
                    "status": "ready" if promotion["approved_rule_count"] == promotion["total_rule_count"] else "mitigated",
                    "action": "Rule draft tetap preview-only; checksum review tersedia di rule catalog.",
                },
                {
                    "gate": "expert_dataset",
                    "status": "ready" if promotion["shadow"]["ready"] else "mitigated",
                    "action": "Bootstrap regression boleh dipakai untuk CI tetapi tidak dihitung sebagai expert gold.",
                },
                {
                    "gate": "vision_ocr",
                    "status": (
                        "ready"
                        if local_ocr["available"] or vision_governance["effective"]
                        else "mitigated"
                    ),
                    "action": (
                        "Local OCR diprioritaskan tanpa transfer data. Vision eksternal hanya fallback "
                        "setelah uji synthetic, capability approval, consent, dan konfigurasi valid."
                    ),
                },
                {
                    "gate": "shadow_comparison",
                    "status": "ready" if shadow_report["review_target_reached"] else "mitigated",
                    "action": (
                        f"Shadow ledger otomatis baru {shadow_report['completed_count']}/50 pasangan terminal; "
                        "metrik agreement tidak menggantikan expert-gold quality evaluation."
                    ),
                },
                {
                    "gate": "production_alerts",
                    "status": "mitigated" if alerting["status"] != "critical" else "blocked",
                    "action": (
                        "Prometheus endpoint dan API alert aktif; deployment Prometheus/Alertmanager "
                        "eksternal masih harus dikonfigurasi dan dibuktikan."
                    ),
                },
                {
                    "gate": "controlled_upload_reservation",
                    "status": (
                        "blocked"
                        if (
                            metrics["stale_controlled_upload_reservation_count"]
                            or metrics["unresolved_controlled_upload_ambiguity_count"]
                        )
                        else "ready"
                    ),
                    "action": (
                        f"{int((metrics.get('controlled_uploads_by_status') or {}).get('uploading') or 0)} "
                        "reservation aktif; "
                        f"{metrics['stale_controlled_upload_reservation_count']} melewati sepuluh menit. "
                        f"{metrics['unresolved_controlled_upload_ambiguity_count']} ambiguity belum selesai. "
                        "Reservation stale wajib ditangani lewat incident flow; hasil terminal ambigu "
                        "memerlukan dua reviewer yang cocok. Jangan retry otomatis."
                    ),
                },
                {
                    "gate": "multi_instance_queue",
                    "status": "ready" if worker.get("multi_instance_supported") else "mitigated",
                    "action": (
                        (
                            f"Adapter {worker.get('queue_backend') or 'unknown'} siap untuk "
                            "replica ganda dengan canonical shared authority."
                            if worker.get("multi_instance_supported")
                            else (
                                f"Adapter {worker.get('queue_backend') or 'unknown'} "
                                f"{'dikenali' if (worker.get('queue_adapter') or {}).get('adapter_known') else 'tidak dikenal'}, "
                                "tetapi replica >1 tetap diblokir sampai canonical PostgreSQL, "
                                "atomic distributed claim, dan shared payload terbukti."
                            )
                        )
                    ),
                },
                {
                    "gate": "payload_storage",
                    "status": (
                        "ready"
                        if storage_encryption_attestation_status(settings)["effective"]
                        else "blocked"
                    ),
                    "action": (
                        "Payload eksternal content-addressed sudah fail-closed; canary tetap "
                        "ditahan sampai attestation enkripsi volume ber-signature, terikat storage, "
                        "dan belum kedaluwarsa."
                    ),
                },
                {
                    "gate": "canary",
                    "status": "ready" if promotion["canary"]["ready"] else "blocked",
                    "action": "Rollout guard menurunkan stage efektif ke development ketika gate belum lengkap.",
                },
                {
                    "gate": "release_evidence",
                    "status": (
                        "ready" if release_evidence["legacy_deprecation_eligible"] else "mitigated"
                    ),
                    "action": (
                        "Release ledger append-only mencatat checksum, evaluator, insiden, dan rollback. "
                        "Deprecation V1 memerlukan dua stable cycle yang sah."
                    ),
                },
            ],
        }

    @router.get("/guided-review/parameters")
    def guided_review_parameters(request: Request, q: str = "", limit: int = 500) -> dict:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        needle = " ".join(str(q or "").lower().split())
        parameters = repository.parameter_index()
        if needle:
            parameters = [
                item for item in parameters
                if needle in " ".join(
                    str(item.get(field) or "").lower()
                    for field in (
                        "kk_id", "kode", "detail_kode", "uraian",
                        "subunsur_name", "matrix_subunsur_name",
                    )
                )
            ]
        safe_limit = max(1, min(1000, limit))
        return {
            "total": len(parameters),
            "parameters": [
                {
                    "kk_id": item["kk_id"],
                    "kode": item["kode"],
                    "detail_kode": item["detail_kode"],
                    "uraian": item["uraian"],
                    "subunsur_name": item.get("subunsur_name"),
                    "cara_pengujian": item.get("cara_pengujian"),
                }
                for item in parameters[:safe_limit]
            ],
        }

    @router.get("/visual-review/queue")
    def visual_review_queue(
        request: Request,
        review_status: str = "all",
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        require_guided_review_access(request)
        if review_status not in {"all", "pending", "needs_attention", "reviewed"}:
            raise HTTPException(status_code=422, detail="Filter status review visual tidak valid.")
        all_items = AnalysisRepository(db).list_visual_review_items()
        counts = {
            state: sum(item["review_state"] == state for item in all_items)
            for state in ("pending", "needs_attention", "reviewed")
        }
        kind_counts = {
            kind: sum(item["review_kind"] == kind for item in all_items)
            for kind in ("visual_semantics", "ocr_rescue")
        }
        filtered = (
            all_items
            if review_status == "all"
            else [item for item in all_items if item["review_state"] == review_status]
        )
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(500, int(limit)))
        return {
            "total": len(all_items),
            "filtered_total": len(filtered),
            "counts": counts,
            "kind_counts": kind_counts,
            "offset": safe_offset,
            "limit": safe_limit,
            "items": filtered[safe_offset:safe_offset + safe_limit],
        }

    @router.get("/visual-review/{run_id}/{unit_key}/preview")
    def visual_review_preview(run_id: int, unit_key: str, request: Request) -> Response:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        unit = repository.get_document_unit(run_id, unit_key)
        if not run or run.get("pipeline_version") == "legacy" or not unit:
            raise HTTPException(status_code=404, detail="Unit visual V2 tidak ditemukan.")
        visual_review_binding(unit)
        document_payload = load_document_payload(repository, run_id)
        if document_payload is None:
            raise HTTPException(
                status_code=410,
                detail="Payload dokumen sudah dipurge; unggah ulang untuk preview visual.",
            )
        try:
            preview, media_type, preview_name = extract_visual_preview(
                run,
                unit,
                document_payload,
                max_bytes=get_settings().analysis_batch_max_entry_bytes,
                max_compression_ratio=get_settings().analysis_batch_max_compression_ratio,
                timeout_seconds=get_settings().ai_timeout_seconds,
            )
        except VisualPreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        suffix = "." + preview_name.rsplit(".", 1)[-1].lower() if "." in preview_name else ""
        return Response(
            content=preview,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="visual-preview{suffix}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
            },
        )

    @router.get("/visual-review/{run_id}/{unit_key}")
    def visual_review_detail(run_id: int, unit_key: str, request: Request) -> dict:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        unit = repository.get_document_unit(run_id, unit_key)
        if not run or run.get("pipeline_version") == "legacy" or not unit:
            raise HTTPException(status_code=404, detail="Unit visual V2 tidak ditemukan.")
        binding = visual_review_binding(unit)
        history = repository.list_visual_review_decisions(run_id, unit_key)
        latest = history[-1] if history else None
        snapshot = repository.visual_review_snapshot(run_id)
        run_items = [
            item for item in repository.list_visual_review_items()
            if int(item["run_id"]) == run_id
        ]
        return {
            "run": run,
            "unit": unit,
            "review_kind": binding["review_kind"],
            "review_text": binding["review_text"],
            "review_binding": {
                key: binding[key]
                for key in (
                    "unit_text_sha256",
                    "source_image_sha256",
                    "ocr_candidate_text_sha256",
                )
            },
            "latest_decision": latest,
            "decision_history": history,
            "visual_review_snapshot": {
                key: snapshot[key]
                for key in ("source_run_id", "decision_count", "actionable_count", "checksum")
            },
            "run_review_summary": {
                "total": len(run_items),
                "pending": sum(item["review_state"] == "pending" for item in run_items),
                "needs_attention": sum(
                    item["review_state"] == "needs_attention" for item in run_items
                ),
                "reviewed": sum(item["review_state"] == "reviewed" for item in run_items),
            },
            "preview_url": f"/api/analysis-runs/visual-review/{run_id}/{unit_key}/preview",
        }

    @router.post(
        "/visual-review/{run_id}/{unit_key}/decision",
        status_code=status.HTTP_201_CREATED,
    )
    def save_visual_review_decision(
        run_id: int,
        unit_key: str,
        payload: VisualReviewDecisionRequest,
        request: Request,
    ) -> dict:
        require_guided_review_access(request)
        if not payload.attested:
            raise HTTPException(status_code=422, detail="Pernyataan pemeriksaan visual wajib dicentang.")
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        unit = repository.get_document_unit(run_id, unit_key)
        if not run or run.get("pipeline_version") == "legacy" or not unit:
            raise HTTPException(status_code=404, detail="Unit visual V2 tidak ditemukan.")
        metadata = unit.get("metadata") or {}
        binding = visual_review_binding(unit)
        if payload.review_kind != binding["review_kind"]:
            raise HTTPException(
                status_code=409,
                detail="Jenis review berubah; muat ulang sebelum menyimpan keputusan.",
            )
        observed_text_sha256 = binding["unit_text_sha256"]
        observed_image_sha256 = binding["source_image_sha256"]
        if (
            payload.unit_text_sha256 != observed_text_sha256
            or payload.source_image_sha256 != observed_image_sha256
            or payload.ocr_candidate_text_sha256
            != binding["ocr_candidate_text_sha256"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Teks atau gambar sumber berubah; muat ulang sebelum menyimpan keputusan.",
            )
        history = repository.list_visual_review_decisions(run_id, unit_key)
        latest_id = int(history[-1]["id"]) if history else None
        if payload.expected_latest_decision_id != latest_id:
            raise HTTPException(
                status_code=409,
                detail="Keputusan review telah berubah; muat ulang untuk mencegah overwrite.",
            )
        reviewed_text = " ".join(payload.reviewed_text.split())
        semantic_description = " ".join(payload.semantic_description.split())
        reviewed_semantic_regions = []
        for region in payload.semantic_regions:
            item = region.model_dump()
            bbox = item["bbox"]
            if bbox["x"] + bbox["width"] > 1.000001 or bbox["y"] + bbox["height"] > 1.000001:
                raise HTTPException(
                    status_code=422,
                    detail="Bounding box region visual melewati batas gambar.",
                )
            if bbox["width"] * bbox["height"] < 0.0001:
                raise HTTPException(
                    status_code=422,
                    detail="Region visual terlalu kecil untuk diverifikasi.",
                )
            reviewed_semantic_regions.append({
                "region_type": item["region_type"],
                "semantic_hint": item["region_type"],
                "label": " ".join(str(item.get("label") or "").split())[:300],
                "bbox": {
                    key: round(float(bbox[key]), 6)
                    for key in ("x", "y", "width", "height")
                },
                "coordinate_space": "normalized_top_left",
                "detection_method": "human_visual_region_v1",
                "requires_human_confirmation": False,
            })
        if payload.decision == "corrected" and len(reviewed_text) < 8:
            raise HTTPException(
                status_code=422,
                detail="Koreksi memerlukan teks/deskripsi visual minimal 8 karakter.",
            )
        if (
            binding["review_kind"] == "ocr_rescue"
            and not binding["ocr_candidate_text_sha256"]
            and payload.decision == "confirmed"
        ):
            raise HTTPException(
                status_code=422,
                detail="OCR tidak menghasilkan kandidat; pilih Perlu koreksi dan transkripsikan teks dari gambar.",
            )
        if payload.decision in {"confirmed", "corrected"} and len(semantic_description) < 8:
            raise HTTPException(
                status_code=422,
                detail="Konfirmasi visual memerlukan ringkasan makna minimal 8 karakter.",
            )
        if payload.decision == "confirmed":
            reviewed_text = binding["review_text"]
        elif payload.decision in {"not_evidence", "unsure"}:
            reviewed_text = ""
            reviewed_semantic_regions = []
        reviewer_id = authorize_api_request(
            request,
            get_settings(),
            payload.reviewer_id,
        )
        semantic_regions = metadata.get("semantic_regions") or []
        semantic_regions_sha256 = hashlib.sha256(
            json.dumps(
                semantic_regions,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        decision = repository.save_visual_review_decision({
            "run_id": run_id,
            "unit_id": int(unit["id"]),
            "unit_key": unit_key,
            "review_kind": binding["review_kind"],
            "decision": payload.decision,
            "unit_text_sha256": observed_text_sha256,
            "source_image_sha256": observed_image_sha256,
            "reviewed_text": reviewed_text,
            "reviewed_text_sha256": (
                hashlib.sha256(reviewed_text.encode("utf-8")).hexdigest()
                if reviewed_text else None
            ),
            "semantic_description": semantic_description,
            "source_location": unit.get("source_location") or {},
            "evidence": {
                "ocr_method": metadata.get("ocr_method"),
                "ocr_confidence": metadata.get("ocr_confidence"),
                "ocr_region_count": metadata.get("ocr_region_count"),
                "ocr_review_candidate_text_sha256": binding[
                    "ocr_candidate_text_sha256"
                ],
                "ocr_review_candidate_confidence": metadata.get(
                    "ocr_review_candidate_confidence"
                ),
                "semantic_region_count": len(semantic_regions),
                "semantic_regions_sha256": semantic_regions_sha256,
                "reviewed_semantic_regions": reviewed_semantic_regions,
                "pipeline_version": run.get("pipeline_version"),
            },
            "reviewer_id": reviewer_id,
            "reason": payload.reason.strip(),
        })
        snapshot = repository.visual_review_snapshot(run_id)
        repository.add_event(
            run_id,
            event_type="visual_review_decision_saved",
            stage="human_review",
            progress=100,
            message=f"Keputusan review {payload.decision} tersimpan untuk {unit_key}.",
            payload={
                "decision_id": decision["id"],
                "unit_key": unit_key,
                "decision": payload.decision,
                "visual_review_checksum": snapshot["checksum"],
            },
        )
        return {
            "decision": decision,
            "visual_review_snapshot": {
                key: snapshot[key]
                for key in ("source_run_id", "decision_count", "actionable_count", "checksum")
            },
        }

    @router.post("/visual-review/{run_id}/apply", status_code=status.HTTP_202_ACCEPTED)
    def apply_visual_review(
        run_id: int,
        payload: VisualReviewApplyRequest,
        request: Request,
    ) -> dict:
        require_guided_review_access(request)
        if not payload.attested:
            raise HTTPException(status_code=422, detail="Pernyataan penerapan review wajib dicentang.")
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run or run.get("pipeline_version") == "legacy":
            raise HTTPException(status_code=404, detail="Run V2 sumber tidak ditemukan.")
        snapshot = repository.visual_review_snapshot(run_id)
        if not snapshot.get("checksum") or snapshot.get("actionable_count", 0) < 1:
            raise HTTPException(
                status_code=409,
                detail="Belum ada keputusan review actionable untuk diterapkan.",
            )
        if payload.visual_review_checksum != snapshot["checksum"]:
            raise HTTPException(
                status_code=409,
                detail="Dataset keputusan review berubah; muat ulang sebelum menerapkan.",
            )
        document_payload = load_document_payload(repository, run_id)
        if document_payload is None:
            raise HTTPException(
                status_code=410,
                detail="Payload dokumen sudah dipurge; unggah ulang sebelum membuat run turunan.",
            )
        reviewer_id = authorize_api_request(
            request,
            get_settings(),
            payload.reviewer_id,
        )
        try:
            job = manager.enqueue(
                file_name=str(run["file_name"]),
                content_type=run.get("content_type"),
                payload=document_payload,
                analysis_mode=str(run.get("analysis_mode") or "full_audit"),
                force=True,
                resume_from_run_id=run_id,
                external_ai_allowed=bool(run.get("external_ai_allowed", True)),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        repository.add_event(
            run_id,
            event_type="visual_review_reanalysis_enqueued",
            stage="human_review",
            progress=100,
            message="Run turunan untuk menerapkan keputusan review telah masuk antrean.",
            payload={
                "job_id": job["id"],
                "visual_review_checksum": snapshot["checksum"],
                "reviewer_id": reviewer_id,
                "reason": payload.reason.strip(),
            },
        )
        return {
            "job": job,
            "source_run_id": run_id,
            "visual_review_checksum": snapshot["checksum"],
            "review_url": "#/visual-review",
        }

    @router.get("/guided-review/queue")
    def guided_review_queue(
        request: Request,
        review_status: str = "all",
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        require_guided_review_access(request)
        if review_status not in {"all", "pending", "needs_attention", "completed"}:
            raise HTTPException(status_code=422, detail="Filter status guided review tidak valid.")
        repository = AnalysisRepository(db)
        all_items = repository.list_guided_review_runs()
        counts = {
            state: sum(item["review_state"] == state for item in all_items)
            for state in ("pending", "needs_attention", "completed")
        }
        filtered = (
            all_items
            if review_status == "all"
            else [item for item in all_items if item["review_state"] == review_status]
        )
        safe_offset = max(0, offset)
        safe_limit = max(1, min(500, limit))
        return {
            "total": len(all_items),
            "filtered_total": len(filtered),
            "counts": counts,
            "offset": safe_offset,
            "limit": safe_limit,
            "items": filtered[safe_offset:safe_offset + safe_limit],
        }

    @router.get("/guided-review/export")
    def export_guided_review(request: Request) -> Response:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        exported = []
        for run in repository.list_guided_review_runs():
            label = repository.active_expert_review_label(int(run["id"]))
            if not label:
                continue
            mapping_strings = []
            for mapping in label.get("expected_mappings") or []:
                mapping_strings.append(
                    "|".join(
                        str(mapping.get(field) or "-")
                        for field in (
                            "kk_id", "kode", "detail_kode", "grade", "evidence_role"
                        )
                    )
                )
            case_types = {
                "confirmed": ["positive"],
                "corrected": ["historical_failure"],
                "not_evidence": ["negative"],
                "unsure": ["edge"],
            }[label["outcome"]]
            exported.append(
                {
                    "document_id": f"run-{run['id']}-{str(run['sha256'])[:12]}",
                    "file_name": run["file_name"],
                    "sha256": run["sha256"],
                    "consent_scope": "local_analysis_only",
                    "sensitivity": "restricted",
                    "dataset_status": label["dataset_status"],
                    "dataset_partition": label.get("dataset_partition") or "evaluation",
                    "organization": None,
                    "period": None,
                    "case_types": case_types,
                    "expected_mappings": mapping_strings,
                    "expected_source_locations": label.get("expected_source_locations") or [],
                    "expected_template_status": (
                        label.get("expected_template_status") or "not_assessed"
                    ),
                    "labelled_by": label["reviewer_id"],
                    "labelled_at": label["created_at"],
                    "notes": f"Guided review outcome: {label['outcome']}. {label['reason']}",
                }
            )
        content = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in exported
        )
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="guided-expert-review.jsonl"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/guided-review/{run_id}")
    def guided_review_detail(run_id: int, request: Request) -> dict:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run or run.get("pipeline_version") == "legacy":
            raise HTTPException(status_code=404, detail="Run V2 untuk guided review tidak ditemukan.")
        parameters = {
            (item["kk_id"], item["kode"], item["detail_kode"]): item
            for item in repository.parameter_index()
        }
        assessments = {
            item["mapping_candidate_id"]: item
            for item in repository.list_grade_assessments(run_id)
        }
        verification_by_mapping: dict[int, list[dict]] = {}
        for item in repository.list_verification_results(run_id):
            mapping_id = item.get("mapping_candidate_id")
            if mapping_id:
                verification_by_mapping.setdefault(int(mapping_id), []).append(item)
        mappings = []
        for mapping in repository.list_mapping_candidates(run_id):
            parameter = parameters.get(
                (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                {},
            )
            assessment = assessments.get(int(mapping["id"])) or {}
            mappings.append(
                {
                    **mapping,
                    "parameter_uraian": parameter.get("uraian"),
                    "subunsur_name": parameter.get("subunsur_name"),
                    "cara_pengujian": parameter.get("cara_pengujian"),
                    "candidate_grade": assessment.get("candidate_grade"),
                    "grade_ceiling": assessment.get("grade_ceiling"),
                    "verification_results": verification_by_mapping.get(int(mapping["id"]), []),
                }
            )
        return {
            "run": run,
            "facts": repository.list_facts(run_id),
            "mappings": mappings,
            "active_label": repository.active_expert_review_label(run_id),
            "label_history": repository.list_expert_review_label_history(run_id),
        }

    @router.get("/guided-review/{run_id}/document")
    def guided_review_document(run_id: int, request: Request) -> Response:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run or run.get("pipeline_version") == "legacy":
            raise HTTPException(status_code=404, detail="Run V2 untuk guided review tidak ditemukan.")
        payload = load_document_payload(repository, run_id)
        if payload is None:
            raise HTTPException(
                status_code=410,
                detail="Payload dokumen sudah dipurge; unggah ulang dokumen untuk preview.",
            )
        content_type = str(run.get("content_type") or "application/octet-stream")
        inline = content_type == "application/pdf" or content_type.startswith(("image/", "text/"))
        return Response(
            content=payload,
            media_type=content_type,
            headers={
                "Content-Disposition": "inline" if inline else "attachment",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @router.post("/guided-review/{run_id}", status_code=status.HTTP_201_CREATED)
    def save_guided_review(
        run_id: int,
        payload: GuidedExpertReviewRequest,
        request: Request,
    ) -> dict:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run or run.get("pipeline_version") == "legacy":
            raise HTTPException(status_code=404, detail="Run V2 untuk guided review tidak ditemukan.")
        mappings = repository.list_mapping_candidates(run_id)
        mapping = next(
            (
                item for item in mappings
                if item["id"] == payload.selected_mapping_candidate_id
            ),
            None,
        )
        facts = repository.list_facts(run_id)
        facts_by_id = {int(item["id"]): item for item in facts}
        selected_fact_ids = list(dict.fromkeys(payload.selected_source_fact_ids))
        unknown_fact_ids = [fact_id for fact_id in selected_fact_ids if fact_id not in facts_by_id]
        if unknown_fact_ids:
            raise HTTPException(status_code=422, detail="Sumber yang dipilih bukan milik run ini.")

        expected_mappings: list[dict] = []
        expected_evidence_role = str(payload.expected_evidence_role or "").strip()
        expected_template_status = str(payload.expected_template_status or "not_assessed")
        if payload.outcome != "unsure" and expected_template_status not in {
            "template_only", "substantive"
        }:
            raise HTTPException(
                status_code=422,
                detail="Pilih apakah dokumen merupakan template kosong atau memiliki isi substantif.",
            )
        if payload.outcome == "confirmed":
            if not mapping:
                raise HTTPException(status_code=422, detail="Pilih saran parameter yang dinyatakan benar.")
            supporting_ids = {int(item) for item in (mapping.get("supporting_fact_ids") or [])}
            if not selected_fact_ids:
                raise HTTPException(status_code=422, detail="Pilih minimal satu fakta/lokasi sumber.")
            if supporting_ids and not set(selected_fact_ids) <= supporting_ids:
                raise HTTPException(
                    status_code=422,
                    detail="Sumber terpilih harus termasuk fakta pendukung kandidat yang dikonfirmasi.",
                )
            if not expected_evidence_role:
                raise HTTPException(
                    status_code=422,
                    detail="Pilih peran evidence yang sudah diperiksa.",
                )
            assessment = next(
                (
                    item for item in repository.list_grade_assessments(run_id)
                    if item["mapping_candidate_id"] == mapping["id"]
                ),
                {},
            )
            expected_mappings = [{
                "kk_id": mapping["kk_id"],
                "kode": mapping["kode"],
                "detail_kode": mapping["detail_kode"],
                "grade": assessment.get("candidate_grade"),
                "evidence_role": expected_evidence_role,
            }]
        elif payload.outcome == "corrected":
            target_kk = str(payload.expected_mapping.get("kk_id") or "").strip()
            target_kode = str(payload.expected_mapping.get("kode") or "").strip()
            target_detail = str(payload.expected_mapping.get("detail_kode") or "").strip()
            grade = str(payload.expected_mapping.get("grade") or "").strip().upper()
            parameter = next(
                (
                    item for item in repository.parameter_index()
                    if item["kk_id"] == target_kk
                    and item["kode"] == target_kode
                    and item["detail_kode"] == target_detail
                ),
                None,
            )
            if not parameter:
                raise HTTPException(status_code=422, detail="Pilih parameter resmi untuk koreksi.")
            if grade and grade not in {"A", "B", "C", "D", "E"}:
                raise HTTPException(status_code=422, detail="Grade koreksi harus A-E atau dikosongkan.")
            if not selected_fact_ids:
                raise HTTPException(status_code=422, detail="Pilih minimal satu fakta/lokasi sumber.")
            if not expected_evidence_role:
                raise HTTPException(
                    status_code=422,
                    detail="Pilih peran evidence yang sudah diperiksa.",
                )
            expected_mappings = [{
                "kk_id": target_kk,
                "kode": target_kode,
                "detail_kode": target_detail,
                "grade": grade or None,
                "evidence_role": expected_evidence_role,
                "parameter_uraian": parameter.get("uraian"),
            }]
        elif payload.outcome in {"not_evidence", "unsure"}:
            selected_fact_ids = []
        else:
            raise HTTPException(status_code=422, detail="Hasil review tidak valid.")

        expected_source_locations = []
        for fact_id in selected_fact_ids:
            fact = facts_by_id[fact_id]
            for source in fact.get("sources") or []:
                expected_source_locations.append(
                    {
                        "fact_id": fact_id,
                        "unit_id": source.get("unit_id"),
                        "unit_key": source.get("unit_key"),
                        "source_location": source.get("source_location") or {},
                    }
                )
        reviewer_id = authorize_api_request(
            request, get_settings(), payload.reviewer_id
        )
        label = repository.save_expert_review_label(
            run_id,
            {
                "reviewer_id": reviewer_id,
                "outcome": payload.outcome,
                "selected_mapping_candidate_id": mapping.get("id") if mapping else None,
                "selected_fact_ids": selected_fact_ids,
                "expected_mappings": expected_mappings,
                "expected_source_locations": expected_source_locations,
                "reason": payload.reason.strip(),
                "dataset_status": (
                    "pilot_unlabelled" if payload.outcome == "unsure" else "expert_candidate"
                ),
                "dataset_partition": "evaluation",
                "expected_template_status": (
                    "not_assessed" if payload.outcome == "unsure"
                    else expected_template_status
                ),
            },
        )
        feedback = RetrievalFeedbackLearningEngine().refresh_fail_closed(repository)
        repository.add_event(
            run_id,
            event_type="expert_review_label_saved",
            stage="evaluation_learning",
            progress=100,
            message="Label guided expert review tersimpan tanpa mengubah keputusan upload.",
            payload={
                "label_id": label["id"],
                "outcome": label["outcome"],
                "dataset_status": label["dataset_status"],
                "dataset_partition": label["dataset_partition"],
                "retrieval_feedback_registry_sha256": feedback.get("registry_sha256"),
                "retrieval_feedback_term_count": int(feedback.get("term_count") or 0),
                "retrieval_feedback_active": bool(feedback.get("active", feedback.get("is_active"))),
            },
        )
        return {
            "label": label,
            "run": repository.get_run(run_id),
            "retrieval_feedback": feedback,
            "decision_authority": "evaluation_label_only",
            "primary_upload_unchanged": True,
        }

    @router.post("/{run_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    def retry_analysis_run(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        payload = load_document_payload(repository, run_id)
        if payload is None:
            raise HTTPException(status_code=410, detail="Payload sumber sudah dipurge; unggah ulang untuk retry.")
        job = manager.enqueue(
            file_name=run["file_name"],
            content_type=run.get("content_type"),
            payload=payload,
            analysis_mode=run.get("analysis_mode") or "full_audit",
            force=True,
            resume_from_run_id=run_id,
            external_ai_allowed=bool(run.get("external_ai_allowed", True)),
        )
        repository.add_event(
            run_id,
            event_type="retry_enqueued",
            stage="orchestration",
            progress=100,
            message=f"Retry job {job['id']} dibuat dari payload run ini.",
            payload={"job_id": job["id"]},
        )
        return {"job": job, "resumes_from_run_id": run_id}

    @router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_analysis_run(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        job = repository.find_job_for_run(run_id)
        if not job:
            if run.get("status") == "cancelled":
                return {"run": run, "job": None, "idempotent": True}
            raise HTTPException(
                status_code=409,
                detail="Analysis run ini tidak lagi terikat ke job aktif yang dapat dibatalkan.",
            )
        if job.get("status") in {"completed", "failed", "cancelled"}:
            if job.get("status") == "cancelled" and run.get("status") == "cancelled":
                return {"run": run, "job": job, "idempotent": True}
            raise HTTPException(
                status_code=409,
                detail="Analysis run sudah terminal dan tidak dapat dibatalkan.",
            )
        cancelled = manager.cancel(str(job["id"]))
        if not cancelled:
            raise HTTPException(status_code=409, detail="Job analysis run tidak dapat dibatalkan.")
        return {
            "run": repository.get_run(run_id),
            "job": cancelled,
            "idempotent": False,
        }

    @router.post("/{run_id}/expand-candidates")
    def expand_candidates(
        run_id: int,
        payload: CandidateExpansionRequest,
        request: Request,
    ) -> dict:
        require_analysis_access(request)
        try:
            return AnalysisOrchestrator(db, get_settings()).expand_candidates(run_id, limit=payload.limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/{run_id}")
    def analysis_run(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        parameters = {
            (item["kk_id"], item["kode"], item["detail_kode"]): item
            for item in repository.parameter_index()
        }
        facts = repository.list_facts(run_id)
        engine_results = repository.list_engine_results(run_id)
        file_kind = next(
            (
                str(item.get("output", {}).get("file_kind") or "")
                for item in engine_results
                if item.get("engine_name") == "file_router"
                and item.get("output", {}).get("file_kind")
            ),
            "text",
        )
        document_role = infer_document_role(
            DocumentIdentity(
                file_name=run["file_name"],
                content_type=run.get("content_type"),
                size_bytes=int(run.get("size_bytes") or 0),
                sha256=run["sha256"],
                file_kind=file_kind,
            ),
            facts,
        )
        mappings = []
        for mapping in repository.list_mapping_candidates(run_id):
            parameter = parameters.get(
                (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                {},
            )
            mappings.append(
                {
                    **mapping,
                    "parameter_uraian": parameter.get("uraian"),
                    "kk_title": parameter.get("kk_title"),
                    "unsur": parameter.get("unsur"),
                    "matrix_subunsur_name": parameter.get("matrix_subunsur_name"),
                    "subunsur_name": parameter.get("subunsur_name"),
                    "cara_pengujian": parameter.get("cara_pengujian"),
                    "available_grades": [
                        item.get("grade")
                        for item in (parameter.get("grades") or [])
                        if item.get("grade")
                    ],
                    "document_role": document_role,
                }
            )
        return {
            "run": run,
            "events": repository.list_events(run_id),
            "engines": engine_results,
            "security_findings": repository.list_security_findings(run_id),
            "document_units": repository.list_document_units(run_id),
            "document_structures": repository.list_document_structures(run_id),
            "facts": facts,
            "mappings": mappings,
            "grade_assessments": repository.list_grade_assessments(run_id),
            "verification_results": repository.list_verification_results(run_id),
            "human_review_decisions": repository.list_human_review_decisions(run_id),
            "controlled_upload_actions": repository.list_controlled_upload_actions(run_id),
            "controlled_upload_reconciliations": (
                repository.list_controlled_upload_reconciliation_summaries(run_id)
            ),
        }

    @router.get("/{run_id}/events")
    def analysis_events(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {"run_id": run_id, "events": repository.list_events(run_id)}

    @router.get("/{run_id}/events/stream")
    async def analysis_event_stream(run_id: int, request: Request) -> StreamingResponse:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")

        async def stream():
            last_id = 0
            idle_after_terminal = 0
            while not await request.is_disconnected():
                events = [item for item in repository.list_events(run_id) if int(item["id"]) > last_id]
                for event in events:
                    last_id = int(event["id"])
                    yield f"id: {last_id}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                run = repository.get_run(run_id) or {}
                terminal = run.get("status") in {
                    "blocked", "cancelled", "screening_complete", "review_required",
                    "approved", "rejected", "failed",
                }
                idle_after_terminal = idle_after_terminal + 1 if terminal and not events else 0
                if idle_after_terminal >= 2:
                    break
                if not events:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/{run_id}/units")
    def analysis_units(run_id: int, request: Request, include_text: bool = False) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {
            "run_id": run_id,
            "units": repository.list_document_units(run_id, include_text=include_text),
        }

    @router.get("/{run_id}/checkpoints")
    def analysis_unit_checkpoints(
        run_id: int,
        request: Request,
        stage: str | None = None,
    ) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {
            "run_id": run_id,
            "summary": repository.checkpoint_summary(run_id),
            "checkpoints": repository.list_unit_checkpoints(run_id, stage),
        }

    @router.get("/{run_id}/document-map")
    def analysis_document_map(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {
            "run_id": run_id,
            "structures": repository.list_document_structures(run_id),
        }

    @router.get("/{run_id}/facts")
    def analysis_facts(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {"run_id": run_id, "facts": repository.list_facts(run_id)}

    @router.get("/{run_id}/mappings")
    def analysis_mappings(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {
            "run_id": run_id,
            "mappings": repository.list_mapping_candidates(run_id),
            "grade_assessments": repository.list_grade_assessments(run_id),
            "verification_results": repository.list_verification_results(run_id),
        }

    @router.post("/{run_id}/reverify")
    def reverify_run(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        try:
            return AnalysisOrchestrator(db, get_settings()).reverify(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/{run_id}/review-decisions")
    def review_decisions(run_id: int, request: Request) -> dict:
        require_analysis_access(request)
        repository = AnalysisRepository(db)
        if not repository.get_run(run_id):
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        return {"run_id": run_id, "decisions": repository.list_human_review_decisions(run_id)}

    @router.post("/{run_id}/review-decisions", status_code=status.HTTP_201_CREATED)
    def create_review_decision(run_id: int, payload: HumanReviewDecisionRequest, request: Request) -> dict:
        require_guided_review_access(request)
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        mappings = repository.list_mapping_candidates(run_id)
        mapping = next(
            (item for item in mappings if item["id"] == payload.mapping_candidate_id),
            None,
        )
        if payload.decision in {"approve", "correct"} and not mapping:
            raise HTTPException(status_code=409, detail="Mapping candidate tidak valid untuk analysis run ini.")
        if payload.decision == "correct" and not payload.final_mapping:
            raise HTTPException(status_code=422, detail="Koreksi membutuhkan final_mapping.")
        if payload.decision == "correct":
            target_kk = str(payload.final_mapping.get("kk_id") or "").strip()
            target_kode = str(payload.final_mapping.get("kode") or "").strip()
            target_detail = str(payload.final_mapping.get("detail_kode") or "").strip()
            valid_parameter = next(
                (
                    item for item in repository.parameter_index()
                    if item["kk_id"] == target_kk
                    and item["kode"] == target_kode
                    and item["detail_kode"] == target_detail
                ),
                None,
            )
            if not valid_parameter:
                raise HTTPException(
                    status_code=422,
                    detail="Target koreksi tidak cocok dengan parameter resmi pada matriks.",
                )
            corrected_grade = str(payload.final_mapping.get("grade") or "").strip().upper()
            if corrected_grade and corrected_grade not in {"A", "B", "C", "D", "E"}:
                raise HTTPException(status_code=422, detail="Grade koreksi harus A-E.")
            payload.final_mapping.update(
                {
                    "kk_id": target_kk,
                    "kode": target_kode,
                    "detail_kode": target_detail,
                    "grade": corrected_grade or None,
                    "parameter_uraian": valid_parameter.get("uraian"),
                }
            )

        if payload.decision == "approve":
            assessments = repository.list_grade_assessments(run_id)
            assessment = next(
                (item for item in assessments if item["mapping_candidate_id"] == payload.mapping_candidate_id),
                None,
            )
            verification = [
                item
                for item in repository.list_verification_results(run_id)
                if item["mapping_candidate_id"] == payload.mapping_candidate_id
            ]
            if not assessment or not assessment.get("primary_allowed"):
                raise HTTPException(
                    status_code=409,
                    detail="Approval ditahan: grade rule belum mengizinkan kandidat utama.",
                )
            if not verification or not all(item.get("status") == "verified" for item in verification):
                raise HTTPException(
                    status_code=409,
                    detail="Approval ditahan: Independent Verification belum berstatus verified.",
                )
            if run.get("coverage_status") != "complete":
                raise HTTPException(status_code=409, detail="Approval ditahan: coverage dokumen belum lengkap.")

        decision = {
            "mapping_candidate_id": payload.mapping_candidate_id,
            "reviewer_id": authorize_api_request(
                request, get_settings(), payload.reviewer_id
            ),
            "decision": payload.decision,
            "original_mapping": mapping or {},
            "final_mapping": payload.final_mapping or mapping or {},
            "reason": payload.reason.strip(),
            "override_warnings": [],
            "pipeline_version": run["pipeline_version"],
            "rule_version": run["rule_version"],
        }
        decision_id = repository.save_human_review_decision(run_id, decision)
        if payload.decision == "approve":
            repository.update_run(
                run_id,
                status="approved",
                primary_blocked=False,
                block_reasons_json=[],
            )
        elif payload.decision == "reject":
            repository.update_run(
                run_id,
                status="rejected",
                primary_blocked=True,
                block_reasons_json=[payload.reason.strip()],
            )
        else:
            repository.update_run(
                run_id,
                status="review_required",
                primary_blocked=True,
            )
        repository.add_event(
            run_id,
            event_type="human_review_decision",
            stage="human_review",
            progress=100,
            message=f"Reviewer mencatat keputusan: {payload.decision}.",
            payload={"decision_id": decision_id, "mapping_candidate_id": payload.mapping_candidate_id},
        )
        repository.save_engine_result(
            run_id,
            EngineResult(
                engine_name="human_review",
                engine_version=PIPELINE_VERSION,
                status=EngineStatus.COMPLETED,
                input_checksum=run["sha256"],
                input_refs=[f"mapping:{payload.mapping_candidate_id}"],
                output_refs=[f"human-review-decision:{decision_id}"],
                coverage={"required": 1, "processed": 1, "failed": 0},
                metrics={"decision_count": len(repository.list_human_review_decisions(run_id))},
                output={
                    "latest_decision_id": decision_id,
                    "latest_decision": payload.decision,
                    "decision_authority": "human",
                },
            ).finish(),
        )
        return {
            "decision_id": decision_id,
            "run": repository.get_run(run_id),
            "decision": repository.list_human_review_decisions(run_id)[-1],
        }

    @router.post(
        "/{run_id}/controlled-upload-actions/{action_id}/reconciliation",
        status_code=status.HTTP_201_CREATED,
    )
    def reconcile_controlled_upload(
        run_id: int,
        action_id: int,
        payload: ControlledUploadReconciliationRequest,
        request: Request,
    ) -> dict:
        require_governance_access(request)
        settings = get_settings()
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        action = repository.get_controlled_upload_action(action_id)
        if not action or int(action["run_id"]) != int(run_id):
            raise HTTPException(
                status_code=404,
                detail="Controlled upload action tidak ditemukan pada run ini.",
            )
        if action.get("status") != "blocked_ambiguous":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Hanya hasil terminal blocked_ambiguous yang dapat direkonsiliasi; "
                    "reservation uploading tidak boleh diubah atau di-retry dari endpoint ini."
                ),
            )
        if not payload.attested:
            raise HTTPException(
                status_code=422,
                detail="Reviewer wajib menyatakan bahwa folder tujuan dan legacy review sudah diperiksa.",
            )
        reviewer_id = authorize_api_request(
            request,
            settings,
            payload.reviewer_id,
        )
        try:
            event, before, after = repository.save_controlled_upload_reconciliation_event(
                action_id=action_id,
                reviewer_id=reviewer_id,
                outcome=payload.outcome,
                reason=payload.reason.strip(),
                expected_latest_event_id=payload.expected_latest_event_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if after["effective"] and not before["effective"]:
            if after["outcome"] == "confirmed_uploaded":
                repository.update_run(run_id, status="uploaded", primary_blocked=False)
            repository.add_event(
                run_id,
                event_type="controlled_upload_reconciled",
                stage="controlled_upload",
                progress=100,
                message=(
                    "Dua reviewer independen menyelesaikan rekonsiliasi controlled upload."
                ),
                payload={
                    "action_id": action_id,
                    "reconciliation_event_id": event["id"],
                    "outcome": after["outcome"],
                },
            )
        return {
            "event": event,
            "reconciliation": after,
            "run": repository.get_run(run_id),
        }

    @router.post("/{run_id}/approve-upload", status_code=status.HTTP_201_CREATED)
    @router.post("/{run_id}/controlled-upload", status_code=status.HTTP_201_CREATED)
    def controlled_upload(run_id: int, payload: ControlledUploadRequest, request: Request) -> dict:
        require_governance_access(request)
        settings = get_settings()
        repository = AnalysisRepository(db)
        run = repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run tidak ditemukan.")
        mapping = next(
            (
                item for item in repository.list_mapping_candidates(run_id)
                if item["id"] == payload.mapping_candidate_id
            ),
            None,
        )
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping kandidat tidak ditemukan pada run ini.")
        reviewer_id = authorize_api_request(request, settings, payload.reviewer_id)
        approvals = [
            item for item in repository.list_human_review_decisions(run_id)
            if item.get("mapping_candidate_id") == payload.mapping_candidate_id
            and item.get("decision") == "approve"
        ]
        assessment = next(
            (
                item for item in repository.list_grade_assessments(run_id)
                if item.get("mapping_candidate_id") == payload.mapping_candidate_id
            ),
            None,
        )
        verifications = [
            item for item in repository.list_verification_results(run_id)
            if item.get("mapping_candidate_id") == payload.mapping_candidate_id
        ]
        gate_reasons = []
        if run.get("status") not in {"approved", "uploaded"} or run.get("primary_blocked"):
            gate_reasons.append("run belum berada pada status approved")
        if run.get("coverage_status") != "complete":
            gate_reasons.append("coverage belum lengkap")
        if not assessment or not assessment.get("primary_allowed") or not assessment.get("candidate_grade"):
            gate_reasons.append("grade rule belum disahkan")
        if not verifications or not all(item.get("status") == "verified" for item in verifications):
            gate_reasons.append("verification belum seluruhnya verified")
        if not approvals:
            gate_reasons.append("human approval untuk mapping belum tersedia")
        if gate_reasons:
            raise HTTPException(
                status_code=409,
                detail="Controlled upload ditahan: " + "; ".join(gate_reasons) + ".",
            )
        previous_uploads = [
            item for item in repository.list_controlled_upload_actions(run_id)
            if item.get("mapping_candidate_id") == payload.mapping_candidate_id
            and item.get("status") == "uploaded_primary"
        ]
        if previous_uploads:
            previous = previous_uploads[-1]
            repository.update_run(run_id, status="uploaded", primary_blocked=False)
            return {
                "action_id": previous["id"],
                "upload": {
                    "status": previous["status"],
                    "message": previous["message"],
                    "candidate": previous.get("destination") or {},
                },
                "idempotent": True,
            }
        if not settings.smart_upload_allow_real_upload:
            raise HTTPException(
                status_code=409,
                detail="Upload sungguhan masih dikunci oleh SMART_UPLOAD_ALLOW_REAL_UPLOAD=false.",
            )
        if not settings.has_share_token:
            raise HTTPException(
                status_code=409,
                detail="LUMBUNG_SHARE_TOKEN belum tersedia untuk upload WebDAV.",
            )
        source_bytes = load_document_payload(repository, run_id)
        if source_bytes is None:
            raise HTTPException(status_code=410, detail="Payload sumber sudah dipurge; unggah ulang untuk melanjutkan.")
        grade = str(assessment["candidate_grade"])
        slots = [
            item for item in db.evidence_slots(mapping["kk_id"], mapping["kode"])
            if item.get("detail_kode") == mapping["detail_kode"] and item.get("grade") == grade
        ]
        if payload.category_name:
            slots = [item for item in slots if item.get("category_name") == payload.category_name]
        if len(slots) != 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Tujuan evidence tidak unik; pilih category_name yang valid."
                    if slots else "Folder evidence untuk parameter-grade tidak ditemukan."
                ),
            )
        slot = slots[0]
        candidate = {
            **mapping,
            "grade": grade,
            "folder_path": slot["folder_path"],
            "category_name": slot["category_name"],
            "public_url": slot.get("public_url"),
        }
        reservation, created = repository.reserve_controlled_upload_action(
            run_id=run_id,
            mapping_candidate_id=payload.mapping_candidate_id,
            reviewer_id=reviewer_id,
            destination=candidate,
        )
        if not created:
            if reservation.get("status") == "uploaded_primary":
                repository.update_run(run_id, status="uploaded", primary_blocked=False)
                return {
                    "action_id": reservation["id"],
                    "upload": {
                        "status": reservation["status"],
                        "message": reservation["message"],
                        "candidate": reservation.get("destination") or {},
                    },
                    "idempotent": True,
                }
            if reservation.get("status") == "uploading":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Controlled upload untuk mapping ini sedang diproses; "
                        "jangan mengulangi permintaan sampai status akhirnya terverifikasi."
                    ),
                )
            if reservation.get("status") == "blocked_ambiguous":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Hasil controlled upload sebelumnya ambigu; verifikasi folder tujuan "
                        "dan legacy review sebelum tindakan manual."
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail="Controlled upload untuk mapping ini sudah mempunyai reservation terminal.",
            )
        try:
            bridge_result = execute_legacy_controlled_upload(
                db,
                settings,
                run=run,
                candidate=candidate,
                source_bytes=source_bytes,
            )
            legacy_review_id = int(bridge_result["legacy_review_id"])
            upload_result = bridge_result["upload"]
        except LegacyBridgeError as exc:
            finalized = repository.finalize_controlled_upload_action(
                int(reservation["id"]),
                status="blocked_ambiguous",
                legacy_review_id=exc.legacy_review_id,
                destination=candidate,
                message=str(exc),
            )
            if not finalized.get("_write_applied"):
                raise HTTPException(
                    status_code=409,
                    detail="Status reservation controlled upload berubah; verifikasi audit sebelum retry.",
                ) from exc
            repository.add_event(
                run_id,
                event_type="controlled_upload_ambiguous",
                stage="controlled_upload",
                progress=100,
                message=str(exc),
                payload={
                    "action_id": reservation["id"],
                    "legacy_review_id": exc.legacy_review_id,
                },
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if upload_result.get("status") != "uploaded_primary":
            raise RuntimeError(
                "Legacy controlled-upload bridge tidak mengembalikan status uploaded_primary."
            )
        finalized = repository.finalize_controlled_upload_action(
            int(reservation["id"]),
            status="uploaded_primary",
            legacy_review_id=legacy_review_id,
            destination=upload_result.get("candidate") or candidate,
            message=upload_result["message"],
        )
        if not finalized.get("_write_applied"):
            raise HTTPException(
                status_code=409,
                detail="Finalisasi controlled upload kehilangan reservation aktif; periksa audit sebelum retry.",
            )
        action_id = int(reservation["id"])
        repository.update_run(run_id, status="uploaded", primary_blocked=False)
        repository.add_event(
            run_id,
            event_type="controlled_upload_completed",
            stage="controlled_upload",
            progress=100,
            message=upload_result["message"],
            payload={"action_id": action_id, "legacy_review_id": legacy_review_id},
        )
        return {"action_id": action_id, "upload": upload_result, "idempotent": False}

    return router


async def _read_payload(file: UploadFile, max_bytes: int) -> bytes:
    if max_bytes > 0:
        payload = await file.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Ukuran file melebihi batas analisis {max_bytes} byte.",
            )
        return payload
    return await file.read()


def _batch_dedupe_key(
    archive_sha256: str,
    analysis_mode: str,
    review_limit: int,
    external_ai_allowed: bool,
) -> str:
    material = (
        f"{archive_sha256}:{analysis_mode}:{review_limit}:"
        f"external-ai={int(bool(external_ai_allowed))}:selection=v1"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _group_rule_catalog(catalog: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    for rule in catalog:
        key = (rule["kk_id"], rule["kode"], rule["detail_kode"])
        item = grouped.setdefault(
            key,
            {
                "kk_id": rule["kk_id"],
                "kode": rule["kode"],
                "detail_kode": rule["detail_kode"],
                "parameter_no": rule.get("parameter_no"),
                "uraian": rule.get("uraian"),
                "rules": [],
            },
        )
        item["rules"].append(rule)
    result = []
    for item in grouped.values():
        statuses = [rule["approval_status"] for rule in item["rules"]]
        if statuses and all(status == "approved" for status in statuses):
            state = "approved"
        elif any(status == "rejected" for status in statuses):
            state = "rejected"
        elif any(status == "approved" for status in statuses):
            state = "partial"
        else:
            state = "pending"
        item["rules"].sort(key=lambda rule: rule["grade"])
        item["review_state"] = state
        item["approved_grade_count"] = sum(status == "approved" for status in statuses)
        item["search_text"] = " ".join(
            str(item.get(field) or "").lower()
            for field in ("kk_id", "kode", "detail_kode", "parameter_no", "uraian")
        )
        result.append(item)
    return sorted(
        result,
        key=lambda item: (item["kk_id"], item["kode"], item["detail_kode"]),
    )
