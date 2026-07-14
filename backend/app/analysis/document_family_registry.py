from __future__ import annotations

from typing import Iterable


DOCUMENT_FAMILIES = frozenset({
    "risk_matrix",
    "monitoring_report",
    "risk_policy",
    "review_audit",
    "transmittal_letter",
    "meeting_invitation",
    "meeting_minutes",
    "photo_documentation",
    "template_form",
    "unknown",
})

DOCUMENT_EVIDENCE_ROLES = frozenset({"primary", "supporting", "optional", "reject"})

RISK_MATRIX_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "objective": ("sasaran", "tujuan"),
    "indicator": ("indikator",),
    "risk_statement": ("pernyataan risiko", "uraian risiko", "kejadian risiko", "risiko"),
    "cause": ("penyebab", "sebab"),
    "impact": ("pihak terdampak", "nilai dampak", "dampak"),
    "risk_owner": ("pemilik risiko", "risk owner", "penanggung jawab", "pic"),
    "likelihood": ("kemungkinan", "probabilitas", "likelihood"),
    "risk_level": ("level risiko", "tingkat risiko", "prioritas risiko"),
    "control_plan": (
        "rencana tindak pengendalian",
        "rencana pengendalian",
        "rtp",
        "mitigasi",
    ),
    "target_time": ("target waktu", "batas waktu", "waktu pelaksanaan"),
    "realization": ("realisasi", "status pelaksanaan", "hasil pelaksanaan"),
    "residual_risk": ("risiko residual", "risiko sisa"),
}


PARAMETER_SCOPE_REGISTRY: dict[str, dict[str, tuple[str, ...] | bool]] = {
    "risk_matrix": {
        "primary": (
            "KK3.1|2.1|2.1.2",
            "KK3.1|2.2|2.2.1",
            "KK3.1|2.2|2.2.2",
            "KK3.1|2.2|2.2.3",
        ),
        "secondary": (
            "KK3.1|2.1|2.1.3",
            "KK3.1|2.2|2.2.4",
            "KK3.1|2.2|2.2.5",
            "KK3.1|5.1|5.1.3",
        ),
        "exploratory": False,
    },
    "monitoring_report": {
        "primary": (
            "KK3.1|5.1|5.1.3",
            "KK3.1|2.2|2.2.4",
            "KK3.1|2.2|2.2.5",
        ),
        "secondary": (),
        "exploratory": False,
    },
    "risk_policy": {
        "primary": ("KK3.1|2.1|2.1.1",),
        "secondary": (),
        "exploratory": False,
    },
    "review_audit": {
        "primary": (
            "KK3.1|5.1|5.1.2",
            "KK3.1|5.2|5.2.1",
        ),
        "secondary": (),
        "exploratory": False,
    },
    "transmittal_letter": {"primary": (), "secondary": (), "exploratory": False},
    "meeting_invitation": {"primary": (), "secondary": (), "exploratory": False},
    "meeting_minutes": {"primary": (), "secondary": (), "exploratory": True},
    "photo_documentation": {"primary": (), "secondary": (), "exploratory": False},
    "template_form": {"primary": (), "secondary": (), "exploratory": False},
    "unknown": {"primary": (), "secondary": (), "exploratory": True},
}


def parameter_key(item: dict) -> str:
    return "|".join(
        str(item.get(name) or "").strip()
        for name in ("kk_id", "kode", "detail_kode")
    )


def parameter_scope_for_family(
    family: str,
    facts: Iterable[dict] | None = None,
) -> dict:
    normalized_family = family if family in DOCUMENT_FAMILIES else "unknown"
    registered = PARAMETER_SCOPE_REGISTRY[normalized_family]
    primary = list(registered["primary"])
    secondary = list(registered["secondary"])
    if normalized_family == "risk_matrix" and _explicit_process_review(facts or []):
        secondary.append("KK3.1|5.1|5.1.2")
    return {
        "family": normalized_family,
        "primary_parameter_keys": primary,
        "secondary_parameter_keys": secondary,
        "allowed_parameter_keys": [*primary, *secondary],
        "exploratory": bool(registered["exploratory"]),
        "registry_version": "document-family-parameter-scope-v1",
    }


def restrict_parameters(parameters: list[dict], scope: dict) -> list[dict]:
    if scope.get("exploratory"):
        return list(parameters)
    allowed = set(scope.get("allowed_parameter_keys") or [])
    return [item for item in parameters if parameter_key(item) in allowed]


def _explicit_process_review(facts: Iterable[dict]) -> bool:
    review_terms = (
        "reviu proses manajemen risiko",
        "review proses manajemen risiko",
        "evaluasi proses manajemen risiko",
        "penilaian proses manajemen risiko",
    )
    return any(
        any(term in str(fact.get("claim") or "").casefold() for term in review_terms)
        for fact in facts
    )
