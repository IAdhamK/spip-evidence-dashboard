from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.analysis.domain.cross_document import CrossDocumentSynthesisEngine
from app.analysis.repository import AnalysisRepository
from app.analysis.reviewer_identity import authorize_api_request
from app.analysis import PIPELINE_VERSION
from app.config import get_settings
from app.database import Database


class CreateAnalysisPackageRequest(BaseModel):
    name: str = Field(default="Paket Evidence", min_length=3, max_length=200)
    run_ids: list[int] = Field(min_length=2, max_length=50)
    organization: str | None = Field(default=None, max_length=200)
    period: str | None = Field(default=None, max_length=40)


class PackageReviewDecisionRequest(BaseModel):
    reviewer_id: str = Field(min_length=2, max_length=120)
    decision: str = Field(pattern="^(approve_synthesis|reject)$")
    reason: str = Field(min_length=8, max_length=2000)


def create_package_router(db: Database) -> APIRouter:
    router = APIRouter(prefix="/api/analysis-packages", tags=["Document Intelligence Packages"])

    def require_package_access(request: Request) -> None:
        settings = get_settings()
        if settings.analysis_require_reviewer_identity or settings.analysis_require_reviewer_role:
            authorize_api_request(request, settings, "")

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create_package(payload: CreateAnalysisPackageRequest, request: Request) -> dict:
        require_package_access(request)
        if not get_settings().analysis_pipeline_v2_enabled:
            raise HTTPException(status_code=403, detail="Document Intelligence Pipeline V2 belum diaktifkan.")
        repository = AnalysisRepository(db)
        try:
            package_id = repository.create_package(
                name=payload.name,
                run_ids=payload.run_ids,
                organization=payload.organization,
                period=payload.period,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return CrossDocumentSynthesisEngine(repository).run(package_id)

    @router.get("/{package_id}")
    def package_detail(package_id: int, request: Request) -> dict:
        require_package_access(request)
        package = AnalysisRepository(db).get_package(package_id)
        if not package:
            raise HTTPException(status_code=404, detail="Paket analysis tidak ditemukan.")
        return package

    @router.post("/{package_id}/review-decisions", status_code=status.HTTP_201_CREATED)
    def review_package(
        package_id: int,
        payload: PackageReviewDecisionRequest,
        request: Request,
    ) -> dict:
        require_package_access(request)
        repository = AnalysisRepository(db)
        package = repository.get_package(package_id)
        if not package:
            raise HTTPException(status_code=404, detail="Paket analysis tidak ditemukan.")
        assessments = package.get("assessments") or []
        if payload.decision == "approve_synthesis":
            if not assessments:
                raise HTTPException(status_code=409, detail="Paket belum memiliki assessment untuk direview.")
            if any(item.get("contradictions") for item in assessments):
                raise HTTPException(
                    status_code=409,
                    detail="Synthesis tidak dapat disetujui selama kontradiksi scope masih ada.",
                )
        decision_id = repository.save_package_review_decision(
            package_id,
            {
                "reviewer_id": authorize_api_request(
                    request,
                    get_settings(),
                    payload.reviewer_id,
                ),
                "decision": payload.decision,
                "reason": payload.reason.strip(),
                "assessment_snapshot": assessments,
                "pipeline_version": PIPELINE_VERSION,
            },
        )
        if payload.decision == "reject":
            repository.update_package(
                package_id,
                status="rejected",
                primary_blocked=True,
                block_reasons_json=[payload.reason.strip()],
            )
        else:
            repository.update_package(
                package_id,
                status="synthesis_approved",
                primary_blocked=True,
                block_reasons_json=[
                    "Synthesis telah direview, tetapi package rule/controlled upload belum disahkan."
                ],
            )
        repository.save_package_engine_result(
            package_id,
            engine_name="human_review",
            engine_version=PIPELINE_VERSION,
            status="completed",
            output={"latest_decision_id": decision_id, "decision": payload.decision},
            warnings=[],
            metrics={"decision_count": len((repository.get_package(package_id) or {}).get("review_decisions") or [])},
        )
        return {"decision_id": decision_id, "package": repository.get_package(package_id)}

    return router
