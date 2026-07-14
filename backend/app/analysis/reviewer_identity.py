from __future__ import annotations

import re

from fastapi import HTTPException, Request

from app.config import Settings


TRUSTED_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+:-]{1,119}$")
TRUSTED_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
ANALYSIS_ADMIN_ROLE = "analysis_admin"
ALLOWED_REVIEWER_ROLES = frozenset({
    ANALYSIS_ADMIN_ROLE,
    "domain_owner",
    "evaluation_owner",
    "evidence_reviewer",
    "operations_owner",
    "release_owner",
    "vision_owner",
})

EVIDENCE_REVIEW_ROLES = frozenset({"evidence_reviewer"})
DOMAIN_RULE_ROLES = frozenset({"domain_owner"})
EXPERT_DATASET_ROLES = frozenset({"domain_owner", "evaluation_owner"})
EVALUATION_ROLES = frozenset({"evaluation_owner"})
VISION_GOVERNANCE_ROLES = frozenset({"vision_owner"})
RELEASE_ROLES = frozenset({"release_owner"})
OPERATIONS_ROLES = frozenset({"operations_owner"})
ANALYSIS_OPERATION_ROLES = frozenset({"evidence_reviewer", "operations_owner"})

AUTHORIZATION_POLICY_VERSION = "analysis-rbac-v1"


def _operation(method: str, path: str) -> tuple[str, str]:
    return method.upper(), path


SECURED_OPERATION_ROLES: dict[tuple[str, str], frozenset[str]] = {
    # Intake, durable jobs, run artifacts, and review operations.
    _operation("POST", "/api/analysis-runs"): EVIDENCE_REVIEW_ROLES,
    _operation("POST", "/api/analysis-runs/batch-intakes"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/batch-intakes/recent"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/batch-intakes/{batch_id}"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/batch-intakes/{batch_id}/cancel"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/jobs/{job_id}"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/jobs/{job_id}/cancel"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/events"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/events/stream"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/units"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/checkpoints"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/document-map"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/facts"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/mappings"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/retry"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/cancel"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/expand-candidates"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/reverify"): ANALYSIS_OPERATION_ROLES,
    _operation("GET", "/api/analysis-runs/{run_id}/review-decisions"): ANALYSIS_OPERATION_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/review-decisions"): EVIDENCE_REVIEW_ROLES,
    # Guided and visual review contain document/source material.
    _operation("GET", "/api/analysis-runs/guided-review/parameters"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/guided-review/queue"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/guided-review/export"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/guided-review/{run_id}"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/guided-review/{run_id}/document"): EVIDENCE_REVIEW_ROLES,
    _operation("POST", "/api/analysis-runs/guided-review/{run_id}"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/visual-review/queue"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/visual-review/{run_id}/{unit_key}"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-runs/visual-review/{run_id}/{unit_key}/preview"): EVIDENCE_REVIEW_ROLES,
    _operation("POST", "/api/analysis-runs/visual-review/{run_id}/{unit_key}/decision"): EVIDENCE_REVIEW_ROLES,
    _operation("POST", "/api/analysis-runs/visual-review/{run_id}/apply"): EVIDENCE_REVIEW_ROLES,
    # Domain, expert dataset, vision, evaluation, and release authority are separate.
    _operation("POST", "/api/analysis-runs/rule-approvals"): DOMAIN_RULE_ROLES,
    _operation("GET", "/api/analysis-runs/governance/rules"): DOMAIN_RULE_ROLES,
    _operation("GET", "/api/analysis-runs/governance/rules/history"): DOMAIN_RULE_ROLES,
    _operation("POST", "/api/analysis-runs/governance/rules/decisions"): DOMAIN_RULE_ROLES,
    _operation("GET", "/api/analysis-runs/governance/expert-dataset"): EXPERT_DATASET_ROLES,
    _operation("POST", "/api/analysis-runs/governance/expert-dataset/{run_id}/decision"): EXPERT_DATASET_ROLES,
    _operation("GET", "/api/analysis-runs/governance/vision"): VISION_GOVERNANCE_ROLES,
    _operation("POST", "/api/analysis-runs/governance/vision/probe"): VISION_GOVERNANCE_ROLES,
    _operation("POST", "/api/analysis-runs/governance/vision/decisions"): VISION_GOVERNANCE_ROLES,
    _operation("GET", "/api/analysis-runs/evaluation-reports"): EVALUATION_ROLES,
    _operation("POST", "/api/analysis-runs/evaluation-reports"): EVALUATION_ROLES,
    _operation("POST", "/api/analysis-runs/evaluation-reports/from-expert-gold"): EVALUATION_ROLES,
    _operation("GET", "/api/analysis-runs/shadow-comparison"): RELEASE_ROLES,
    _operation("POST", "/api/analysis-runs/shadow-comparisons/refresh"): RELEASE_ROLES,
    _operation("GET", "/api/analysis-runs/shadow-comparison-report"): RELEASE_ROLES,
    _operation("GET", "/api/analysis-runs/release-evidence"): RELEASE_ROLES,
    _operation("POST", "/api/analysis-runs/release-evidence"): RELEASE_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/controlled-upload"): RELEASE_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/approve-upload"): RELEASE_ROLES,
    _operation("POST", "/api/analysis-runs/{run_id}/controlled-upload-actions/{action_id}/reconciliation"): OPERATIONS_ROLES,
    # Cross-document packages contain reviewed source/run material.
    _operation("POST", "/api/analysis-packages"): EVIDENCE_REVIEW_ROLES,
    _operation("GET", "/api/analysis-packages/{package_id}"): EVIDENCE_REVIEW_ROLES,
    _operation("POST", "/api/analysis-packages/{package_id}/review-decisions"): EVIDENCE_REVIEW_ROLES,
}

