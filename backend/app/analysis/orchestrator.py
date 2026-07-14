from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.advanced_rag import (
    ADVANCED_RAG_VERSION,
    AdvancedRAGQueryExpansionEngine,
    expand_domain_tokens,
    retrieval_needs_model_expansion,
)
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus, utc_now_iso
from app.analysis.document_map import CoverageEngine, DocumentStructureEngine, NativeParsingEngine
from app.analysis.domain.grading import (
    DomainRuleGradeEngine,
    IndependentVerificationEngine,
    ModelSecondPassVerificationEngine,
)
from app.analysis.domain.retrieval import (
    ParameterRetrievalEngine,
    SPIPMappingEngine,
    parameter_corpus_tokens,
)
from app.analysis.facts import (
    FactExtractionEngine,
    StructuredFactExtractionEngine,
    is_fact_eligible_unit,
)
from app.analysis.explainability import OutputExplainabilityEngine
from app.analysis.intake import FileIntakeSecurityEngine, FileRouterEngine
from app.analysis.local_ocr import configured_local_ocr_provider, local_ocr_runtime_status
from app.analysis.mapping_reasoning import ConstrainedMappingReasoningEngine
from app.analysis.repository import AnalysisRepository
from app.analysis.provider import (
    configured_mapping_provider,
    configured_rag_query_provider,
    configured_structured_provider,
    configured_verification_provider,
    configured_vision_provider,
)
from app.analysis.routing import ComputeRoutingEngine, ROUTING_POLICY_VERSION
from app.analysis.vision import VisualCancellationRequested, VisualOCREngine
from app.analysis.template_detection import TemplateCompletenessEngine
from app.config import Settings
from app.database import Database


ANALYSIS_MODES = {"screening", "full_audit"}
LEGACY_MODE_MAP = {
    "fast": "screening",
    "deep": "full_audit",
    "full": "full_audit",
}
CHECKPOINT_POLICY_VERSION = "unit-checkpoint-v2"


def normalize_analysis_mode(value: str | None) -> str:
    normalized = str(value or "full_audit").strip().lower()
    normalized = LEGACY_MODE_MAP.get(normalized, normalized)
    return normalized if normalized in ANALYSIS_MODES else "full_audit"


