from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import re
from typing import Any, Iterable


WORKBOOK_EVIDENCE_SCHEMA_VERSION = "workbook-multi-evidence-v1"
DEFAULT_AMBIGUITY_MARGIN = 0.05
MAX_SECONDARY_PARAMETERS = 3
DERIVABLE_RUN_STATUSES = frozenset({
    "approved",
    "blocked",
    "rejected",
    "review_required",
    "screening_complete",
    "uploaded",
})
UNAVAILABLE_RUN_STATUSES = frozenset({"cancelled", "failed"})

_FACT_ROLE_WEIGHTS = {
    "primary": 1.0,
    "supporting": 0.8,
    "context": 0.5,
    "optional": 0.35,
}
_FACT_STAGE_TYPES = {"policy", "socialization", "implementation", "evaluation", "improvement"}
_PARAMETER_STAGES = {
    "2.1.2": "register",
    "2.2.1": "analysis",
    "2.2.2": "prioritization",
    "2.2.3": "rtp",
    "2.2.4": "monitoring",
    "2.2.5": "evaluation",
}
_REGISTER_SHEET_RE = re.compile(
    r"\b(?:register|peta\s+risiko|petris|risk\s+5\s*tahun|keterjadian\s+risiko)\b",
    flags=re.IGNORECASE,
)
_SHEET_STAGE_HINTS = (
    (re.compile(r"\b(?:monitoring|pemantauan)\b", flags=re.IGNORECASE), "2.2.4"),
    (re.compile(r"\b(?:efektivitas|evaluasi\s+penanganan)\b", flags=re.IGNORECASE), "2.2.5"),
    (re.compile(r"\b(?:prioritas|priorit(?:as|isasi))\b", flags=re.IGNORECASE), "2.2.2"),
    (re.compile(r"\b(?:analisis|assessment)\b", flags=re.IGNORECASE), "2.2.1"),
    (re.compile(r"\b(?:rtp|rencana\s+tindak\s+pengendalian)\b", flags=re.IGNORECASE), "2.2.3"),
)
_RISK_REDUCTION_RE = re.compile(
    r"\b(?:menurun|penurunan|berkurang|pengurangan|"
    r"risiko\s+residual[^.]{0,80}(?:rendah|turun))\b",
    flags=re.IGNORECASE,
)
_NON_SUBSTANTIVE_FACT_STATUSES = {
    "discarded",
    "rejected",
    "template_only",
    "invalid",
}


