from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from app.analysis.advanced_rag import (
    AdvancedRAGCatalogSearchEngine,
    AdvancedRAGQueryExpansionEngine,
    retrieval_needs_model_expansion,
)
from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.domain.retrieval import ParameterRetrievalEngine, SPIPMappingEngine
from app.analysis.provider import configured_rag_catalog_provider, configured_rag_query_provider
from app.analysis.workbook_evidence import (
    MAX_SECONDARY_PARAMETERS,
    refresh_workbook_evidence_summary,
)
from app.config import Settings


SHEET_RETRIEVAL_VERSION = "sheet-retrieval-v1"
DEFAULT_SHEET_CANDIDATE_LIMIT = 10
SHEET_RETRIEVAL_AUTHORITY = (
    "derived_recommendation_only_no_grade_human_decision_or_upload_authority"
)


class SheetRetrievalFallback:
    """Run the existing parameter-first retrieval only for unresolved sheets."""

    def __init__(
        self,
        *,
        retrieval_engine: ParameterRetrievalEngine,
        mapping_engine: SPIPMappingEngine | None = None,
        catalog_search_engine: AdvancedRAGCatalogSearchEngine | None = None,
        query_expansion_engine: AdvancedRAGQueryExpansionEngine | None = None,
        advanced_rag_enabled: bool = False,
        minimum_confidence: float = 0.68,
        ambiguity_margin: float = 0.08,
        candidate_limit: int = DEFAULT_SHEET_CANDIDATE_LIMIT,
    ) -> None:
        self.retrieval_engine = retrieval_engine
        self.mapping_engine = mapping_engine or SPIPMappingEngine()
        self.catalog_search_engine = catalog_search_engine
        self.query_expansion_engine = query_expansion_engine
        self.advanced_rag_enabled = bool(advanced_rag_enabled)
        self.minimum_confidence = max(0.0, min(1.0, float(minimum_confidence)))
        self.ambiguity_margin = max(0.0, min(1.0, float(ambiguity_margin)))
        self.candidate_limit = max(
            1,
            min(DEFAULT_SHEET_CANDIDATE_LIMIT, int(candidate_limit)),
        )

    def apply(
        self,
        *,
        workbook_evidence: dict[str, Any],
        identity: DocumentIdentity,
        document_units: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        parameters: list[dict[str, Any]],
        document_role: str,
        feedback_terms: list[dict[str, Any]] | None = None,
        external_ai_allowed: bool = False,
    ) -> dict[str, Any]:
        result = deepcopy(workbook_evidence)
        if not result.get("applicable") or result.get("status") != "ready":
            return result

        units_by_key = {
            str(unit.get("unit_key") or ""): unit
            for unit in document_units
            if unit.get("unit_key")
        }
        facts_by_id = {
            int(fact["id"]): fact
            for fact in facts
            if _positive_int(fact.get("id")) is not None
        }
        for sheet in result.get("sheet_results") or []:
            if sheet.get("status") != "needs_sheet_retrieval":
                continue
            unit = units_by_key.get(str(sheet.get("unit_key") or ""))
            sheet_fact_ids = {
                fact_id
                for value in (sheet.get("source_fact_ids") or [])
                if (fact_id := _positive_int(value)) is not None
            }
            sheet_facts = [
                facts_by_id[fact_id]
                for fact_id in sorted(sheet_fact_ids)
                if fact_id in facts_by_id
            ]
            if not unit or not sheet_facts:
                sheet.setdefault("warnings", []).append(
                    "Sheet retrieval tidak dijalankan karena unit atau fakta sheet tidak lengkap."
                )
                sheet["fallback_retrieval"] = _fallback_metadata(
                    attempted=False,
                    query_sha256=None,
                    deterministic_sufficient=False,
                    advanced_rag_enabled=self.advanced_rag_enabled,
                    advanced_rag_used=False,
                    provider_status="not_called",
                    candidate_count=0,
                    candidate_limit=self.candidate_limit,
                )
                continue

            sheet_identity, query_facts, query_sha256, sheet_intent_detail = _sheet_query(
                identity=identity,
                unit=unit,
                sheet=sheet,
                sheet_facts=sheet_facts,
                document_role=document_role,
            )
            parameter_scope = {
                "registry_version": SHEET_RETRIEVAL_VERSION,
                "family": "workbook_sheet",
                "exploratory": True,
                "evidence_role": document_role,
                "primary_parameter_keys": [],
                "secondary_parameter_keys": [],
            }
            retrieved, _retrieval_result = self.retrieval_engine.run(
                sheet_identity,
                query_facts,
                parameters,
                limit=self.candidate_limit,
                feedback_terms=feedback_terms,
                parameter_scope=parameter_scope,
            )
            retrieved = _rank_sheet_intent(
                _enforce_effectiveness_evidence(retrieved, sheet_facts),
                sheet_intent_detail,
            )
            deterministic_sufficient = not retrieval_needs_model_expansion(
                retrieved,
                minimum_confidence=self.minimum_confidence,
                ambiguity_margin=self.ambiguity_margin,
            )
            provider_statuses: list[str] = []
            provider_warnings: list[str] = []
            advanced_rag_used = False
            catalog_shortlist: list[dict[str, Any]] = []

            if (
                self.advanced_rag_enabled
                and external_ai_allowed
                and not deterministic_sufficient
                and self.catalog_search_engine
            ):
                advanced_rag_used = True
                catalog_shortlist, catalog_result = self.catalog_search_engine.run(
                    sheet_identity,
                    query_facts,
                    parameters,
                )
                provider_statuses.append(str(catalog_result.status.value))
                provider_warnings.extend(catalog_result.warnings)
                if catalog_shortlist:
                    retrieved, _retrieval_result = self.retrieval_engine.run(
                        sheet_identity,
                        query_facts,
                        parameters,
                        limit=self.candidate_limit,
                        feedback_terms=feedback_terms,
                        catalog_shortlist=catalog_shortlist,
                        parameter_scope=parameter_scope,
                    )
                    retrieved = _rank_sheet_intent(
                        _enforce_effectiveness_evidence(retrieved, sheet_facts),
                        sheet_intent_detail,
                    )

            if (
                self.advanced_rag_enabled
                and external_ai_allowed
                and not deterministic_sufficient
                and not catalog_shortlist
                and self.query_expansion_engine
            ):
                advanced_rag_used = True
                query_expansions, expansion_result = self.query_expansion_engine.run(
                    sheet_identity,
                    query_facts,
                )
                provider_statuses.append(str(expansion_result.status.value))
                provider_warnings.extend(expansion_result.warnings)
                if query_expansions:
                    retrieved, _retrieval_result = self.retrieval_engine.run(
                        sheet_identity,
                        query_facts,
                        parameters,
                        limit=self.candidate_limit,
                        feedback_terms=feedback_terms,
                        query_expansions=query_expansions,
                        parameter_scope=parameter_scope,
                    )
                    retrieved = _rank_sheet_intent(
                        _enforce_effectiveness_evidence(retrieved, sheet_facts),
                        sheet_intent_detail,
                    )

            mappings, _mapping_result = self.mapping_engine.run(
                sheet_identity,
                sheet_facts,
                retrieved,
            )
            safe_mappings = []
            for mapping in mappings:
                supporting_ids = {
                    fact_id
                    for value in (mapping.get("supporting_fact_ids") or [])
                    if (fact_id := _positive_int(value)) is not None
                }
                if not supporting_ids or not supporting_ids <= sheet_fact_ids:
                    continue
                safe_mappings.append(mapping)
            safe_mappings.sort(key=lambda item: (
                int(item.get("rag_rank") or self.candidate_limit + 1),
                -float(item.get("mapping_score") or 0),
                str(item.get("kk_id") or ""),
                str(item.get("detail_kode") or ""),
            ))

            recommendations = [
                _derived_recommendation(
                    mapping=mapping,
                    sheet=sheet,
                    facts_by_id=facts_by_id,
                    query_sha256=query_sha256,
                    advanced_rag_used=advanced_rag_used,
                )
                for mapping in safe_mappings[:self.candidate_limit]
            ]
            final_ambiguous = retrieval_needs_model_expansion(
                recommendations,
                minimum_confidence=self.minimum_confidence,
                ambiguity_margin=self.ambiguity_margin,
            )
            provider_failed = EngineStatus.FAILED.value in provider_statuses
            if recommendations:
                primary = {
                    **recommendations[0],
                    "selection_status": (
                        "needs_review" if final_ambiguous else "derived_recommendation"
                    ),
                }
                secondary = [
                    {**item, "selection_status": "secondary_derived_recommendation"}
                    for item in recommendations[1:1 + MAX_SECONDARY_PARAMETERS]
                ]
                sheet["primary_parameter"] = primary
                sheet["secondary_parameters"] = secondary
                sheet["status"] = (
                    "needs_review" if final_ambiguous else "sheet_retrieval_recommended"
                )
                sheet["primary_eligible"] = False
                sheet.setdefault("warnings", []).append(
                    "Parameter berasal dari sheet retrieval dan tetap memerlukan keputusan manusia."
                )
                if final_ambiguous:
                    sheet["warnings"].append(
                        "Hasil sheet retrieval masih lemah atau ambigu; status dipertahankan needs_review."
                    )
            else:
                sheet.setdefault("warnings", []).append(
                    "Sheet retrieval tetap abstain karena belum ada kandidat dengan fakta pendukung dari sheet yang sama."
                )
            if provider_failed:
                sheet.setdefault("warnings", []).append(
                    "Provider Advanced RAG gagal; hasil tidak dipromosikan dan tetap fail-closed."
                )
            for warning in provider_warnings:
                if warning and warning not in sheet.setdefault("warnings", []):
                    sheet["warnings"].append(warning)
            sheet["fallback_retrieval"] = _fallback_metadata(
                attempted=True,
                query_sha256=query_sha256,
                deterministic_sufficient=deterministic_sufficient,
                advanced_rag_enabled=self.advanced_rag_enabled,
                advanced_rag_used=advanced_rag_used,
                provider_status=_provider_status(provider_statuses),
                candidate_count=len(recommendations),
                candidate_limit=self.candidate_limit,
            )

        return refresh_workbook_evidence_summary(result)