PROXY_BOUNDARY_OPERATIONS: dict[tuple[str, str], str] = {
    _operation("GET", "/api/analysis-runs/config"): "authenticated_proxy",
    _operation("GET", "/api/analysis-runs/parameter-catalog"): "authenticated_proxy",
    _operation("GET", "/api/analysis-runs/rule-catalog"): "authenticated_proxy",
    _operation("GET", "/api/analysis-runs/promotion-readiness"): "authenticated_proxy",
    _operation("GET", "/api/analysis-runs/readiness-dashboard"): "authenticated_proxy",
    _operation("GET", "/api/analysis-runs/metrics"): "internal_network",
    _operation("GET", "/api/analysis-runs/metrics/prometheus"): "internal_network",
}


def resolve_reviewer_identity(
    request: Request,
    settings: Settings,
    provided_reviewer_id: str,
) -> str:
    """Bind review audit identity to a trusted reverse-proxy header when configured."""
    header_name = settings.analysis_reviewer_identity_header.strip() or "X-Reviewer-Identity"
    trusted_identity = str(request.headers.get(header_name) or "").strip()
    provided = provided_reviewer_id.strip()
    if trusted_identity and not TRUSTED_IDENTITY_PATTERN.fullmatch(trusted_identity):
        raise HTTPException(
            status_code=401,
            detail="Identitas reviewer terautentikasi memiliki format yang tidak aman.",
        )
    if settings.analysis_require_reviewer_identity and not trusted_identity:
        raise HTTPException(
            status_code=401,
            detail=f"Identitas reviewer terautentikasi wajib tersedia pada header {header_name}.",
        )
    if trusted_identity and provided and trusted_identity != provided:
        raise HTTPException(
            status_code=403,
            detail="Reviewer ID pada payload tidak sesuai identitas terautentikasi.",
        )
    return trusted_identity or provided


def resolve_reviewer_roles(request: Request, settings: Settings) -> frozenset[str]:
    """Read an application-specific role claim that only the trusted proxy may set."""
    if not settings.analysis_require_reviewer_role:
        return frozenset()
    if not settings.analysis_require_reviewer_identity:
        raise HTTPException(
            status_code=503,
            detail="Konfigurasi otorisasi reviewer tidak aman.",
        )
    header_name = settings.analysis_reviewer_role_header.strip() or "X-Reviewer-Roles"
    raw = str(request.headers.get(header_name) or "").strip()
    if not raw or len(raw) > 512:
        raise HTTPException(
            status_code=403,
            detail="Peran reviewer terautentikasi tidak tersedia.",
        )
    roles = [item.strip() for item in raw.split(",") if item.strip()]
    if not roles or any(
        not TRUSTED_ROLE_PATTERN.fullmatch(role) or role not in ALLOWED_REVIEWER_ROLES
        for role in roles
    ):
        raise HTTPException(
            status_code=403,
            detail="Peran reviewer terautentikasi tidak valid.",
        )
    return frozenset(roles)


def authorize_reviewer(
    request: Request,
    settings: Settings,
    provided_reviewer_id: str,
    required_roles: frozenset[str],
) -> str:
    """Bind identity and enforce least-privilege roles when production RBAC is enabled."""
    identity = resolve_reviewer_identity(request, settings, provided_reviewer_id)
    if not settings.analysis_require_reviewer_role:
        return identity
    roles = resolve_reviewer_roles(request, settings)
    if ANALYSIS_ADMIN_ROLE not in roles and not roles.intersection(required_roles):
        raise HTTPException(
            status_code=403,
            detail="Peran reviewer tidak mengizinkan tindakan ini.",
        )
    return identity


def api_operation_key(request: Request) -> tuple[str, str] | None:
    route = request.scope.get("route")
    route_path = str(getattr(route, "path", "") or "")
    if not route_path:
        return None
    return _operation(request.method, route_path)


def authorize_api_request(
    request: Request,
    settings: Settings,
    provided_reviewer_id: str,
) -> str:
    """Enforce the centralized role contract for a matched V2 API route."""
    operation = api_operation_key(request)
    required_roles = SECURED_OPERATION_ROLES.get(operation) if operation else None
    if required_roles is None:
        raise HTTPException(
            status_code=503,
            detail="Kontrak otorisasi endpoint belum terdaftar.",
        )
    return authorize_reviewer(
        request,
        settings,
        provided_reviewer_id,
        required_roles,
    )


def authorization_contract_summary() -> dict[str, object]:
    return {
        "policy_version": AUTHORIZATION_POLICY_VERSION,
        "secured_operation_count": len(SECURED_OPERATION_ROLES),
        "proxy_boundary_operation_count": len(PROXY_BOUNDARY_OPERATIONS),
        "classified_operation_count": (
            len(SECURED_OPERATION_ROLES) + len(PROXY_BOUNDARY_OPERATIONS)
        ),
        "all_mutations_role_secured": all(
            method != "POST" for method, _path in PROXY_BOUNDARY_OPERATIONS
        ),
    }