def build_workbook_evidence_view(
    *,
    run_status: str,
    file_kind: str,
    document_units: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    mapping_candidates: list[dict[str, Any]],
    grade_assessments: list[dict[str, Any]] | None = None,
    verification_results: list[dict[str, Any]] | None = None,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> dict[str, Any]:
    """Build the safe API view without publishing partial sheet attribution."""

    normalized_kind = str(file_kind or "").strip().casefold()
    if normalized_kind != "xlsx":
        return _not_applicable_result()
    normalized_status = str(run_status or "").strip().casefold()
    if normalized_status in UNAVAILABLE_RUN_STATUSES:
        return _unavailable_result(normalized_status)
    if normalized_status not in DERIVABLE_RUN_STATUSES:
        return _pending_result()
    return build_workbook_evidence(
        file_kind=normalized_kind,
        document_units=document_units,
        facts=facts,
        mapping_candidates=mapping_candidates,
        grade_assessments=grade_assessments,
        verification_results=verification_results,
        ambiguity_margin=ambiguity_margin,
    )


def build_workbook_evidence(
    *,
    file_kind: str,
    document_units: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    mapping_candidates: list[dict[str, Any]],
    grade_assessments: list[dict[str, Any]] | None = None,
    verification_results: list[dict[str, Any]] | None = None,
    ambiguity_margin: float = DEFAULT_AMBIGUITY_MARGIN,
) -> dict[str, Any]:
    """Build a read-only sheet attribution view from persisted V2 artifacts.

    The function never retrieves new parameters, invokes a provider, assesses a
    Grade, mutates review state, or changes controlled-upload eligibility.
    """

    if str(file_kind or "").strip().casefold() != "xlsx":
        return _not_applicable_result()

    sheets = sorted(
        (
            unit for unit in document_units
            if str(unit.get("unit_type") or "").casefold() == "sheet"
        ),
        key=lambda unit: (
            int(unit.get("ordinal") or 0),
            str(unit.get("unit_key") or ""),
        ),
    )
    fact_by_id = {
        fact_id: fact
        for fact in facts
        if (fact_id := _positive_int(fact.get("id"))) is not None
        and _is_substantive_fact(fact)
    }
    assessment_by_mapping = {
        mapping_id: assessment
        for assessment in (grade_assessments or [])
        if (mapping_id := _positive_int(assessment.get("mapping_candidate_id"))) is not None
    }
    verification_by_mapping: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for verification in verification_results or []:
        mapping_id = _positive_int(verification.get("mapping_candidate_id"))
        if mapping_id is not None:
            verification_by_mapping[mapping_id].append(verification)

    sheet_results: list[dict[str, Any]] = []
    seen_fact_signatures: dict[tuple[tuple[str, str, str], ...], str] = {}
    used_sheet_keys: set[str] = set()
    safe_margin = max(0.0, min(1.0, float(ambiguity_margin or 0.0)))

    for unit in sheets:
        sheet_name = _sheet_name(unit)
        sheet_key = _unique_sheet_key(sheet_name, unit, used_sheet_keys)
        used_sheet_keys.add(sheet_key)
        sheet_facts = [
            fact for fact in fact_by_id.values()
            if _fact_belongs_to_sheet(fact, unit, sheet_name)
        ]
        sheet_facts.sort(key=lambda fact: int(fact.get("id") or 0))
        source_fact_ids = [int(fact["id"]) for fact in sheet_facts]
        sheet_state = _sheet_exclusion_state(unit)
        coverage_status = _sheet_coverage_status(unit)
        candidates = _attributed_candidates(
            unit=unit,
            sheet_name=sheet_name,
            sheet_fact_ids=set(source_fact_ids),
            fact_by_id=fact_by_id,
            mapping_candidates=mapping_candidates,
            assessment_by_mapping=assessment_by_mapping,
            verification_by_mapping=verification_by_mapping,
        )
        evidence_role = _sheet_evidence_role(unit, sheet_facts)
        warnings = list(dict.fromkeys(str(item) for item in (unit.get("warnings") or []) if item))
        if sheet_name is None:
            warnings.append(
                "Nama sheet tidak tersedia pada source_location atau metadata; sistem tidak menebaknya."
            )
        primary_parameter: dict[str, Any] | None = None
        secondary_parameters: list[dict[str, Any]] = []
        status = sheet_state

        if sheet_state == "hidden":
            secondary_parameters = candidates[:MAX_SECONDARY_PARAMETERS]
            warnings.append("Sheet tersembunyi tidak digunakan sebagai primary evidence.")
        elif sheet_state in {"empty", "template_only", "instruction_only", "unreadable", "not_processed"}:
            candidates = []
        elif not source_fact_ids:
            status = "no_substantive_facts"
            candidates = []
            warnings.append("Sheet belum mempunyai fakta substantif yang dapat diatribusikan.")
        elif coverage_status == "partial":
            status = "needs_review"
            warnings.append(
                "Coverage sheet baru sebagian; rekomendasi tidak boleh dianggap final sebelum pemeriksaan sumber."
            )
            if candidates:
                primary_parameter = {
                    **candidates[0],
                    "selection_status": "needs_review",
                }
                secondary_parameters = _distinct_secondary_candidates(
                    candidates[1:],
                    primary_parameter,
                )
            else:
                warnings.append(
                    "Mapping belum dapat diatribusikan secara aman dari coverage parsial."
                )
        elif not candidates:
            status = "needs_sheet_retrieval"
            warnings.append(
                "Fakta substantif tersedia, tetapi mapping existing belum mempunyai fakta pendukung dari sheet ini."
            )
        else:
            primary_parameter = {**candidates[0], "selection_status": "selected"}
            secondary_parameters = _distinct_secondary_candidates(
                candidates[1:],
                primary_parameter,
            )
            status = "mapped"
            if _is_ambiguous(candidates, safe_margin):
                status = "ambiguous"
                primary_parameter["selection_status"] = "ambiguous"
                warnings.append(
                    f"Dua kandidat teratas memiliki dukungan fakta setara dan selisih mapping score tidak melebihi {safe_margin:.2f}."
                )

        fact_signature = _fact_signature(sheet_facts)
        duplicate_of = None
        if fact_signature and status in {"mapped", "ambiguous", "needs_sheet_retrieval"}:
            duplicate_of = seen_fact_signatures.get(fact_signature)
            if duplicate_of:
                warnings.append(
                    f"Fakta substantif identik dengan {duplicate_of}; sheet duplikat tidak menambah deteksi multi-evidence."
                )
            else:
                seen_fact_signatures[fact_signature] = sheet_key

        parameter_candidates = [
            item for item in [primary_parameter, *secondary_parameters] if item
        ]
        evidence_stages = sorted({
            *(
                str(fact.get("fact_type"))
                for fact in sheet_facts
                if str(fact.get("fact_type") or "") in _FACT_STAGE_TYPES
            ),
            *(
                stage for item in parameter_candidates
                if (stage := _PARAMETER_STAGES.get(str(item.get("detail_kode") or "")))
            ),
        })
        sheet_results.append(
            {
                "sheet_key": sheet_key,
                "unit_key": str(unit.get("unit_key") or ""),
                "sheet_name": sheet_name,
                "sheet_index": _sheet_index(unit),
                "ordinal": int(unit.get("ordinal") or 0),
                "hidden": _is_hidden_sheet(unit),
                "status": status or "no_substantive_facts",
                "primary_eligible": status == "mapped",
                "evidence_role": evidence_role,
                "coverage_status": coverage_status,
                "fact_count": len(source_fact_ids),
                "source_fact_ids": source_fact_ids,
                "context": {
                    "periods": sorted({str(fact["period"]) for fact in sheet_facts if fact.get("period")}),
                    "organizations": sorted({
                        str(fact["organization"]) for fact in sheet_facts if fact.get("organization")
                    }),
                },
                "evidence_stages": evidence_stages,
                "primary_parameter": primary_parameter,
                "secondary_parameters": secondary_parameters,
                "duplicate_of": duplicate_of,
                "warnings": list(dict.fromkeys(warnings)),
            }
        )

    substantive_results = [
        item for item in sheet_results
        if item["status"] in {
            "mapped", "ambiguous", "needs_review", "needs_sheet_retrieval"
        }
    ]
    unique_substantive_results = [
        item for item in substantive_results if not item.get("duplicate_of")
    ]
    workbook_summary = _workbook_summary(sheet_results)
    is_multi_evidence, detection_reasons = _detect_multi_evidence(unique_substantive_results)
    workbook_summary["detection_reasons"] = detection_reasons

    return {
        "schema_version": WORKBOOK_EVIDENCE_SCHEMA_VERSION,
        "status": "ready",
        "applicable": True,
        "is_multi_evidence": is_multi_evidence,
        "sheet_count": len(sheet_results),
        "substantive_sheet_count": len(substantive_results),
        "unique_substantive_sheet_count": len(unique_substantive_results),
        "sheet_results": sheet_results,
        "workbook_summary": workbook_summary,
    }


def refresh_workbook_evidence_summary(
    workbook_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Recalculate workbook aggregates after additive derived recommendations."""

    refreshed = deepcopy(workbook_evidence)
    sheet_results = refreshed.get("sheet_results") or []
    substantive_statuses = {
        "mapped",
        "ambiguous",
        "needs_sheet_retrieval",
        "sheet_retrieval_recommended",
        "needs_review",
    }
    substantive_results = [
        item for item in sheet_results if item.get("status") in substantive_statuses
    ]
    unique_substantive_results = [
        item for item in substantive_results if not item.get("duplicate_of")
    ]
    workbook_summary = _workbook_summary(sheet_results)
    is_multi_evidence, detection_reasons = _detect_multi_evidence(
        unique_substantive_results
    )
    workbook_summary["detection_reasons"] = detection_reasons
    refreshed.update({
        "is_multi_evidence": is_multi_evidence,
        "sheet_count": len(sheet_results),
        "substantive_sheet_count": len(substantive_results),
        "unique_substantive_sheet_count": len(unique_substantive_results),
        "sheet_results": sheet_results,
        "workbook_summary": workbook_summary,
    })
    return refreshed


def _not_applicable_result() -> dict[str, Any]:
    return {
        "schema_version": WORKBOOK_EVIDENCE_SCHEMA_VERSION,
        "status": "not_applicable",
        "applicable": False,
        "is_multi_evidence": False,
        "sheet_count": 0,
        "substantive_sheet_count": 0,
        "unique_substantive_sheet_count": 0,
        "sheet_results": [],
        "workbook_summary": {
            "primary_parameter": None,
            "secondary_parameters": [],
            "parameter_sources": [],
            "detection_reasons": [],
            "warning_codes": [],
            "warnings": [],
        },
    }


def _pending_result() -> dict[str, Any]:
    return {
        "schema_version": WORKBOOK_EVIDENCE_SCHEMA_VERSION,
        "status": "pending",
        "applicable": True,
        "is_multi_evidence": False,
        "sheet_count": 0,
        "substantive_sheet_count": 0,
        "unique_substantive_sheet_count": 0,
        "sheet_results": [],
        "workbook_summary": {
            "primary_parameter": None,
            "secondary_parameters": [],
            "parameter_sources": [],
            "detection_reasons": [],
            "warning_codes": [],
            "warnings": [
                "Analisis workbook belum selesai; atribusi sheet belum diterbitkan."
            ],
        },
    }


def _unavailable_result(run_status: str) -> dict[str, Any]:
    safe_status = "cancelled" if run_status == "cancelled" else "failed"
    warning_code = "RUN_CANCELLED" if safe_status == "cancelled" else "RUN_FAILED"
    warning = (
        "Analisis workbook dibatalkan; atribusi sheet tidak diterbitkan."
        if safe_status == "cancelled"
        else "Analisis workbook gagal; atribusi sheet tidak diterbitkan."
    )
    return {
        "schema_version": WORKBOOK_EVIDENCE_SCHEMA_VERSION,
        "status": safe_status,
        "applicable": True,
        "is_multi_evidence": False,
        "sheet_count": 0,
        "substantive_sheet_count": 0,
        "unique_substantive_sheet_count": 0,
        "sheet_results": [],
        "workbook_summary": {
            "primary_parameter": None,
            "secondary_parameters": [],
            "parameter_sources": [],
            "detection_reasons": [],
            "warning_codes": [warning_code],
            "warnings": [warning],
        },
    }


def _attributed_candidates(
    *,
    unit: dict[str, Any],
    sheet_name: str | None,
    sheet_fact_ids: set[int],
    fact_by_id: dict[int, dict[str, Any]],
    mapping_candidates: list[dict[str, Any]],
    assessment_by_mapping: dict[int, dict[str, Any]],
    verification_by_mapping: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    candidates = []
    for mapping in mapping_candidates:
        mapping_id = _positive_int(mapping.get("id"))
        if mapping_id is None:
            continue
        supported_ids = sorted({
            fact_id for value in (mapping.get("supporting_fact_ids") or [])
            if (fact_id := _positive_int(value)) is not None
            and fact_id in sheet_fact_ids
            and fact_id in fact_by_id
        })
        if not supported_ids:
            continue
        supporting_facts = [fact_by_id[fact_id] for fact_id in supported_ids]
        detail_kode = str(mapping.get("detail_kode") or "")
        if detail_kode == "2.2.5" and not _has_risk_reduction_evidence(
            supporting_facts
        ):
            # Efektivitas penanganan harus mempunyai bukti outcome. Kata
            # "evaluasi" atau "efektivitas" saja tidak cukup untuk 2.2.5.
            continue
        support_strength = round(sum(
            _FACT_ROLE_WEIGHTS.get(str(fact.get("evidence_role") or "context"), 0.5)
            for fact in supporting_facts
        ), 4)
        source_locations = _candidate_source_locations(
            supporting_facts,
            unit,
            sheet_name,
        )
        candidate = {
            "mapping_candidate_id": mapping_id,
            "kk_id": str(mapping.get("kk_id") or ""),
            "kode": str(mapping.get("kode") or ""),
            "detail_kode": detail_kode,
            "uraian": mapping.get("parameter_uraian") or mapping.get("uraian"),
            "mapping_score": round(float(mapping.get("mapping_score") or 0.0), 4),
            "supporting_fact_count": len(supported_ids),
            "supporting_fact_ids": supported_ids,
            "support_strength": support_strength,
            "source_locations": source_locations,
            "verification_status": _verification_status(
                verification_by_mapping.get(mapping_id, [])
            ),
            "sheet_intent_score": _sheet_intent_score(
                unit,
                sheet_name,
                detail_kode,
            ),
        }
        assessment = assessment_by_mapping.get(mapping_id)
        if assessment:
            candidate["existing_grade_assessment"] = {
                "assessment_id": assessment.get("id"),
                "candidate_grade": assessment.get("candidate_grade"),
                "grade_ceiling": assessment.get("grade_ceiling"),
                "grade_status": assessment.get("grade_status"),
                "primary_allowed": bool(assessment.get("primary_allowed")),
                "source": "existing_grade_assessment",
            }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -float(item["sheet_intent_score"]),
            -float(item["support_strength"]),
            -int(item["supporting_fact_count"]),
            -float(item["mapping_score"]),
            str(item["kk_id"]),
            str(item["kode"]),
            str(item["detail_kode"]),
            int(item["mapping_candidate_id"]),
        )
    )
    return _deduplicate_parameter_candidates(candidates)


def _deduplicate_parameter_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        key = _parameter_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
    return selected


def _distinct_secondary_candidates(
    candidates: list[dict[str, Any]],
    primary: dict[str, Any],
) -> list[dict[str, Any]]:
    primary_key = _parameter_key(primary)
    selected = []
    seen = {primary_key}
    for candidate in candidates:
        key = _parameter_key(candidate)
        if key in seen or not candidate.get("supporting_fact_ids"):
            continue
        seen.add(key)
        selected.append({**candidate, "selection_status": "secondary"})
        if len(selected) >= MAX_SECONDARY_PARAMETERS:
            break
    return selected


def _is_ambiguous(candidates: list[dict[str, Any]], margin: float) -> bool:
    if len(candidates) < 2:
        return False
    first, second = candidates[:2]
    return bool(
        float(first.get("support_strength") or 0) == float(second.get("support_strength") or 0)
        and int(first.get("supporting_fact_count") or 0)
        == int(second.get("supporting_fact_count") or 0)
        and abs(
            float(first.get("mapping_score") or 0)
            - float(second.get("mapping_score") or 0)
        ) <= margin
    )


def _sheet_intent_score(
    unit: dict[str, Any],
    sheet_name: str | None,
    detail_kode: str,
) -> float:
    """Prefer a same-sheet stage signal without creating a new mapping.

    This score only reorders candidates that already cite facts from the same
    sheet. It therefore cannot make a title-only candidate eligible.
    """

    normalized_name = " ".join(str(sheet_name or "").casefold().split())
    metadata = unit.get("metadata") or {}
    risk_matrix_relevant = bool(metadata.get("risk_matrix_relevant"))
    substantive_rows = int(metadata.get("substantive_row_count") or 0)

    if (
        detail_kode == "2.1.2"
        and _REGISTER_SHEET_RE.search(normalized_name)
        and (risk_matrix_relevant and substantive_rows > 0 or "register" in normalized_name)
    ):
        return 1.0

    for pattern, expected_detail in _SHEET_STAGE_HINTS:
        if not pattern.search(normalized_name):
            continue
        # "Monitoring RTP" is monitoring evidence, not merely an RTP plan.
        return 1.0 if detail_kode == expected_detail else 0.0
    return 0.0


def _has_risk_reduction_evidence(facts: list[dict[str, Any]]) -> bool:
    return any(
        _RISK_REDUCTION_RE.search(str(fact.get("claim") or ""))
        for fact in facts
    )


def _distinct_context_values(
    sheets: list[dict[str, Any]],
    field: str,
) -> set[str]:
    return {
        normalized
        for sheet in sheets
        for value in (sheet.get("context") or {}).get(field) or []
        if (normalized := " ".join(str(value or "").casefold().split()))
    }


def _workbook_summary(sheet_results: list[dict[str, Any]]) -> dict[str, Any]:
    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}
    for sheet in sheet_results:
        candidates = []
        if sheet.get("primary_parameter"):
            candidates.append(("primary", sheet["primary_parameter"]))
        candidates.extend(("secondary", item) for item in sheet.get("secondary_parameters") or [])
        for role, candidate in candidates:
            key = _parameter_key(candidate)
            aggregate = aggregates.setdefault(
                key,
                {
                    "mapping_candidate_id": candidate.get("mapping_candidate_id"),
                    "kk_id": candidate.get("kk_id"),
                    "kode": candidate.get("kode"),
                    "detail_kode": candidate.get("detail_kode"),
                    "uraian": candidate.get("uraian"),
                    "max_mapping_score": 0.0,
                    "supporting_fact_count": 0,
                    "primary_source_count": 0,
                    "source_sheets": [],
                },
            )
            aggregate["max_mapping_score"] = max(
                float(aggregate["max_mapping_score"]),
                float(candidate.get("mapping_score") or 0),
            )
            aggregate["supporting_fact_count"] += int(candidate.get("supporting_fact_count") or 0)
            aggregate["primary_source_count"] += int(role == "primary")
            source = {
                "sheet_key": sheet.get("sheet_key"),
                "sheet_name": sheet.get("sheet_name"),
                "unit_key": sheet.get("unit_key"),
                "role": role,
            }
            if source not in aggregate["source_sheets"]:
                aggregate["source_sheets"].append(source)

    parameter_sources = sorted(
        aggregates.values(),
        key=lambda item: (
            -int(item["primary_source_count"]),
            -int(item["supporting_fact_count"]),
            -float(item["max_mapping_score"]),
            str(item["kk_id"]),
            str(item["kode"]),
            str(item["detail_kode"]),
        ),
    )
    primary = _summary_parameter(parameter_sources[0]) if parameter_sources else None
    secondary = [
        _summary_parameter(item)
        for item in parameter_sources[1:1 + MAX_SECONDARY_PARAMETERS]
    ]
    warnings = []
    warning_codes = []
    ambiguous_count = sum(
        item.get("status") in {"ambiguous", "needs_review"}
        for item in sheet_results
    )
    fallback_count = sum(
        item.get("status") == "sheet_retrieval_recommended"
        for item in sheet_results
    )
    retrieval_count = sum(item.get("status") == "needs_sheet_retrieval" for item in sheet_results)
    excluded_count = sum(
        item.get("status") in {
            "empty", "hidden", "template_only", "instruction_only",
            "unreadable", "not_processed", "no_substantive_facts",
        }
        for item in sheet_results
    )
    if ambiguous_count:
        warnings.append(f"{ambiguous_count} sheet mempunyai atribusi parameter ambigu.")
    if retrieval_count:
        warnings.append(f"{retrieval_count} sheet memerlukan retrieval khusus sheet pada fase berikutnya.")
    if fallback_count:
        warnings.append(
            f"{fallback_count} sheet mempunyai rekomendasi derived dari sheet retrieval; human review tetap diperlukan."
        )
    if excluded_count:
        warnings.append(f"{excluded_count} sheet tidak memenuhi syarat sebagai primary evidence.")
    if not sheet_results:
        warning_codes.append("NO_WORKBOOK_SHEETS")
        warnings.append(
            "Workbook tidak mempunyai sheet hasil parser yang dapat ditampilkan."
        )

    context_sheets = [
        item for item in sheet_results
        if item.get("status") in {
            "mapped",
            "ambiguous",
            "needs_sheet_retrieval",
            "sheet_retrieval_recommended",
            "needs_review",
        }
        and not item.get("duplicate_of")
    ]
    periods = _distinct_context_values(context_sheets, "periods")
    organizations = _distinct_context_values(context_sheets, "organizations")
    if len(periods) > 1:
        warning_codes.append("MIXED_PERIODS_ACROSS_SHEETS")
        warnings.append(
            "Workbook memuat lebih dari satu periode; periode penilaian perlu dikonfirmasi per sheet."
        )
    if len(organizations) > 1:
        warning_codes.append("MIXED_ORGANIZATIONS_ACROSS_SHEETS")
        warnings.append(
            "Workbook memuat lebih dari satu konteks unit kerja; atribusi organisasi perlu dikonfirmasi per sheet."
        )
    return {
        "primary_parameter": primary,
        "secondary_parameters": secondary,
        "parameter_sources": parameter_sources,
        "warning_codes": warning_codes,
        "warnings": warnings,
    }


def _summary_parameter(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "mapping_candidate_id", "kk_id", "kode", "detail_kode", "uraian",
            "max_mapping_score", "supporting_fact_count", "primary_source_count",
            "source_sheets",
        )
    }


def _detect_multi_evidence(
    substantive_results: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    if len(substantive_results) < 2:
        return False, []
    reasons = []
    primary_by_sheet = {
        str(sheet["sheet_key"]): _parameter_key(sheet["primary_parameter"])
        for sheet in substantive_results
        if sheet.get("primary_parameter")
    }
    if len(set(primary_by_sheet.values())) >= 2:
        reasons.append("different_primary_parameters_across_substantive_sheets")

    for primary_sheet, primary_key in primary_by_sheet.items():
        if any(
            sheet["sheet_key"] != primary_sheet
            and any(
                _parameter_key(candidate) != primary_key
                and bool(candidate.get("supporting_fact_ids"))
                for candidate in sheet.get("secondary_parameters") or []
            )
            for sheet in substantive_results
        ):
            reasons.append("cross_sheet_secondary_parameter_with_source_support")
            break

    stage_sets = {
        str(sheet["sheet_key"]): frozenset(sheet.get("evidence_stages") or [])
        for sheet in substantive_results
        if sheet.get("evidence_stages")
    }
    stage_items = list(stage_sets.items())
    if any(
        left_stages != right_stages and len(left_stages | right_stages) >= 2
        for left_index, (_left_sheet, left_stages) in enumerate(stage_items)
        for _right_sheet, right_stages in stage_items[left_index + 1:]
    ):
        reasons.append("different_evidence_stages_across_substantive_sheets")

    return bool(reasons), reasons


def _candidate_source_locations(
    facts: list[dict[str, Any]],
    unit: dict[str, Any],
    sheet_name: str | None,
) -> list[dict[str, Any]]:
    locations = []
    for fact in facts:
        for source in _fact_sources(fact):
            if not _source_belongs_to_sheet(source, unit, sheet_name):
                continue
            location = dict(source.get("source_location") or {})
            item = {
                "unit_key": str(source.get("unit_key") or unit.get("unit_key") or ""),
                **location,
            }
            if item not in locations:
                locations.append(item)
    return locations


def _fact_belongs_to_sheet(
    fact: dict[str, Any],
    unit: dict[str, Any],
    sheet_name: str | None,
) -> bool:
    return any(
        _source_belongs_to_sheet(source, unit, sheet_name)
        for source in _fact_sources(fact)
    )


def _source_belongs_to_sheet(
    source: dict[str, Any],
    unit: dict[str, Any],
    sheet_name: str | None,
) -> bool:
    unit_key = str(unit.get("unit_key") or "")
    source_unit_key = str(source.get("unit_key") or "")
    if unit_key and source_unit_key and unit_key == source_unit_key:
        return True
    location = source.get("source_location") or {}
    source_sheet = str(location.get("sheet") or "").strip().casefold()
    expected_sheet = str(sheet_name or "").strip().casefold()
    if not source_sheet or not expected_sheet or source_sheet != expected_sheet:
        return False
    source_index = _positive_int(location.get("sheet_index"))
    expected_index = _sheet_index(unit)
    return source_index is None or expected_index is None or source_index == expected_index


def _fact_sources(fact: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [item for item in (fact.get("sources") or []) if isinstance(item, dict)]
    source = fact.get("source")
    if isinstance(source, dict) and source not in sources:
        sources.append(source)
    return sources


def _is_substantive_fact(fact: dict[str, Any]) -> bool:
    return bool(
        str(fact.get("claim") or "").strip()
        and str(fact.get("status") or "extracted").casefold()
        not in _NON_SUBSTANTIVE_FACT_STATUSES
        and str(fact.get("evidence_role") or "context").casefold() != "contradictory"
    )


def _sheet_exclusion_state(unit: dict[str, Any]) -> str | None:
    status = str(unit.get("status") or "pending").casefold()
    metadata = unit.get("metadata") or {}
    template = metadata.get("template_detection") or {}
    if status == "failed":
        return "unreadable"
    if status not in {"processed", "partial"}:
        return "not_processed"
    if _is_hidden_sheet(unit):
        return "hidden"
    if bool(
        metadata.get("instruction_only")
        or template.get("instruction_only")
    ):
        return "instruction_only"
    if bool(
        metadata.get("template_only")
        or template.get("template_only")
    ):
        return "template_only"
    if _is_empty_sheet(unit):
        return "empty"
    return None


def _is_empty_sheet(unit: dict[str, Any]) -> bool:
    if unit.get("char_count") is not None:
        return int(unit.get("char_count") or 0) <= 0
    if "text" in unit:
        return not str(unit.get("text") or "").strip()
    metadata = unit.get("metadata") or {}
    for field in ("value_cells", "profiled_cell_count"):
        if metadata.get(field) is not None:
            return int(metadata.get(field) or 0) <= 0
    return False


def _is_hidden_sheet(unit: dict[str, Any]) -> bool:
    location = unit.get("source_location") or {}
    metadata = unit.get("metadata") or {}
    return bool(
        location.get("hidden")
        or str(metadata.get("state") or "visible").casefold() != "visible"
    )


def _sheet_name(unit: dict[str, Any]) -> str | None:
    location = unit.get("source_location") or {}
    metadata = unit.get("metadata") or {}
    for value in (location.get("sheet"), metadata.get("name")):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _sheet_index(unit: dict[str, Any]) -> int | None:
    location = unit.get("source_location") or {}
    return _positive_int(location.get("sheet_index")) or _positive_int(unit.get("ordinal"))


def _unique_sheet_key(
    sheet_name: str | None,
    unit: dict[str, Any],
    used_keys: set[str],
) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(sheet_name or "").casefold()).strip("-")
    unit_slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        str(unit.get("unit_key") or "").casefold(),
    ).strip("-")
    base = f"sheet:{slug or unit_slug or _sheet_index(unit) or 'unknown'}"
    if base not in used_keys:
        return base
    suffix = _sheet_index(unit) or len(used_keys) + 1
    return f"{base}:{suffix}"


def _sheet_coverage_status(unit: dict[str, Any]) -> str:
    status = str(unit.get("status") or "pending").casefold()
    return {
        "processed": "complete",
        "partial": "partial",
        "failed": "failed",
    }.get(status, "pending")


def _sheet_evidence_role(
    unit: dict[str, Any],
    facts: list[dict[str, Any]],
) -> str:
    metadata = unit.get("metadata") or {}
    configured = str(metadata.get("unit_evidence_role") or "").casefold()
    if configured:
        return configured
    roles = {str(fact.get("evidence_role") or "context").casefold() for fact in facts}
    for role in ("primary", "supporting", "context", "optional", "reject"):
        if role in roles:
            return role
    return "context"


def _verification_status(results: list[dict[str, Any]]) -> str:
    statuses = [str(item.get("status") or "").casefold() for item in results if item.get("status")]
    if not statuses:
        return "not_available"
    if all(status == "verified" for status in statuses):
        return "verified"
    for status in ("needs_human_review", "failed", "rejected", "blocked"):
        if status in statuses:
            return status
    return statuses[0] if len(set(statuses)) == 1 else "mixed"


def _fact_signature(facts: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted({
        (
            " ".join(str(fact.get("claim") or "").casefold().split()),
            str(fact.get("period") or "").casefold(),
            str(fact.get("organization") or "").casefold(),
        )
        for fact in facts
        if str(fact.get("claim") or "").strip()
    }))


def _parameter_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("kk_id") or ""),
        str(item.get("kode") or ""),
        str(item.get("detail_kode") or ""),
    )


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