def configuration_hash(settings: Settings, analysis_mode: str) -> str:
    payload = {
        "analysis_mode": analysis_mode,
        "pipeline_version": PIPELINE_VERSION,
        "parser_version": PARSER_VERSION,
        "rule_version": RULE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "routing_policy_version": ROUTING_POLICY_VERSION,
        "checkpoint_policy_version": CHECKPOINT_POLICY_VERSION,
        "vision_enabled": settings.vision_analysis_enabled,
        "vision_max_units": settings.analysis_vision_max_units,
        "pdf_render_dpi": settings.analysis_pdf_render_dpi,
        "pdf_retry_render_dpi": settings.analysis_pdf_retry_render_dpi,
        "pdf_retry_max_units": settings.analysis_pdf_retry_max_units,
        "office_rendering_enabled": settings.analysis_office_rendering_enabled,
        "office_render_max_pages": settings.analysis_office_render_max_pages,
        "local_ocr_enabled": settings.analysis_local_ocr_enabled,
        "local_ocr_provider": settings.analysis_local_ocr_provider,
        "local_ocr_languages": settings.analysis_local_ocr_languages,
        "local_ocr_min_confidence": settings.analysis_local_ocr_min_confidence,
        "local_ocr_max_units": settings.analysis_local_ocr_max_units,
        "local_ocr_timeout_seconds": settings.analysis_local_ocr_timeout_seconds,
        "local_ocr_unit_budget_seconds": settings.analysis_local_ocr_unit_budget_seconds,
        "local_ocr_document_budget_seconds": settings.analysis_local_ocr_document_budget_seconds,
        "local_ocr_max_attempts_per_unit": settings.analysis_local_ocr_max_attempts_per_unit,
        "local_ocr_render_batch_units": settings.analysis_local_ocr_render_batch_units,
        "local_ocr_tesseract_psm_modes": settings.analysis_local_ocr_tesseract_psm_modes,
        "local_ocr_preprocessing_enabled": settings.analysis_local_ocr_preprocessing_enabled,
        "structured_model_enabled": settings.analysis_structured_model_enabled,
        "mapping_reasoning_enabled": settings.analysis_mapping_reasoning_enabled,
        "advanced_rag_enabled": settings.analysis_advanced_rag_enabled,
        "advanced_rag_deepseek_enabled": settings.analysis_advanced_rag_deepseek_enabled,
        "advanced_rag_version": ADVANCED_RAG_VERSION,
        "advanced_rag_min_confidence": settings.analysis_advanced_rag_min_confidence,
        "advanced_rag_ambiguity_margin": settings.analysis_advanced_rag_ambiguity_margin,
        "verification_enabled": settings.verification_pass_enabled,
        "model_verifier_enabled": settings.analysis_model_verifier_enabled,
        "routing_structured_min_complexity": settings.analysis_routing_structured_min_complexity,
        "routing_mapping_margin": settings.analysis_routing_mapping_margin,
        "routing_verifier_min_risk": settings.analysis_routing_verifier_min_risk,
        "api_surface": settings.analysis_api_surface,
        "responses_max_output_tokens": settings.analysis_responses_max_output_tokens,
        "allow_partial_primary": settings.allow_partial_primary,
        "payload_storage_backend": settings.analysis_payload_storage_backend,
        "provider": settings.ai_provider,
        "model": settings.deepseek_model,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AnalysisOrchestrator:
    def __init__(self, db: Database, settings: Settings):
        self.settings = settings
        self.repository = AnalysisRepository(db, settings=settings)
        self.intake_engine = FileIntakeSecurityEngine()
        self.router_engine = FileRouterEngine()
        self.native_parsing_engine = NativeParsingEngine()
        self.document_structure_engine = DocumentStructureEngine()
        self.coverage_engine = CoverageEngine()
        self.template_completeness_engine = TemplateCompletenessEngine()
        self.fact_extraction_engine = FactExtractionEngine()
        self.retrieval_engine = ParameterRetrievalEngine(
            advanced_rag_enabled=settings.analysis_advanced_rag_enabled
        )
        self.mapping_engine = SPIPMappingEngine()
        self.grade_engine = DomainRuleGradeEngine()
        self.verification_engine = IndependentVerificationEngine()
        self.explainability_engine = OutputExplainabilityEngine()
        self.compute_routing_engine = ComputeRoutingEngine()
        self.structured_provider = configured_structured_provider(settings)
        self.mapping_provider = configured_mapping_provider(settings)
        self.rag_query_provider = configured_rag_query_provider(settings)
        self.rag_query_expansion_engine = (
            AdvancedRAGQueryExpansionEngine(self.rag_query_provider)
            if self.rag_query_provider else None
        )
        self.mapping_reasoning_engine = (
            ConstrainedMappingReasoningEngine(self.mapping_provider)
            if self.mapping_provider else None
        )
        self.vision_governance = self.repository.vision_governance_status(
            settings,
            renderer_available=bool(shutil.which("pdftoppm")),
        )
        vision_provider = (
            configured_vision_provider(settings)
            if self.vision_governance["effective"]
            else None
        )
        self.local_ocr_provider = configured_local_ocr_provider(settings)
        self.local_ocr_status = local_ocr_runtime_status(settings, self.local_ocr_provider)
        self.visual_ocr_engine = VisualOCREngine(
            vision_provider,
            self.local_ocr_provider,
        )
        verification_provider = configured_verification_provider(settings)
        self.model_verification_engine = (
            ModelSecondPassVerificationEngine(verification_provider)
            if verification_provider else None
        )

    def start(
        self,
        *,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        analysis_mode: str = "full_audit",
        cancellation_check: Callable[[], bool] | None = None,
        run_created_callback: Callable[[int], None] | None = None,
        resume_from_run_id: int | None = None,
        external_ai_allowed: bool = True,
    ) -> dict:
        mode = normalize_analysis_mode(analysis_mode)
        identity, findings, intake_result = self.intake_engine.run(
            file_name=file_name,
            content_type=content_type,
            payload=payload,
            max_bytes=self.settings.smart_upload_max_bytes,
        )
        document = self.repository.upsert_document(
            file_name=identity.file_name,
            content_type=identity.content_type,
            size_bytes=identity.size_bytes,
            sha256=identity.sha256,
            payload=payload,
            ttl_hours=self.settings.analysis_pending_file_ttl_hours,
        )
        visual_review_snapshot = self.repository.visual_review_snapshot(None)
        if resume_from_run_id:
            source_run = self.repository.get_run(int(resume_from_run_id))
            if (
                source_run
                and source_run.get("pipeline_version") != "legacy"
                and source_run.get("sha256") == identity.sha256
            ):
                visual_review_snapshot = self.repository.visual_review_snapshot(
                    int(resume_from_run_id)
                )
        run_id = self.repository.create_run(
            document_id=int(document["id"]),
            analysis_mode=mode,
            pipeline_version=PIPELINE_VERSION,
            parser_version=PARSER_VERSION,
            rule_version=RULE_VERSION,
            prompt_version=PROMPT_VERSION,
            provider=(self.settings.ai_provider if external_ai_allowed else "local_only"),
            model=(self.settings.deepseek_model if external_ai_allowed else None),
            configuration_hash=configuration_hash(self.settings, mode),
            resumed_from_run_id=resume_from_run_id,
            external_ai_allowed=external_ai_allowed,
            visual_review_checksum=visual_review_snapshot.get("checksum"),
        )
        if run_created_callback:
            run_created_callback(run_id)
        self.repository.update_run(run_id, status="intake", started_at=utc_now_iso())
        self.repository.add_event(
            run_id,
            event_type="stage_started",
            stage="intake",
            progress=2,
            message="File diterima oleh File Intake & Security Engine.",
        )
        self.repository.save_engine_result(run_id, intake_result)
        for finding in findings:
            self.repository.add_security_finding(run_id, int(document["id"]), finding)

        if intake_result.status == EngineStatus.BLOCKED:
            reasons = [finding.message for finding in findings if finding.blocking]
            self.repository.update_run(
                run_id,
                status="blocked",
                coverage_status="blocked",
                primary_blocked=True,
                block_reasons_json=reasons,
                error_message=reasons[0] if reasons else "File ditolak Security Engine.",
                finished_at=utc_now_iso(),
            )
            self.repository.add_event(
                run_id,
                event_type="run_blocked",
                stage="intake",
                progress=100,
                message="Analisis dihentikan oleh File Intake & Security Engine.",
                payload={"reasons": reasons},
            )
            return self.describe(run_id)

        cancelled = self._cancel_if_requested(run_id, cancellation_check, "intake", 6)
        if cancelled:
            return cancelled

        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="intake",
            progress=6,
            message="Identitas, checksum, signature, dan keamanan file tervalidasi.",
            payload={"file_kind": identity.file_kind},
        )
        router_result = self.router_engine.run(
            identity,
            vision_enabled=bool(
                self.local_ocr_status["available"]
                or self.vision_governance["effective"]
            ),
        )
        self.repository.save_engine_result(run_id, router_result)
        if router_result.status == EngineStatus.BLOCKED:
            reason = router_result.error_message or "File Router tidak menemukan processor."
            self.repository.update_run(
                run_id,
                status="blocked",
                coverage_status="blocked",
                primary_blocked=True,
                block_reasons_json=[reason],
                error_message=reason,
                finished_at=utc_now_iso(),
            )
            self.repository.add_event(
                run_id,
                event_type="run_blocked",
                stage="routing",
                progress=100,
                message=reason,
            )
            return self.describe(run_id)

        cancelled = self._cancel_if_requested(run_id, cancellation_check, "routing", 10)
        if cancelled:
            return cancelled

        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="routing",
            progress=10,
            message=f"File diarahkan ke {router_result.output.get('processor')}.",
            payload=router_result.output,
        )
        self.repository.add_event(
            run_id,
            event_type="stage_started",
            stage="native_parsing",
            progress=12,
            message="Native Parsing Engine mulai membentuk unit dokumen.",
        )
        resumed_artifacts = self._load_resumable_units(
            resume_from_run_id,
            identity,
            mode,
        )
        visual_artifacts_complete = False
        if resumed_artifacts:
            units, inventory, visual_artifacts_complete = resumed_artifacts
            parsing_result = EngineResult(
                engine_name="native_parsing",
                engine_version=PARSER_VERSION,
                status=(
                    EngineStatus.COMPLETED
                    if units and all(unit.get("status") == "processed" for unit in units)
                    else EngineStatus.PARTIAL
                ),
                input_checksum=identity.sha256,
                input_refs=[f"resume-run:{resume_from_run_id}"],
                output_refs=[f"unit:{unit['unit_key']}" for unit in units],
                coverage={
                    "required": len(units),
                    "processed": sum(unit.get("status") in {"processed", "partial"} for unit in units),
                    "failed": sum(unit.get("status") == "failed" for unit in units),
                },
                warnings=[f"Unit parsing digunakan ulang dari run #{resume_from_run_id}."],
                metrics={"duration_ms": 0, "unit_count": len(units), "resumed_unit_count": len(units)},
                output={"inventory": inventory, "resumed_from_run_id": resume_from_run_id},
            ).finish()
            self.repository.add_event(
                run_id,
                event_type="unit_checkpoints_reused",
                stage="native_parsing",
                progress=28,
                message=f"{len(units)} unit parsing digunakan ulang dari run #{resume_from_run_id}.",
                payload={"resumed_from_run_id": resume_from_run_id, "unit_count": len(units)},
            )
        else:
            units, inventory, parsing_result = self.native_parsing_engine.run(identity, payload, mode)
        self.repository.save_engine_result(run_id, parsing_result)
        if parsing_result.status == EngineStatus.FAILED:
            reason = parsing_result.error_message or "Native Parsing Engine gagal."
            self.repository.update_run(
                run_id,
                status="blocked",
                coverage_status="failed",
                primary_blocked=True,
                block_reasons_json=[reason],
                error_message=reason,
                finished_at=utc_now_iso(),
            )
            self.repository.add_event(
                run_id,
                event_type="run_blocked",
                stage="native_parsing",
                progress=100,
                message=reason,
            )
            return self.describe(run_id)

        cancelled = self._cancel_if_requested(run_id, cancellation_check, "native_parsing", 30)
        if cancelled:
            return cancelled

        self.repository.add_event(
            run_id,
            event_type="stage_started",
            stage="visual_ocr",
            progress=30,
            message="Visual/OCR Engine memeriksa unit yang tidak memiliki text layer.",
        )
        visual_checkpoint_batches = 0
        visual_manifest_initialized = False
        if resumed_artifacts and visual_artifacts_complete:
            visual_result = EngineResult(
                engine_name="visual_ocr",
                engine_version=PIPELINE_VERSION,
                status=EngineStatus.SKIPPED,
                input_checksum=identity.sha256,
                input_refs=[f"resume-run:{resume_from_run_id}"],
                output_refs=[f"unit:{unit['unit_key']}" for unit in units],
                coverage={"required": len(units), "processed": len(units), "failed": 0},
                warnings=["Visual/OCR artifact digunakan ulang dari checkpoint unit."],
                metrics={"duration_ms": 0, "resumed_unit_count": len(units)},
                output={"ocr_required": 0, "ocr_processed": 0, "resumed": True},
            ).finish()
        else:
            def persist_visual_checkpoint(
                progress_units: list[dict],
                checkpoint: dict,
            ) -> None:
                nonlocal visual_checkpoint_batches, visual_manifest_initialized
                batch_keys = {
                    str(key) for key in (checkpoint.get("unit_keys") or [])
                }
                batch_units = [
                    unit for unit in progress_units
                    if str(unit.get("unit_key") or "") in batch_keys
                ]
                successful_units = [
                    unit for unit in batch_units
                    if unit.get("status") in {"processed", "partial"}
                ]
                persisted_units = (
                    progress_units if not visual_manifest_initialized else batch_units
                )
                self.repository.save_document_units(run_id, persisted_units)
                self._checkpoint_units(
                    run_id,
                    "visual_ocr_manifest",
                    persisted_units,
                )
                visual_manifest_initialized = True
                if successful_units:
                    self._checkpoint_units(
                        run_id,
                        "visual_ocr_batch",
                        successful_units,
                    )
                visual_checkpoint_batches += 1
                self.repository.add_event(
                    run_id,
                    event_type="visual_ocr_batch_checkpoint",
                    stage="visual_ocr",
                    progress=33,
                    message=(
                        f"Batch Visual/OCR #{visual_checkpoint_batches} disimpan; "
                        f"{len(successful_units)} unit sukses dapat digunakan ulang."
                    ),
                    payload={
                        "batch": visual_checkpoint_batches,
                        "phase": str(checkpoint.get("phase") or "base"),
                        "attempted_unit_count": len(batch_keys),
                        "reusable_unit_count": len(successful_units),
                        "attempt_count": int(checkpoint.get("attempt_count") or 0),
                        "timeout_count": int(checkpoint.get("timeout_count") or 0),
                    },
                )

            visual_engine = (
                self.visual_ocr_engine
                if external_ai_allowed
                else VisualOCREngine(None, self.local_ocr_provider)
            )
            try:
                units, visual_result = visual_engine.run(
                    identity,
                    payload,
                    units,
                    max_units=self.settings.analysis_vision_max_units,
                    local_max_units=self.settings.analysis_local_ocr_max_units,
                    local_min_confidence=self.settings.analysis_local_ocr_min_confidence,
                    pdf_dpi=self.settings.analysis_pdf_render_dpi,
                    pdf_retry_dpi=self.settings.analysis_pdf_retry_render_dpi,
                    pdf_retry_max_units=self.settings.analysis_pdf_retry_max_units,
                    local_unit_budget_seconds=(
                        self.settings.analysis_local_ocr_unit_budget_seconds
                    ),
                    local_document_budget_seconds=(
                        self.settings.analysis_local_ocr_document_budget_seconds
                    ),
                    local_max_attempts_per_unit=(
                        self.settings.analysis_local_ocr_max_attempts_per_unit
                    ),
                    local_render_batch_units=(
                        self.settings.analysis_local_ocr_render_batch_units
                    ),
                    checkpoint_callback=persist_visual_checkpoint,
                    cancellation_check=cancellation_check,
                    timeout_seconds=self.settings.analysis_local_ocr_timeout_seconds,
                    office_rendering_enabled=self.settings.analysis_office_rendering_enabled,
                    office_page_expansion_enabled=mode == "full_audit",
                    office_render_max_pages=self.settings.analysis_office_render_max_pages,
                )
            except VisualCancellationRequested:
                cancelled = self._cancel_if_requested(
                    run_id, cancellation_check, "visual_ocr", 34
                )
                if cancelled:
                    return cancelled
                raise
            if resumed_artifacts and not visual_artifacts_complete:
                visual_result.input_refs.append(
                    f"partial-resume-run:{resume_from_run_id}"
                )
                visual_result.output["partial_resume_from_run_id"] = int(
                    resume_from_run_id or 0
                )
                visual_result.metrics["partial_resume"] = 1
                visual_result.warnings.append(
                    "Unit Visual/OCR sukses digunakan ulang dari durable batch checkpoint; "
                    "hanya unit belum selesai yang diproses kembali."
                )
                self.repository.add_event(
                    run_id,
                    event_type="visual_ocr_partial_resume",
                    stage="visual_ocr",
                    progress=32,
                    message=(
                        f"Visual/OCR melanjutkan checkpoint parsial dari run "
                        f"#{resume_from_run_id}."
                    ),
                    payload={"source_run_id": int(resume_from_run_id or 0)},
                )
        visual_result.output["durable_checkpoint_batches"] = (
            visual_checkpoint_batches
        )
        visual_review_application = {
            **visual_review_snapshot,
            "applied_count": 0,
            "applied_decision_ids": [],
            "stale_count": 0,
            "stale_decision_ids": [],
            "pending_count": 0,
            "pending_decision_ids": [],
        }
        if visual_review_snapshot.get("decision_count"):
            units, visual_review_application = self.repository.apply_visual_review_decisions(
                int(resume_from_run_id or 0),
                units,
            )
            visual_result.output.update({
                "visual_review_source_run_id": resume_from_run_id,
                "visual_review_checksum": visual_review_application.get("checksum"),
                "visual_review_applied": visual_review_application["applied_count"],
                "visual_review_stale": visual_review_application["stale_count"],
                "visual_review_pending": visual_review_application["pending_count"],
                "visual_semantics_pending": sum(
                    (unit.get("metadata") or {}).get("visual_semantics_status")
                    == "pending_review_or_vision"
                    for unit in units
                ),
            })
            visual_result.metrics["visual_review_applied"] = visual_review_application[
                "applied_count"
            ]
            if visual_review_application["stale_count"]:
                visual_result.warnings.append(
                    f"{visual_review_application['stale_count']} keputusan visual stale "
                    "tidak diterapkan karena checksum sumber berubah."
                )
            if (
                visual_review_application["applied_count"]
                and not visual_result.output["visual_semantics_pending"]
                and not any(unit.get("status") == "ocr_required" for unit in units)
            ):
                visual_result.status = EngineStatus.COMPLETED
            self.repository.add_event(
                run_id,
                event_type="visual_review_overlay_applied",
                stage="visual_ocr",
                progress=34,
                message=(
                    f"{visual_review_application['applied_count']} keputusan visual "
                    f"dari run #{resume_from_run_id} diterapkan pada run turunan."
                ),
                payload={
                    key: visual_review_application[key]
                    for key in (
                        "source_run_id", "checksum", "decision_count",
                        "actionable_count", "applied_count", "stale_count", "pending_count",
                    )
                },
            )
        self.repository.save_engine_result(run_id, visual_result)
        self.repository.save_document_units(run_id, units)
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="visual_ocr",
            progress=35,
            message=(
                f"Native Parsing membentuk {len(units)} unit; Visual/OCR memproses "
                f"{visual_result.output.get('ocr_processed', 0)} unit selektif."
            ),
            payload={
                "unit_count": len(units),
                "inventory": inventory,
                **visual_result.output,
                "warnings": visual_result.warnings,
            },
        )

        document_map, structure_result = self.document_structure_engine.run(identity, units, inventory)
        self.repository.save_document_structure(run_id, "document_map", document_map)
        self.repository.save_engine_result(run_id, structure_result)
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="document_structure",
            progress=43,
            message="Document Structure Engine selesai membentuk peta dokumen.",
            payload={"unit_count": len(units), "heading_count": len(document_map.get("headings") or [])},
        )

        ledger, coverage_result = self.coverage_engine.run(identity, units)
        self.repository.save_engine_result(run_id, coverage_result)
        downstream_reason = "Fact Extraction, Retrieval, Mapping, Grade, dan Verification Engine belum dijalankan."
        block_reasons = [*ledger["block_reasons"], downstream_reason]
        if router_result.warnings:
            block_reasons.extend(router_result.warnings)
        next_status = "inventory_complete" if ledger["coverage_status"] == "complete" else "coverage_partial"
        self.repository.update_run(
            run_id,
            status=next_status,
            total_units=ledger["total_units"],
            processed_units=ledger["processed_units"],
            failed_units=ledger["failed_units"],
            ocr_required_units=ledger["ocr_required_units"],
            coverage_percentage=ledger["coverage_percentage"],
            coverage_status=ledger["coverage_status"],
            primary_blocked=True,
            block_reasons_json=block_reasons,
        )
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="coverage",
            progress=50,
            message=(
                f"Coverage Engine: {ledger['processed_units']}/{ledger['total_units']} unit "
                f"({ledger['coverage_percentage']}%)."
            ),
            payload=ledger,
        )
        cancelled = self._cancel_if_requested(run_id, cancellation_check, "coverage", 50)
        if cancelled:
            return cancelled
        units, template_ledger, template_result = self.template_completeness_engine.run(
            identity, units
        )
        self.repository.save_document_units(run_id, units)
        self._checkpoint_units(run_id, "unit_preparation", units)
        self.repository.save_document_structure(run_id, "template_ledger", template_ledger)
        self.repository.save_engine_result(run_id, template_result)
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="template_completeness",
            progress=53,
            message=(
                f"Template Completeness Engine mengecualikan "
                f"{template_ledger['template_only_units']} unit template-only."
            ),
            payload=template_ledger,
        )
        self.repository.add_event(
            run_id,
            event_type="stage_started",
            stage="fact_extraction",
            progress=53,
            message="Fact Extraction Engine mulai membentuk fakta bersumber.",
        )
        stored_units = self.repository.list_document_units(run_id, include_text=True)
        reused_facts = (
            None
            if visual_review_snapshot.get("actionable_count")
            else self._copy_resumable_facts(
                resume_from_run_id,
                run_id,
                stored_units,
            )
        )
        if reused_facts is not None:
            extracted_facts = reused_facts
            fact_result = EngineResult(
                engine_name="fact_extraction",
                engine_version=PIPELINE_VERSION,
                status=EngineStatus.COMPLETED,
                input_checksum=identity.sha256,
                input_refs=[f"resume-run:{resume_from_run_id}"],
                output_refs=[f"fact:{fact['fact_key']}" for fact in reused_facts],
                coverage={"required": len(stored_units), "processed": len(stored_units), "failed": 0},
                warnings=[f"Fakta bersumber digunakan ulang dari run #{resume_from_run_id}."],
                metrics={"duration_ms": 0, "fact_count": len(reused_facts), "resumed_fact_count": len(reused_facts)},
                output={"fact_count": len(reused_facts), "resumed_from_run_id": resume_from_run_id},
            ).finish()
        else:
            extracted_facts, fact_result = self.fact_extraction_engine.run(identity, stored_units)
            if extracted_facts:
                self.repository.save_extracted_facts(run_id, extracted_facts)
        self.repository.save_engine_result(run_id, fact_result)
        covered_unit_keys = {
            str((fact.get("source") or {}).get("unit_key") or "")
            for fact in extracted_facts
        }
        ambiguous_units = (
            []
            if reused_facts is not None
            else [
                unit
                for unit in stored_units
                if is_fact_eligible_unit(unit)
                and str(unit.get("unit_key") or "") not in covered_unit_keys
            ]
        )
        fact_routing, fact_routing_result = self.compute_routing_engine.route_fact_extraction(
            identity,
            stored_units,
            ledger,
            ambiguous_units,
            requested_mode=mode,
            external_ai_allowed=external_ai_allowed,
            provider_available=bool(self.structured_provider),
            minimum_complexity=self.settings.analysis_routing_structured_min_complexity,
            resumed=reused_facts is not None,
        )
        self.repository.save_engine_result(run_id, fact_routing_result)
        self.repository.add_event(
            run_id,
            event_type="compute_route_selected",
            stage="fact_extraction",
            progress=56,
            message=(
                "Compute Routing memilih structured extraction untuk unit ambigu."
                if fact_routing["selected"]
                else "Compute Routing mempertahankan fact extraction deterministik."
            ),
            payload=fact_routing,
        )
        if fact_routing["selected"] and self.structured_provider:
            structured_facts, structured_result = StructuredFactExtractionEngine(
                self.structured_provider
            ).run(identity, ambiguous_units)
            self.repository.save_engine_result(run_id, structured_result)
            if structured_facts:
                self.repository.save_extracted_facts(run_id, structured_facts)
        saved_facts = self.repository.list_facts(run_id)
        fact_refs_by_unit: dict[str, list[str]] = {}
        for fact in saved_facts:
            for source in fact.get("sources") or []:
                fact_refs_by_unit.setdefault(str(source.get("unit_key") or ""), []).append(
                    f"fact:{fact['fact_key']}"
                )
        self._checkpoint_units(
            run_id,
            "fact_extraction",
            stored_units,
            output_refs_by_unit=fact_refs_by_unit,
        )
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="fact_extraction",
            progress=62,
            message=f"Fact Extraction Engine menghasilkan {len(saved_facts)} fakta bersumber.",
            payload=fact_result.output,
        )
        cancelled = self._cancel_if_requested(run_id, cancellation_check, "fact_extraction", 62)
        if cancelled:
            return cancelled

        parameters = self.repository.parameter_index()
        feedback_terms = self.repository.active_retrieval_feedback_terms()
        retrieved, retrieval_result = self.retrieval_engine.run(
            identity,
            saved_facts,
            parameters,
            feedback_terms=feedback_terms,
        )
        if (
            self.settings.analysis_advanced_rag_enabled
            and external_ai_allowed
            and self.rag_query_expansion_engine
            and retrieval_needs_model_expansion(
                retrieved,
                minimum_confidence=max(
                    0.0, min(1.0, self.settings.analysis_advanced_rag_min_confidence)
                ),
                ambiguity_margin=max(
                    0.0, min(1.0, self.settings.analysis_advanced_rag_ambiguity_margin)
                ),
            )
        ):
            retrieval_result.engine_name = "parameter_retrieval_local_baseline"
            self.repository.save_engine_result(run_id, retrieval_result)
            query_expansions, query_expansion_result = self.rag_query_expansion_engine.run(
                identity,
                saved_facts,
            )
            self.repository.save_engine_result(run_id, query_expansion_result)
            if query_expansions:
                retrieved, retrieval_result = self.retrieval_engine.run(
                    identity,
                    saved_facts,
                    parameters,
                    feedback_terms=feedback_terms,
                    query_expansions=query_expansions,
                )
            else:
                retrieval_result.engine_name = "parameter_retrieval"
        self.repository.save_engine_result(run_id, retrieval_result)
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="parameter_retrieval",
            progress=70,
            message=f"Retrieval Engine memilih {len(retrieved)} parameter dari {len(parameters)} parameter.",
            payload=retrieval_result.output,
        )

        mappings, mapping_result = self.mapping_engine.run(identity, saved_facts, retrieved)
        self.repository.save_engine_result(run_id, mapping_result)
        mapping_routing, mapping_routing_result = (
            self.compute_routing_engine.route_mapping_reasoning(
                identity,
                mappings,
                saved_facts,
                base_complexity_score=float(fact_routing["complexity_score"]),
                requested_mode=mode,
                external_ai_allowed=external_ai_allowed,
                provider_available=bool(self.mapping_reasoning_engine),
                ambiguity_margin=self.settings.analysis_routing_mapping_margin,
            )
        )
        self.repository.save_engine_result(run_id, mapping_routing_result)
        self.repository.add_event(
            run_id,
            event_type="compute_route_selected",
            stage="spip_mapping",
            progress=75,
            message=(
                "Compute Routing memilih constrained mapping reasoning untuk kandidat ambigu."
                if mapping_routing["selected"]
                else "Compute Routing mempertahankan mapping deterministik dan abstention/human review."
            ),
            payload=mapping_routing,
        )
        if mapping_routing["selected"] and self.mapping_reasoning_engine:
            mappings, mapping_advisory_result = self.mapping_reasoning_engine.run(
                identity,
                mappings,
                saved_facts,
                candidate_keys=mapping_routing["candidate_keys"],
            )
            self.repository.save_engine_result(run_id, mapping_advisory_result)
        saved_mappings = self.repository.save_mapping_candidates(run_id, mappings) if mappings else []
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="spip_mapping",
            progress=78,
            message=f"SPIP Mapping Engine menghasilkan {len(saved_mappings)} kandidat parameter.",
            payload=mapping_result.output,
        )
        cancelled = self._cancel_if_requested(run_id, cancellation_check, "spip_mapping", 78)
        if cancelled:
            return cancelled

        if mode == "screening":
            explainability, explain_result = self.explainability_engine.run(
                identity, ledger, saved_facts, saved_mappings, [], []
            )
            self.repository.save_document_structure(run_id, "explainability", explainability)
            self.repository.save_engine_result(run_id, explain_result)
            screening_reasons = [
                *ledger["block_reasons"],
                "Mode screening tidak menjalankan grade final atau controlled upload.",
            ]
            self.repository.update_run(
                run_id,
                status="screening_complete",
                primary_blocked=True,
                block_reasons_json=screening_reasons,
                finished_at=utc_now_iso(),
            )
            self.repository.add_event(
                run_id,
                event_type="run_completed",
                stage="screening",
                progress=100,
                message="Screening selesai; lanjutkan sebagai full audit untuk grading dan verification.",
            )
            return self.describe(run_id)

        retrieved_by_key = {
            (item["kk_id"], item["kode"], item["detail_kode"]): item
            for item in retrieved
        }
        mappings_for_rules = [
            {
                **mapping,
                "grades": (retrieved_by_key.get(
                    (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                    {},
                ).get("grades") or []),
            }
            for mapping in saved_mappings
        ]
        assessments, grade_result = self.grade_engine.run(
            identity,
            mappings_for_rules,
            saved_facts,
            self.repository.list_rule_approvals(),
        )
        self.repository.save_engine_result(run_id, grade_result)
        for assessment in assessments:
            self.repository.save_grade_assessment(run_id, assessment)
        saved_assessments = self.repository.list_grade_assessments(run_id)
        self.repository.add_event(
            run_id,
            event_type="stage_completed",
            stage="domain_rule_grade",
            progress=86,
            message=f"Domain Rule Engine membuat {len(saved_assessments)} assessment berstatus draft.",
            payload=grade_result.output,
        )

        verification_results, verification_engine_result = self.verification_engine.run(
            identity,
            ledger,
            saved_mappings,
            saved_assessments,
            saved_facts,
        )
        self.repository.save_engine_result(run_id, verification_engine_result)
        for verification in verification_results:
            self.repository.save_verification_result(run_id, verification)
        all_verification_results = list(verification_results)
        verification_routing, verification_routing_result = (
            self.compute_routing_engine.route_model_verification(
                identity,
                saved_mappings,
                saved_assessments,
                verification_results,
                ledger,
                base_complexity_score=float(mapping_routing["complexity_score"]),
                requested_mode=mode,
                external_ai_allowed=external_ai_allowed,
                provider_available=bool(self.model_verification_engine),
                minimum_risk=self.settings.analysis_routing_verifier_min_risk,
            )
        )
        self.repository.save_engine_result(run_id, verification_routing_result)
        self.repository.add_event(
            run_id,
            event_type="compute_route_selected",
            stage="verification",
            progress=91,
            message=(
                "Compute Routing memilih model verifier sebagai second-pass berisiko tinggi."
                if verification_routing["selected"]
                else "Compute Routing mempertahankan deterministic verifier atau human review."
            ),
            payload=verification_routing,
        )
        if verification_routing["selected"] and self.model_verification_engine:
            routed_mapping_ids = set(verification_routing["mapping_candidate_ids"])
            routed_mappings = [
                item for item in saved_mappings
                if int(item.get("id") or 0) in routed_mapping_ids
            ]
            routed_assessments = [
                item for item in saved_assessments
                if int(item.get("mapping_candidate_id") or 0) in routed_mapping_ids
            ]
            routed_deterministic_results = [
                item for item in verification_results
                if int(item.get("mapping_candidate_id") or 0) in routed_mapping_ids
            ]
            model_results, model_engine_result = self.model_verification_engine.run(
                identity,
                routed_mappings,
                routed_assessments,
                saved_facts,
                routed_deterministic_results,
            )
            self.repository.save_engine_result(run_id, model_engine_result)
            for verification in model_results:
                self.repository.save_verification_result(run_id, verification)
            all_verification_results.extend(model_results)
        verification_by_mapping: dict[int, list[dict]] = {}
        for item in all_verification_results:
            if item.get("mapping_candidate_id") is not None:
                verification_by_mapping.setdefault(int(item["mapping_candidate_id"]), []).append(item)
        verified_count = sum(
            bool(items) and all(item["status"] == "verified" for item in items)
            for items in verification_by_mapping.values()
        )
        explainability, explain_result = self.explainability_engine.run(
            identity,
            ledger,
            saved_facts,
            saved_mappings,
            saved_assessments,
            all_verification_results,
        )
        self.repository.save_document_structure(run_id, "explainability", explainability)
        self.repository.save_engine_result(run_id, explain_result)
        final_reasons = [*ledger["block_reasons"]]
        if not saved_mappings:
            final_reasons.append("Belum ada parameter yang dapat dipetakan; sistem abstain.")
        if saved_assessments and not all(item.get("primary_allowed") for item in saved_assessments):
            final_reasons.append("Rule parameter masih draft dan memerlukan pengesahan domain owner.")
        if verified_count != len(saved_mappings) or not saved_mappings:
            final_reasons.append("Independent Verification belum menyetujui seluruh mapping.")
        self.repository.update_run(
            run_id,
            status="review_required",
            primary_blocked=True,
            block_reasons_json=final_reasons,
            finished_at=utc_now_iso(),
        )
        self.repository.add_event(
            run_id,
            event_type="run_completed",
            stage="verification",
            progress=100,
            message=(
                f"Full audit tahap otomatis selesai: {verified_count}/{len(saved_mappings)} "
                "mapping terverifikasi; human review diperlukan."
            ),
            payload={"verified_count": verified_count, "mapping_count": len(saved_mappings)},
        )
        return self.describe(run_id)

    def _cancel_if_requested(
        self,
        run_id: int,
        cancellation_check: Callable[[], bool] | None,
        stage: str,
        progress: int,
    ) -> dict | None:
        if not cancellation_check or not cancellation_check():
            return None
        self.repository.update_run(
            run_id,
            status="cancelled",
            primary_blocked=True,
            block_reasons_json=["Analysis run dibatalkan oleh pengguna."],
            finished_at=utc_now_iso(),
        )
        self.repository.add_event(
            run_id,
            event_type="run_cancelled",
            stage=stage,
            progress=progress,
            message="Analysis run dibatalkan pada batas aman antar-engine.",
        )
        return self.describe(run_id)

    def _load_resumable_units(
        self,
        source_run_id: int | None,
        identity: DocumentIdentity,
        analysis_mode: str,
    ) -> tuple[list[dict], dict, bool] | None:
        if not source_run_id:
            return None
        source_run = self.repository.get_run(int(source_run_id))
        if not source_run:
            return None
        if source_run.get("sha256") != identity.sha256:
            return None
        if source_run.get("parser_version") != PARSER_VERSION:
            return None
        if source_run.get("configuration_hash") != configuration_hash(self.settings, analysis_mode):
            return None
        units = self.repository.list_document_units(int(source_run_id), include_text=True)
        final_checkpoints = self.repository.list_unit_checkpoints(
            int(source_run_id), "unit_preparation"
        )
        unit_keys = {str(unit.get("unit_key") or "") for unit in units}
        units_by_key = {
            str(unit.get("unit_key") or ""): unit for unit in units
        }

        def valid_checkpoint_keys(checkpoints: list[dict]) -> set[str]:
            valid: set[str] = set()
            for item in checkpoints:
                key = str(item.get("unit_key") or "")
                unit = units_by_key.get(key)
                if (
                    item.get("status") == "completed"
                    and unit
                    and str(item.get("input_checksum") or "")
                    == self._unit_checkpoint_checksum(unit)
                ):
                    valid.add(key)
            return valid

        final_completed_keys = valid_checkpoint_keys(final_checkpoints)
        if not units:
            return None
        final_checkpoint_keys = {
            str(item.get("unit_key") or "") for item in final_checkpoints
        }
        visual_complete = (
            unit_keys == final_completed_keys == final_checkpoint_keys
        )
        if not visual_complete:
            manifest_checkpoints = self.repository.list_unit_checkpoints(
                int(source_run_id), "visual_ocr_manifest"
            )
            manifest_keys = valid_checkpoint_keys(manifest_checkpoints)
            manifest_checkpoint_keys = {
                str(item.get("unit_key") or "") for item in manifest_checkpoints
            }
            batch_checkpoints = self.repository.list_unit_checkpoints(
                int(source_run_id), "visual_ocr_batch"
            )
            reusable_batch_keys = valid_checkpoint_keys(batch_checkpoints)
            if (
                manifest_keys != unit_keys
                or manifest_checkpoint_keys != unit_keys
                or not reusable_batch_keys
                or not reusable_batch_keys.issubset(unit_keys)
                or any(
                    (units_by_key[key].get("status") not in {"processed", "partial"})
                    for key in reusable_batch_keys
                )
            ):
                return None
        inventory = next(
            (
                (item.get("output") or {}).get("inventory") or {}
                for item in reversed(self.repository.list_engine_results(int(source_run_id)))
                if item.get("engine_name") == "native_parsing"
            ),
            {},
        )
        reusable = []
        for unit in units:
            reusable.append({
                key: value
                for key, value in unit.items()
                if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
            })
        return reusable, inventory, visual_complete

    def _copy_resumable_facts(
        self,
        source_run_id: int | None,
        target_run_id: int,
        target_units: list[dict],
    ) -> list[dict] | None:
        if not source_run_id:
            return None
        checkpoints = self.repository.list_unit_checkpoints(
            int(source_run_id), "fact_extraction"
        )
        completed_keys = {
            str(item["unit_key"])
            for item in checkpoints
            if item.get("status") == "completed"
        }
        target_by_key = {
            str(unit.get("unit_key") or ""): unit for unit in target_units
        }
        if not checkpoints or completed_keys != set(target_by_key):
            return None
        copied = []
        for fact in self.repository.list_facts(int(source_run_id)):
            sources = fact.get("sources") or []
            source = sources[0] if sources else None
            target_unit = target_by_key.get(str((source or {}).get("unit_key") or ""))
            if not source or not target_unit:
                return None
            copied.append({
                "fact_key": fact["fact_key"],
                "claim": fact["claim"],
                "fact_type": fact.get("fact_type") or "unknown",
                "evidence_role": fact.get("evidence_role") or "context",
                "evidence_role_method": fact.get("evidence_role_method") or "legacy_default_v1",
                "organization": fact.get("organization"),
                "period": fact.get("period"),
                "confidence": fact.get("confidence"),
                "extraction_method": f"resume:{fact.get('extraction_method') or 'unknown'}",
                "status": fact.get("status") or "extracted",
                "source": {
                    "unit_id": target_unit["id"],
                    "unit_key": target_unit["unit_key"],
                    "source_location": source.get("source_location") or {},
                    "source_quote": source.get("source_quote") or fact["claim"],
                },
            })
        if copied:
            self.repository.save_extracted_facts(target_run_id, copied)
        return copied

    def _checkpoint_units(
        self,
        run_id: int,
        stage: str,
        units: list[dict],
        *,
        output_refs_by_unit: dict[str, list[str]] | None = None,
    ) -> None:
        for unit in units:
            unit_key = str(unit.get("unit_key") or "")
            checksum = self._unit_checkpoint_checksum(unit)
            self.repository.save_unit_checkpoint(
                run_id,
                unit_key=unit_key,
                stage=stage,
                status="failed" if unit.get("status") == "failed" else "completed",
                input_checksum=checksum,
                output_refs=(output_refs_by_unit or {}).get(unit_key, []),
                error_message=("Unit gagal diproses." if unit.get("status") == "failed" else None),
            )

    @staticmethod
    def _unit_checkpoint_checksum(unit: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "unit_key": str(unit.get("unit_key") or ""),
                    "unit_type": str(unit.get("unit_type") or ""),
                    "ordinal": int(unit.get("ordinal") or 0),
                    "heading_path": unit.get("heading_path") or [],
                    "status": unit.get("status"),
                    "source_location": unit.get("source_location") or {},
                    "text": str(unit.get("text") or ""),
                    "warnings": unit.get("warnings") or [],
                    "metadata": unit.get("metadata") or {},
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    def expand_candidates(self, run_id: int, *, limit: int = 30) -> dict:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("analysis_mode") != "full_audit" or run.get("coverage_status") != "complete":
            raise ValueError("Candidate expansion hanya tersedia untuk full audit dengan coverage lengkap.")
        safe_limit = max(11, min(50, int(limit or 30)))
        identity = self._identity_for_run(run_id, run)
        facts = self.repository.list_facts(run_id)
        if not facts:
            raise ValueError("Candidate expansion membutuhkan fakta bersumber.")
        existing = self.repository.list_mapping_candidates(run_id)
        existing_keys = {
            (item["kk_id"], item["kode"], item["detail_kode"])
            for item in existing
        }
        retrieved, retrieval_result = self.retrieval_engine.run(
            identity,
            facts,
            self.repository.parameter_index(),
            limit=safe_limit,
            feedback_terms=self.repository.active_retrieval_feedback_terms(),
        )
        if (
            self.settings.analysis_advanced_rag_enabled
            and bool(run.get("external_ai_allowed", True))
            and self.rag_query_expansion_engine
            and retrieval_needs_model_expansion(
                retrieved,
                minimum_confidence=max(
                    0.0, min(1.0, self.settings.analysis_advanced_rag_min_confidence)
                ),
                ambiguity_margin=max(
                    0.0, min(1.0, self.settings.analysis_advanced_rag_ambiguity_margin)
                ),
            )
        ):
            retrieval_result.engine_name = "parameter_retrieval_expansion_local_baseline"
            retrieval_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}:local"
            self.repository.save_engine_result(run_id, retrieval_result)
            query_expansions, query_result = self.rag_query_expansion_engine.run(identity, facts)
            query_result.engine_name = "advanced_rag_query_expansion_candidate_expansion"
            query_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}"
            self.repository.save_engine_result(run_id, query_result)
            if query_expansions:
                retrieved, retrieval_result = self.retrieval_engine.run(
                    identity,
                    facts,
                    self.repository.parameter_index(),
                    limit=safe_limit,
                    feedback_terms=self.repository.active_retrieval_feedback_terms(),
                    query_expansions=query_expansions,
                )
        candidates, mapping_result = self.mapping_engine.run(identity, facts, retrieved)
        retrieval_result.engine_name = "parameter_retrieval_expansion"
        retrieval_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}"
        retrieval_result.output["requested_limit"] = safe_limit
        mapping_result.engine_name = "spip_mapping_expansion"
        mapping_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}"
        prior_fact_routing = next(
            (
                item.get("output") or {}
                for item in reversed(self.repository.list_engine_results(run_id))
                if item.get("engine_name") == "compute_routing_fact"
            ),
            {},
        )
        expansion_routing, expansion_routing_result = (
            self.compute_routing_engine.route_mapping_reasoning(
                identity,
                candidates,
                facts,
                base_complexity_score=float(
                    prior_fact_routing.get("complexity_score") or 0
                ),
                requested_mode=str(run.get("analysis_mode") or "full_audit"),
                external_ai_allowed=bool(run.get("external_ai_allowed", True)),
                provider_available=bool(self.mapping_reasoning_engine),
                ambiguity_margin=self.settings.analysis_routing_mapping_margin,
            )
        )
        expansion_routing_result.engine_name = "compute_routing_mapping_expansion"
        expansion_routing_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}"
        if expansion_routing["selected"] and self.mapping_reasoning_engine:
            candidates, advisory_result = self.mapping_reasoning_engine.run(
                identity,
                candidates,
                facts,
                candidate_keys=expansion_routing["candidate_keys"],
            )
            advisory_result.engine_name = "constrained_mapping_reasoning_expansion"
            advisory_result.input_checksum = f"{identity.sha256}:limit:{safe_limit}"
            self.repository.save_engine_result(run_id, advisory_result)
        saved = self.repository.save_mapping_candidates(run_id, candidates)
        self.repository.save_engine_result(run_id, retrieval_result)
        self.repository.save_engine_result(run_id, mapping_result)
        self.repository.save_engine_result(run_id, expansion_routing_result)
        new_keys = {
            (item["kk_id"], item["kode"], item["detail_kode"])
            for item in saved
        } - existing_keys
        self.repository.add_event(
            run_id,
            event_type="candidate_expansion_completed",
            stage="mapping",
            progress=90,
            message=f"Candidate expansion selesai: {len(new_keys)} kandidat baru dari limit {safe_limit}.",
            payload={
                "requested_limit": safe_limit,
                "new_candidate_count": len(new_keys),
                "mapping_route": expansion_routing,
            },
        )
        result = self.reverify(run_id)
        result["candidate_expansion"] = {
            "requested_limit": safe_limit,
            "previous_count": len(existing_keys),
            "current_count": len(self.repository.list_mapping_candidates(run_id)),
            "new_candidate_count": len(new_keys),
        }
        return result

    def reverify(self, run_id: int) -> dict:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run.get("analysis_mode") != "full_audit" or run.get("coverage_status") != "complete":
            raise ValueError("Re-verification hanya tersedia untuk full audit dengan coverage lengkap.")
        identity = self._identity_for_run(run_id, run)
        mappings = self.repository.list_mapping_candidates(run_id)
        facts = self.repository.list_facts(run_id)
        parameter_by_key = {
            (item["kk_id"], item["kode"], item["detail_kode"]): item
            for item in self.repository.parameter_index()
        }
        retrieved_existing = []
        for mapping in mappings:
            parameter = parameter_by_key.get(
                (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                {},
            )
            if not parameter:
                continue
            corpus_tokens = parameter_corpus_tokens(parameter)
            if self.settings.analysis_advanced_rag_enabled:
                corpus_tokens = expand_domain_tokens(corpus_tokens)
            retrieved_existing.append({
                "parameter_id": parameter["id"],
                "kk_id": parameter["kk_id"],
                "kode": parameter["kode"],
                "detail_kode": parameter["detail_kode"],
                "subunsur_name": parameter.get("subunsur_name"),
                "uraian": parameter.get("uraian"),
                "grades": parameter.get("grades") or [],
                "retrieval_score": mapping.get("retrieval_score") or 0,
                "corpus_tokens": sorted(corpus_tokens),
                "retrieval_components": {
                    "semantic_vector": 0.0,
                } if self.settings.analysis_advanced_rag_enabled else {},
            })
        if retrieved_existing:
            refreshed, support_refresh_result = self.mapping_engine.run(
                identity,
                facts,
                retrieved_existing,
            )
            support_refresh_result.engine_name = "spip_mapping_support_refresh"
            refreshed_by_key = {
                (item["kk_id"], item["kode"], item["detail_kode"]): item
                for item in refreshed
            }
            merged_mappings = []
            for mapping in mappings:
                refreshed_mapping = refreshed_by_key.get(
                    (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                    {},
                )
                if not refreshed_mapping:
                    merged_mappings.append(mapping)
                    continue
                merged_mappings.append({
                    **mapping,
                    "supporting_fact_ids": refreshed_mapping.get("supporting_fact_ids") or [],
                    "reasons": list(dict.fromkeys([
                        *(mapping.get("reasons") or []),
                        *(refreshed_mapping.get("reasons") or []),
                    ])),
                    "missing_evidence": [
                        item for item in (mapping.get("missing_evidence") or [])
                        if item != "Belum ada fakta bersumber yang mendukung parameter."
                    ] if refreshed_mapping.get("supporting_fact_ids") else (
                        mapping.get("missing_evidence") or []
                    ),
                })
            self.repository.save_mapping_candidates(run_id, merged_mappings)
            self.repository.save_engine_result(run_id, support_refresh_result)
            mappings = self.repository.list_mapping_candidates(run_id)
        mappings_for_rules = [
            {
                **mapping,
                "grades": parameter_by_key.get(
                    (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]), {}
                ).get("grades") or [],
            }
            for mapping in mappings
        ]
        assessments, grade_result = self.grade_engine.run(
            identity,
            mappings_for_rules,
            facts,
            self.repository.list_rule_approvals(),
        )
        ledger = {
            "coverage_status": run.get("coverage_status"),
            "coverage_percentage": run.get("coverage_percentage"),
            "total_units": run.get("total_units"),
            "processed_units": run.get("processed_units"),
        }
        deterministic_results, verification_result = self.verification_engine.run(
            identity, ledger, mappings, assessments, facts
        )
        all_results = list(deterministic_results)
        model_engine_result = None
        prior_routing = next(
            (
                item.get("output") or {}
                for item in reversed(self.repository.list_engine_results(run_id))
                if item.get("engine_name") in {
                    "compute_routing_mapping",
                    "compute_routing_mapping_expansion",
                }
            ),
            {},
        )
        verification_routing, verification_routing_result = (
            self.compute_routing_engine.route_model_verification(
                identity,
                mappings,
                assessments,
                deterministic_results,
                ledger,
                base_complexity_score=float(
                    prior_routing.get("complexity_score") or 0
                ),
                requested_mode=str(run.get("analysis_mode") or "full_audit"),
                external_ai_allowed=bool(run.get("external_ai_allowed", True)),
                provider_available=bool(self.model_verification_engine),
                minimum_risk=self.settings.analysis_routing_verifier_min_risk,
            )
        )
        verification_routing_result.engine_name = "compute_routing_verification_reverify"
        self.repository.save_engine_result(run_id, verification_routing_result)
        if verification_routing["selected"] and self.model_verification_engine:
            routed_ids = set(verification_routing["mapping_candidate_ids"])
            model_results, model_engine_result = self.model_verification_engine.run(
                identity,
                [item for item in mappings if int(item.get("id") or 0) in routed_ids],
                [
                    item for item in assessments
                    if int(item.get("mapping_candidate_id") or 0) in routed_ids
                ],
                facts,
                [
                    item for item in deterministic_results
                    if int(item.get("mapping_candidate_id") or 0) in routed_ids
                ],
            )
            all_results.extend(model_results)

        self.repository.supersede_active_assessments(run_id)
        self.repository.save_engine_result(run_id, grade_result)
        self.repository.save_engine_result(run_id, verification_result)
        if model_engine_result:
            self.repository.save_engine_result(run_id, model_engine_result)
        for assessment in assessments:
            self.repository.save_grade_assessment(run_id, assessment)
        for verification in all_results:
            self.repository.save_verification_result(run_id, verification)

        grouped: dict[int, list[dict]] = {}
        for item in all_results:
            grouped.setdefault(int(item["mapping_candidate_id"]), []).append(item)
        verified_count = sum(
            bool(items) and all(item["status"] == "verified" for item in items)
            for items in grouped.values()
        )
        explainability, explain_result = self.explainability_engine.run(
            identity, ledger, facts, mappings, assessments, all_results
        )
        self.repository.save_document_structure(run_id, "explainability", explainability)
        self.repository.save_engine_result(run_id, explain_result)
        reasons = []
        if not assessments or not all(item.get("primary_allowed") for item in assessments):
            reasons.append("Sebagian rule parameter belum disahkan atau checksum approval sudah stale.")
        if verified_count != len(mappings) or not mappings:
            reasons.append("Independent Verification belum menyetujui seluruh mapping aktif.")
        self.repository.update_run(
            run_id,
            status="review_required",
            primary_blocked=True,
            block_reasons_json=reasons,
            finished_at=utc_now_iso(),
        )
        self.repository.add_event(
            run_id,
            event_type="reverification_completed",
            stage="verification",
            progress=100,
            message=f"Re-verification selesai: {verified_count}/{len(mappings)} mapping verified.",
            payload={
                "verified_count": verified_count,
                "mapping_count": len(mappings),
                "rule_approval_count": len(self.repository.list_rule_approvals()),
                "verification_route": verification_routing,
            },
        )
        return self.describe(run_id)

    def _identity_for_run(self, run_id: int, run: dict) -> DocumentIdentity:
        return DocumentIdentity(
            file_name=run["file_name"],
            content_type=run.get("content_type"),
            size_bytes=int(run.get("size_bytes") or 0),
            sha256=run["sha256"],
            file_kind=str(
                next(
                    (
                        item.get("output", {}).get("file_kind")
                        for item in self.repository.list_engine_results(run_id)
                        if item.get("engine_name") == "file_router"
                    ),
                    "text",
                )
            ),
        )

    def describe(self, run_id: int) -> dict:
        run = self.repository.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        parameters = {
            (item["kk_id"], item["kode"], item["detail_kode"]): item
            for item in self.repository.parameter_index()
        }
        mappings = []
        for mapping in self.repository.list_mapping_candidates(run_id):
            parameter = parameters.get(
                (mapping["kk_id"], mapping["kode"], mapping["detail_kode"]),
                {},
            )
            mappings.append({
                **mapping,
                "parameter_uraian": parameter.get("uraian"),
                "subunsur_name": parameter.get("subunsur_name"),
                "cara_pengujian": parameter.get("cara_pengujian"),
            })
        return {
            "run": run,
            "events": self.repository.list_events(run_id),
            "engines": self.repository.list_engine_results(run_id),
            "security_findings": self.repository.list_security_findings(run_id),
            "document_units": self.repository.list_document_units(run_id),
            "document_structures": self.repository.list_document_structures(run_id),
            "explainability": next(
                (
                    item.get("structure")
                    for item in reversed(self.repository.list_document_structures(run_id))
                    if item.get("structure_type") == "explainability"
                ),
                None,
            ),
            "facts": self.repository.list_facts(run_id),
            "mappings": mappings,
            "grade_assessments": self.repository.list_grade_assessments(run_id),
            "verification_results": self.repository.list_verification_results(run_id),
            "human_review_decisions": self.repository.list_human_review_decisions(run_id),
            "controlled_upload_actions": self.repository.list_controlled_upload_actions(run_id),
            "checkpoint_summary": self.repository.checkpoint_summary(run_id),
        }