def configured_sheet_retrieval_fallback(settings: Settings) -> SheetRetrievalFallback:
    query_provider = configured_rag_query_provider(settings)
    catalog_provider = configured_rag_catalog_provider(settings)
    return SheetRetrievalFallback(
        retrieval_engine=ParameterRetrievalEngine(
            advanced_rag_enabled=settings.analysis_advanced_rag_enabled
        ),
        mapping_engine=SPIPMappingEngine(),
        catalog_search_engine=(
            AdvancedRAGCatalogSearchEngine(catalog_provider)
            if catalog_provider else None
        ),
        query_expansion_engine=(
            AdvancedRAGQueryExpansionEngine(query_provider)
            if query_provider else None
        ),
        advanced_rag_enabled=settings.analysis_advanced_rag_enabled,
        minimum_confidence=settings.analysis_advanced_rag_min_confidence,
        ambiguity_margin=settings.analysis_advanced_rag_ambiguity_margin,
        candidate_limit=DEFAULT_SHEET_CANDIDATE_LIMIT,
    )


def _sheet_query(
    *,
    identity: DocumentIdentity,
    unit: dict[str, Any],
    sheet: dict[str, Any],
    sheet_facts: list[dict[str, Any]],
    document_role: str,
) -> tuple[DocumentIdentity, list[dict[str, Any]], str, str | None]:
    sheet_name = str(sheet.get("sheet_name") or "").strip()
    heading_values = [
        str(value).strip()
        for value in (unit.get("heading_path") or [])
        if str(value).strip()
    ]
    metadata = unit.get("metadata") or {}
    for value in (metadata.get("heading"), metadata.get("title")):
        normalized = str(value or "").strip()
        if normalized and normalized not in heading_values:
            heading_values.append(normalized)
    periods = sorted({str(fact["period"]) for fact in sheet_facts if fact.get("period")})
    organizations = sorted({
        str(fact["organization"]) for fact in sheet_facts if fact.get("organization")
    })
    deterministic_terms = _deterministic_sheet_terms(
        sheet_name=sheet_name,
        headings=heading_values,
        facts=sheet_facts,
    )
    sheet_intent_detail = _sheet_intent_detail(deterministic_terms)
    context_parts = [
        f"Nama sheet {sheet_name}" if sheet_name else "",
        f"Heading {' '.join(heading_values)}" if heading_values else "",
        f"Peran dokumen {document_role}" if document_role else "",
        f"Periode {' '.join(periods)}" if periods else "",
        f"Organisasi {' '.join(organizations)}" if organizations else "",
        f"Konteks {' '.join(deterministic_terms)}" if deterministic_terms else "",
    ]
    context_claim = ". ".join(part for part in context_parts if part)
    query_facts = [_query_safe_fact(fact) for fact in sheet_facts]
    if context_claim:
        query_facts.append({
            "id": None,
            "fact_key": f"sheet-query-context:{sheet.get('sheet_key')}",
            "claim": context_claim,
            "fact_type": "context",
            "evidence_role": "context",
            "period": periods[0] if len(periods) == 1 else None,
            "organization": organizations[0] if len(organizations) == 1 else None,
            "status": "query_context",
            "sources": [],
        })
    query_payload = {
        "sheet_key": sheet.get("sheet_key"),
        "sheet_name": sheet_name or None,
        "headings": heading_values,
        "document_role": document_role,
        "periods": periods,
        "organizations": organizations,
        "deterministic_terms": deterministic_terms,
        "facts": [
            {
                "id": fact.get("id"),
                "claim": fact.get("claim"),
                "source_locations": [
                    source.get("source_location") or {}
                    for source in (fact.get("sources") or [])
                    if isinstance(source, dict)
                ],
            }
            for fact in sheet_facts
        ],
    }
    query_sha256 = hashlib.sha256(
        json.dumps(
            query_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    query_name = " ".join(
        part for part in [sheet_name, *heading_values, *deterministic_terms] if part
    )[:500]
    sheet_identity = DocumentIdentity(
        file_name=query_name or "workbook sheet",
        content_type=identity.content_type,
        size_bytes=0,
        sha256=hashlib.sha256(
            f"{identity.sha256}:{sheet.get('sheet_key')}:{query_sha256}".encode("utf-8")
        ).hexdigest(),
        file_kind="xlsx",
    )
    return sheet_identity, query_facts, query_sha256, sheet_intent_detail


def _query_safe_fact(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        key: fact.get(key)
        for key in (
            "id",
            "fact_key",
            "claim",
            "fact_type",
            "evidence_role",
            "period",
            "organization",
            "status",
        )
    } | {
        "sources": [
            {
                "unit_key": source.get("unit_key"),
                "source_location": source.get("source_location") or {},
            }
            for source in (fact.get("sources") or [])
            if isinstance(source, dict)
        ]
    }


def _deterministic_sheet_terms(
    *,
    sheet_name: str,
    headings: list[str],
    facts: list[dict[str, Any]],
) -> list[str]:
    context = " ".join([
        sheet_name,
        *headings,
        *(str(fact.get("claim") or "") for fact in facts),
    ]).casefold()
    terms: list[str] = []
    if _has_risk_reduction_evidence(facts) and any(
        marker in context for marker in ("efektiv", "penanganan", "residual")
    ):
        terms.extend(("tindak pengendalian efektif", "menurunkan risiko", "level risiko residual"))
    if any(marker in context for marker in ("monitoring rtp", "pemantauan rtp", "realisasi rtp")):
        terms.extend(("implementasi tindak pengendalian", "pelaksanaan rtp", "realisasi pengendalian"))
    elif any(marker in context for marker in ("prioritas risiko", "risiko prioritas")):
        terms.extend(("menentukan prioritas risiko", "evaluasi hasil analisis risiko"))
    elif "analisis risiko" in context:
        terms.extend(("analisis dampak risiko", "kemungkinan risiko", "tingkat risiko"))
    elif any(marker in context for marker in ("register risiko", "risk register", "peta risiko")):
        terms.extend(("identifikasi risiko", "register risiko", "pemilik penyebab dampak risiko"))
    elif re.search(r"\brtp\b", context) or "rencana tindak pengendalian" in context:
        terms.extend(("rencana tindak pengendalian", "target waktu", "penanggung jawab risiko"))
    return list(dict.fromkeys(terms))


def _enforce_effectiveness_evidence(
    retrieved: list[dict[str, Any]],
    facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if _has_risk_reduction_evidence(facts):
        return retrieved
    return [item for item in retrieved if str(item.get("detail_kode") or "") != "2.2.5"]


def _sheet_intent_detail(deterministic_terms: list[str]) -> str | None:
    context = " ".join(deterministic_terms)
    if "level risiko residual" in context:
        return "2.2.5"
    if "implementasi tindak pengendalian" in context:
        return "2.2.4"
    if "menentukan prioritas risiko" in context:
        return "2.2.2"
    if "analisis dampak risiko" in context:
        return "2.2.1"
    if "identifikasi risiko" in context:
        return "2.1.2"
    if "rencana tindak pengendalian" in context:
        return "2.2.3"
    return None


def _rank_sheet_intent(
    retrieved: list[dict[str, Any]],
    detail_kode: str | None,
) -> list[dict[str, Any]]:
    ranked = []
    for item in retrieved:
        intent_match = bool(
            detail_kode and str(item.get("detail_kode") or "") == detail_kode
        )
        ranked.append({
            **item,
            "sheet_intent_match": intent_match,
            "retrieval_score": round(min(
                1.0,
                float(item.get("retrieval_score") or 0) + (0.35 if intent_match else 0.0),
            ), 4),
        })
    ranked.sort(key=lambda item: (
        -int(bool(item.get("sheet_intent_match"))),
        -float(item.get("retrieval_score") or 0),
        str(item.get("kk_id") or ""),
        str(item.get("detail_kode") or ""),
    ))
    for rank, item in enumerate(ranked, start=1):
        item["sheet_retrieval_rank"] = rank
    return ranked


def _has_risk_reduction_evidence(facts: list[dict[str, Any]]) -> bool:
    text = " ".join(str(fact.get("claim") or "") for fact in facts).casefold()
    patterns = (
        r"(?:menurun|turun|berkurang|penurunan).{0,60}(?:risiko|level)",
        r"(?:risiko|level).{0,60}(?:menurun|turun|berkurang|lebih rendah)",
        r"(?:residual|residu).{0,50}(?:menurun|turun|lebih rendah)",
        r"(?:menurunkan|mengurangi).{0,40}(?:risiko|dampak risiko)",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _derived_recommendation(
    *,
    mapping: dict[str, Any],
    sheet: dict[str, Any],
    facts_by_id: dict[int, dict[str, Any]],
    query_sha256: str,
    advanced_rag_used: bool,
) -> dict[str, Any]:
    supporting_ids = [
        fact_id
        for value in (mapping.get("supporting_fact_ids") or [])
        if (fact_id := _positive_int(value)) is not None
    ]
    source_locations: list[dict[str, Any]] = []
    for fact_id in supporting_ids:
        for source in (facts_by_id.get(fact_id, {}).get("sources") or []):
            if not isinstance(source, dict):
                continue
            location = {
                "unit_key": source.get("unit_key"),
                **(source.get("source_location") or {}),
            }
            if location not in source_locations:
                source_locations.append(location)
    parameter_key = ":".join((
        str(mapping.get("kk_id") or ""),
        str(mapping.get("kode") or ""),
        str(mapping.get("detail_kode") or ""),
    ))
    return {
        "mapping_candidate_id": None,
        "derived_mapping_key": (
            f"sheet-retrieval:{sheet.get('sheet_key')}:{parameter_key}"
        ),
        "kk_id": str(mapping.get("kk_id") or ""),
        "kode": str(mapping.get("kode") or ""),
        "detail_kode": str(mapping.get("detail_kode") or ""),
        "uraian": mapping.get("uraian"),
        "mapping_score": round(float(mapping.get("mapping_score") or 0), 4),
        "retrieval_score": round(float(mapping.get("retrieval_score") or 0), 4),
        "supporting_fact_count": len(supporting_ids),
        "supporting_fact_ids": supporting_ids,
        "source_locations": source_locations,
        "verification_status": "not_run",
        "primary_allowed": False,
        "grade_eligible": False,
        "grade_status": "not_assessed",
        "recommendation_status": "derived_recommendation",
        "provenance": {
            "origin": "sheet_retrieval",
            "engine": "ParameterRetrievalEngine+SPIPMappingEngine",
            "version": SHEET_RETRIEVAL_VERSION,
            "query_sha256": query_sha256,
            "advanced_rag_used": advanced_rag_used,
            "authority": SHEET_RETRIEVAL_AUTHORITY,
        },
    }


def _fallback_metadata(
    *,
    attempted: bool,
    query_sha256: str | None,
    deterministic_sufficient: bool,
    advanced_rag_enabled: bool,
    advanced_rag_used: bool,
    provider_status: str,
    candidate_count: int,
    candidate_limit: int,
) -> dict[str, Any]:
    return {
        "attempted": attempted,
        "origin": "sheet_retrieval",
        "version": SHEET_RETRIEVAL_VERSION,
        "query_sha256": query_sha256,
        "query_content_persisted": False,
        "candidate_limit": candidate_limit,
        "candidate_count": candidate_count,
        "deterministic_sufficient": deterministic_sufficient,
        "advanced_rag_enabled": advanced_rag_enabled,
        "advanced_rag_used": advanced_rag_used,
        "provider_status": provider_status,
        "authority": SHEET_RETRIEVAL_AUTHORITY,
    }


def _provider_status(statuses: list[str]) -> str:
    if not statuses:
        return "not_called"
    if EngineStatus.FAILED.value in statuses:
        return "failed"
    return "completed"


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
