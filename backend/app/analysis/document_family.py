from __future__ import annotations

from collections import Counter
import re
from time import perf_counter
from typing import Any

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.document_family_registry import (
    DOCUMENT_EVIDENCE_ROLES,
    DOCUMENT_FAMILIES,
    RISK_MATRIX_HEADER_ALIASES,
    parameter_key,
    parameter_scope_for_family,
)


NON_GRADE_FAMILIES = frozenset({
    "transmittal_letter",
    "meeting_invitation",
    "photo_documentation",
    "template_form",
})

FAMILY_LABELS = {
    "risk_matrix": "Matriks Peta Risiko",
    "monitoring_report": "Laporan Monitoring Risiko",
    "risk_policy": "Kebijakan Manajemen Risiko",
    "review_audit": "Laporan Reviu atau Audit",
    "transmittal_letter": "Nota Dinas atau Surat Pengantar",
    "meeting_invitation": "Undangan Rapat",
    "meeting_minutes": "Notulen atau Berita Acara",
    "photo_documentation": "Dokumentasi Foto",
    "template_form": "Formulir atau Template Kosong",
    "unknown": "Jenis Dokumen Belum Dikenali",
}


def apply_document_evidence_role(
    units: list[dict],
    document_family: dict,
) -> list[dict]:
    """Persist the document authority ceiling on every normalized unit."""
    document_role = str(document_family.get("evidence_role") or "reject")
    updated: list[dict] = []
    for unit in units:
        metadata = dict(unit.get("metadata") or {})
        template_only = bool(
            (metadata.get("template_detection") or {}).get("template_only")
        )
        unit_role = "optional" if template_only else document_role
        metadata.update({
            "document_evidence_role": document_role,
            "unit_evidence_role": unit_role,
            "unit_evidence_role_method": (
                "template_unit_cap_v1" if template_only
                else "document_family_inheritance_v1"
            ),
        })
        updated.append({**unit, "metadata": metadata})
    return updated


class DocumentFamilyEngine:
    name = "document_family"
    version = "document-family-v1"

    def run(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        coverage_ledger: dict,
        template_ledger: dict,
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        processed_units = [
            unit for unit in units
            if unit.get("status") in {"processed", "partial"}
        ]
        text = "\n".join(str(unit.get("text") or "") for unit in processed_units)
        normalized_text = _normalize(text)
        normalized_name = _normalize(identity.file_name)
        features = _document_features(
            identity,
            units,
            normalized_text,
            coverage_ledger,
            template_ledger,
        )
        family, confidence, reasons = _classify_family(
            identity,
            normalized_text,
            normalized_name,
            features,
        )
        evidence_role, grade_eligible = _family_authority(family, features)
        grade_status = (
            "not_applicable" if family in NON_GRADE_FAMILIES
            else "direction_only" if grade_eligible
            else "blocked"
        )
        grade_block_reasons = (
            ["document_family_has_no_standalone_grade"]
            if grade_status == "not_applicable"
            else [
                "document_family_unknown"
                if family == "unknown"
                else "document_family_not_grade_eligible"
            ]
            if grade_status == "blocked"
            else []
        )
        relationship_hints = _relationship_hints(family, normalized_text)
        scope = parameter_scope_for_family(family)
        warnings: list[str] = []
        if features["relevant_coverage_ratio"] < 0.70:
            warnings.append(
                "Coverage bagian relevan di bawah 70%; confidence keputusan dibatasi dan Grade diblokir."
            )
        if family == "unknown":
            warnings.append(
                "Jenis dokumen belum cukup terkonfirmasi; retrieval hanya eksploratif dan memerlukan review manusia."
            )
        if family == "risk_matrix" and features["unprocessed_relevant_unit_count"]:
            warnings.append(
                "Sebagian sheet matriks yang relevan belum diproses; Grade belum boleh dinilai."
            )
        contract = {
            "contract_version": "document-family-contract-v1",
            "family": family,
            "family_label": FAMILY_LABELS[family],
            "family_confidence": round(confidence, 4),
            "evidence_role": evidence_role,
            "grade_eligible": grade_eligible,
            "grade_status": grade_status,
            "grade_block_reasons": grade_block_reasons,
            "allowed_parameter_keys": scope["primary_parameter_keys"],
            "secondary_parameter_keys": scope["secondary_parameter_keys"],
            "parameter_scope": scope,
            "reasons": reasons,
            "warnings": warnings,
            "features": features,
            "relationship_hints": relationship_hints,
        }
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=(
                EngineStatus.COMPLETED
                if family != "unknown" else EngineStatus.PARTIAL
            ),
            input_checksum=identity.sha256,
            input_refs=[f"unit:{unit.get('unit_key')}" for unit in units],
            output_refs=[f"document-family:{family}"],
            coverage={
                "required": int(features["relevant_unit_count"]),
                "processed": int(features["processed_relevant_unit_count"]),
                "failed": int(features["failed_relevant_unit_count"]),
                "pending": int(features["unprocessed_relevant_unit_count"]),
            },
            warnings=warnings,
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "family_confidence": round(confidence, 4),
                "relevant_coverage_ratio": features["relevant_coverage_ratio"],
            },
            output=contract,
        ).finish()
        return contract, result


class GradeEligibilityGate:
    name = "grade_eligibility_gate"
    version = "grade-eligibility-v1"

    def run(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        facts: list[dict],
        document_family: dict,
        coverage_ledger: dict,
        *,
        family_confidence_threshold: float = 0.70,
        relevant_coverage_threshold: float = 0.70,
        ambiguity_margin_threshold: float = 0.08,
        high_confidence_threshold: float = 0.80,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        family = str(document_family.get("family") or "unknown")
        family_confidence = float(document_family.get("family_confidence") or 0)
        document_grade_status = str(document_family.get("grade_status") or "") or (
            "not_applicable" if family in NON_GRADE_FAMILIES
            else "blocked" if not document_family.get("grade_eligible")
            else "direction_only"
        )
        relevant_coverage = float(
            coverage_ledger.get("relevant_coverage_ratio")
            if coverage_ledger.get("relevant_coverage_ratio") is not None
            else float(coverage_ledger.get("coverage_percentage") or 0) / 100
        )
        sorted_scores = sorted(
            (float(item.get("mapping_score") or 0) for item in mappings),
            reverse=True,
        )
        top_margin = (
            sorted_scores[0] - sorted_scores[1]
            if len(sorted_scores) > 1 else sorted_scores[0] if sorted_scores else 0
        )
        ambiguous = bool(len(sorted_scores) > 1 and top_margin < ambiguity_margin_threshold)
        allowed = set((document_family.get("parameter_scope") or {}).get("allowed_parameter_keys") or [])
        exploratory = bool((document_family.get("parameter_scope") or {}).get("exploratory"))
        contradiction_count = sum(
            str(fact.get("evidence_role") or "") == "contradictory" for fact in facts
        )
        updated: list[dict] = []
        for mapping in mappings:
            compatible = exploratory or parameter_key(mapping) in allowed
            confidence, components = calibrated_decision_confidence(
                mapping,
                family_confidence=family_confidence,
                relevant_coverage_ratio=relevant_coverage,
                top_margin=top_margin,
                contradiction_count=contradiction_count,
                important_units_unprocessed=int(
                    coverage_ledger.get("unprocessed_relevant_units")
                    or coverage_ledger.get("relevant_pending_units")
                    or 0
                ),
                template_only=family == "template_form",
                compatible=compatible,
                family_confidence_threshold=family_confidence_threshold,
                relevant_coverage_threshold=relevant_coverage_threshold,
                ambiguity_margin_threshold=ambiguity_margin_threshold,
            )
            block_reasons: list[str] = []
            if family in NON_GRADE_FAMILIES:
                block_reasons.append("document_family_has_no_standalone_grade")
                grade_status = "not_applicable"
            else:
                if family == "unknown":
                    block_reasons.append("document_family_unknown")
                if not compatible:
                    block_reasons.append("family_parameter_incompatible")
                if family_confidence < family_confidence_threshold:
                    block_reasons.append("document_family_confidence_below_threshold")
                if relevant_coverage < relevant_coverage_threshold:
                    block_reasons.append("relevant_coverage_below_threshold")
                if ambiguous:
                    block_reasons.append("parameter_candidates_ambiguous")
                if not document_family.get("grade_eligible"):
                    block_reasons.append("document_family_not_grade_eligible")
                grade_status = "blocked" if block_reasons else "direction_only"
            eligible = grade_status == "direction_only"
            decision_status = (
                "ambiguous" if ambiguous
                else "needs_review" if grade_status in {"blocked", "not_applicable"}
                else "candidate"
            )
            updated.append({
                **mapping,
                "status": (
                    "needs_review" if decision_status != "candidate"
                    else mapping.get("status") or "candidate"
                ),
                "document_role": document_family.get("evidence_role") or "reject",
                "document_family": family,
                "raw_retrieval_score": float(mapping.get("retrieval_score") or 0),
                "calibrated_decision_confidence": confidence,
                "decision_confidence_label": confidence_label(
                    confidence,
                    ambiguous=ambiguous,
                    high_threshold=high_confidence_threshold,
                ),
                "confidence_components": components,
                "decision_status": decision_status,
                "grade_eligible": eligible,
                "grade_status": grade_status,
                "grade_block_reasons": list(dict.fromkeys(block_reasons)),
                "family_parameter_compatible": compatible,
            })
        blocked_count = sum(not item["grade_eligible"] for item in updated)
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=(
                EngineStatus.COMPLETED
                if updated and not blocked_count
                else EngineStatus.PARTIAL if updated
                else EngineStatus.SKIPPED
            ),
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{parameter_key(item)}" for item in mappings],
            output_refs=[f"grade-gate:{parameter_key(item)}" for item in updated],
            coverage={"required": len(mappings), "processed": len(updated), "failed": 0},
            warnings=(
                [f"Grade diblokir atau tidak berlaku pada {blocked_count} kandidat."]
                if blocked_count else []
            ),
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "blocked_count": blocked_count,
                "ambiguous": int(ambiguous),
                "top_candidate_margin": round(top_margin, 4),
            },
            output={
                "family": family,
                "document_grade_status": document_grade_status,
                "grade_eligible_count": sum(item["grade_eligible"] for item in updated),
                "blocked_count": blocked_count,
                "ambiguous": ambiguous,
                "top_candidate_margin": round(top_margin, 4),
                "thresholds": {
                    "family_confidence": family_confidence_threshold,
                    "relevant_coverage": relevant_coverage_threshold,
                    "ambiguity_margin": ambiguity_margin_threshold,
                    "high_confidence": high_confidence_threshold,
                },
            },
        ).finish()
        return updated, result


def calibrated_decision_confidence(
    mapping: dict,
    *,
    family_confidence: float,
    relevant_coverage_ratio: float,
    top_margin: float,
    contradiction_count: int,
    important_units_unprocessed: int,
    template_only: bool,
    compatible: bool,
    family_confidence_threshold: float,
    relevant_coverage_threshold: float,
    ambiguity_margin_threshold: float,
) -> tuple[float, dict[str, Any]]:
    retrieval = _bounded(float(mapping.get("retrieval_score") or 0))
    mapping_score = _bounded(float(mapping.get("mapping_score") or 0))
    family_score = _bounded(family_confidence)
    coverage = _bounded(relevant_coverage_ratio)
    margin_score = _bounded(top_margin / 0.25)
    fact_support = _bounded(len(mapping.get("supporting_fact_ids") or []) / 3)
    base = (
        retrieval * 0.25
        + mapping_score * 0.15
        + family_score * 0.20
        + coverage * 0.15
        + margin_score * 0.10
        + fact_support * 0.15
    )
    contradiction_penalty = min(0.20, contradiction_count * 0.08)
    unprocessed_penalty = min(0.15, important_units_unprocessed * 0.03)
    template_penalty = 0.45 if template_only else 0.0
    compatibility_penalty = 1.0 if not compatible else 0.0
    confidence = _bounded(
        base
        - contradiction_penalty
        - unprocessed_penalty
        - template_penalty
        - compatibility_penalty
    )
    caps: list[str] = []
    if relevant_coverage_ratio < relevant_coverage_threshold:
        confidence = min(confidence, 0.59)
        caps.append("low_relevant_coverage")
    if family_confidence < family_confidence_threshold:
        confidence = min(confidence, 0.59)
        caps.append("low_family_confidence")
    if top_margin < ambiguity_margin_threshold:
        confidence = min(confidence, 0.59)
        caps.append("ambiguous_candidate_margin")
    return round(confidence, 4), {
        "formula_version": "calibrated-decision-confidence-v1",
        "raw_retrieval_score": round(retrieval, 4),
        "mapping_score": round(mapping_score, 4),
        "document_family_confidence": round(family_score, 4),
        "relevant_coverage_ratio": round(coverage, 4),
        "top_candidate_margin": round(top_margin, 4),
        "margin_score": round(margin_score, 4),
        "fact_support_score": round(fact_support, 4),
        "contradiction_penalty": round(contradiction_penalty, 4),
        "unprocessed_relevant_unit_penalty": round(unprocessed_penalty, 4),
        "template_penalty": round(template_penalty, 4),
        "family_parameter_compatibility": compatible,
        "confidence_caps": caps,
    }


def confidence_label(
    confidence: float,
    *,
    ambiguous: bool,
    high_threshold: float,
) -> str:
    if ambiguous:
        return "ambiguous"
    if confidence >= high_threshold:
        return "high"
    if confidence >= 0.60:
        return "medium"
    return "needs_review"


def _document_features(
    identity: DocumentIdentity,
    units: list[dict],
    normalized_text: str,
    coverage_ledger: dict,
    template_ledger: dict,
) -> dict:
    sheet_features = []
    for unit in units:
        if unit.get("unit_type") != "sheet":
            continue
        metadata = unit.get("metadata") or {}
        detailed = metadata.get("risk_matrix_features")
        sheet_features.append(detailed if isinstance(detailed, dict) else metadata)
    header_categories = sorted({
        category
        for item in sheet_features
        for category in (
            item.get("risk_header_categories")
            or item.get("header_categories")
            or []
        )
    })
    text_header_categories = sorted(
        category
        for category, aliases in RISK_MATRIX_HEADER_ALIASES.items()
        if any(alias in normalized_text for alias in aliases)
    )
    header_categories = sorted(set(header_categories) | set(text_header_categories))
    relevant_units = [
        unit for unit in units
        if (unit.get("metadata") or {}).get("risk_matrix_relevant")
    ]
    if not relevant_units:
        relevant_units = list(units)
    relevant_processed = sum(
        unit.get("status") == "processed" for unit in relevant_units
    )
    relevant_failed = sum(unit.get("status") == "failed" for unit in relevant_units)
    relevant_total = len(relevant_units)
    relevant_ratio = (
        relevant_processed / relevant_total if relevant_total else 0.0
    )
    checked = int(template_ledger.get("checked_units") or 0)
    template_units = int(template_ledger.get("template_only_units") or 0)
    risk_statements = {
        str(value).strip().casefold()
        for item in sheet_features
        for value in item.get("risk_statement_values") or []
        if str(value).strip()
    }
    return {
        "file_kind": identity.file_kind,
        "sheet_count": sum(unit.get("unit_type") == "sheet" for unit in units),
        "relevant_sheet_count": sum(
            unit.get("unit_type") == "sheet"
            and bool((unit.get("metadata") or {}).get("risk_matrix_relevant"))
            for unit in units
        ),
        "risk_header_categories": header_categories,
        "risk_header_category_count": len(header_categories),
        "substantive_row_count": sum(
            int(item.get("substantive_row_count") or 0) for item in sheet_features
        ),
        "unique_risk_statement_count": len(risk_statements),
        "risk_statement_values": sorted(risk_statements)[:20],
        "filled_rtp_count": sum(int(item.get("filled_rtp_count") or 0) for item in sheet_features),
        "filled_likelihood_count": sum(int(item.get("filled_likelihood_count") or 0) for item in sheet_features),
        "filled_impact_count": sum(int(item.get("filled_impact_count") or 0) for item in sheet_features),
        "filled_risk_owner_count": sum(int(item.get("filled_risk_owner_count") or 0) for item in sheet_features),
        "filled_cell_ratio": round(max(
            [float(item.get("filled_cell_ratio") or 0) for item in sheet_features] or [0]
        ), 4),
        "placeholder_ratio": round(max(
            [float(item.get("placeholder_ratio") or 0) for item in sheet_features] or [0]
        ), 4),
        "contains_example_data": any(
            bool(item.get("contains_example_data")) for item in sheet_features
        ),
        "formula_only": bool(sheet_features) and all(
            bool(item.get("formula_only")) for item in sheet_features
        ),
        "relevant_unit_count": relevant_total,
        "processed_relevant_unit_count": relevant_processed,
        "failed_relevant_unit_count": relevant_failed,
        "unprocessed_relevant_unit_count": relevant_total - relevant_processed,
        "relevant_coverage_ratio": round(
            float(coverage_ledger.get("relevant_coverage_ratio") or relevant_ratio), 4
        ),
        "template_unit_ratio": round(template_units / checked, 4) if checked else 0.0,
        "has_actual_rows": sum(
            int(item.get("substantive_row_count") or 0) for item in sheet_features
        ) >= 2,
        "transmittal_marker_count": _count_phrases(normalized_text, (
            "nota dinas", "nomor", "sifat", "lampiran", "hal", "perihal",
            "kepada", "dari", "tanggal", "bersama ini disampaikan", "terlampir",
        )),
        "delivery_phrase_present": any(
            phrase in normalized_text
            for phrase in ("bersama ini disampaikan", "terlampir", "disampaikan laporan")
        ),
        "substantive_risk_text_marker_count": _count_phrases(normalized_text, (
            "pernyataan risiko",
            "penyebab risiko",
            "nilai kemungkinan",
            "nilai dampak",
            "level risiko",
            "rencana tindak pengendalian",
            "pemilik risiko",
            "risiko residual",
        )),
    }


def _classify_family(
    identity: DocumentIdentity,
    text: str,
    file_name: str,
    features: dict,
) -> tuple[str, float, list[str]]:
    risk_structure = (
        identity.file_kind == "xlsx"
        and int(features["risk_header_category_count"]) >= 4
    )
    if risk_structure and features["has_actual_rows"] and (
        int(features["unique_risk_statement_count"]) >= 2
        or int(features["substantive_row_count"]) >= 3
    ):
        confidence = min(
            0.98,
            0.76
            + min(0.12, int(features["risk_header_category_count"]) * 0.015)
            + min(0.08, int(features["substantive_row_count"]) * 0.01),
        )
        return "risk_matrix", confidence, [
            "Struktur workbook memuat kombinasi header matriks risiko.",
            "Ditemukan beberapa baris risiko aktual, bukan hanya header atau contoh pengisian.",
        ]
    if risk_structure and not features["has_actual_rows"]:
        return "template_form", 0.94, [
            "Struktur formulir risiko ditemukan, tetapi tidak terdapat beberapa baris risiko aktual.",
            "Dokumen diperlakukan sebagai template sampai isinya dikonfirmasi.",
        ]
    if features["template_unit_ratio"] >= 0.70:
        return "template_form", 0.90, [
            "Sebagian besar unit berisi petunjuk atau placeholder tanpa bukti aktivitas aktual."
        ]
    if (
        features["transmittal_marker_count"] >= 4
        and features["delivery_phrase_present"]
        and not features["has_actual_rows"]
        and int(features["substantive_risk_text_marker_count"]) < 3
    ):
        filename_signal = int(any(
            marker in file_name for marker in ("nota dinas", "penyampaian", "surat pengantar")
        ))
        return "transmittal_letter", min(0.98, 0.88 + filename_signal * 0.04), [
            "Struktur surat/nota dinas dan frasa penyampaian atau lampiran ditemukan.",
            "Isi utama bersifat pengantar dan tidak memuat register risiko substantif.",
        ]
    if _count_phrases(text, ("undangan", "hari", "tanggal", "tempat", "acara", "kepada")) >= 4:
        return "meeting_invitation", 0.88, ["Struktur undangan rapat ditemukan dalam isi dokumen."]
    if _count_phrases(text, ("notulen", "berita acara", "peserta", "agenda", "hasil rapat", "kesimpulan")) >= 3:
        return "meeting_minutes", 0.84, ["Struktur notulen atau berita acara ditemukan."]
    if identity.file_kind == "image" and _count_phrases(text + " " + file_name, ("foto", "dokumentasi", "kegiatan")) >= 1:
        return "photo_documentation", 0.82, ["Dokumen gambar dikenali sebagai dokumentasi foto."]
    if _count_phrases(text, ("laporan monitoring", "laporan pemantauan", "semester", "triwulan", "realisasi rtp", "efektivitas pengendalian")) >= 2:
        return "monitoring_report", 0.86, [
            "Isi memuat struktur laporan monitoring dan hasil/realisasi pengendalian."
        ]
    if _count_phrases(text, ("kebijakan manajemen risiko", "pedoman manajemen risiko", "keputusan", "ditetapkan", "ruang lingkup")) >= 3:
        return "risk_policy", 0.84, ["Isi memuat ketentuan dan penetapan kebijakan manajemen risiko."]
    if _count_phrases(text, ("reviu", "review", "audit", "hasil pemeriksaan", "temuan", "rekomendasi", "auditor", "apip")) >= 3:
        return "review_audit", 0.84, ["Isi memuat prosedur dan hasil reviu/audit."]
    return "unknown", 0.45, [
        "Sinyal struktur dan isi belum cukup kuat untuk menetapkan satu jenis dokumen."
    ]


def _family_authority(family: str, features: dict) -> tuple[str, bool]:
    if family == "risk_matrix":
        substantive = bool(features["has_actual_rows"])
        return ("primary", True) if substantive else ("optional", False)
    if family in {"monitoring_report", "risk_policy", "review_audit", "meeting_minutes"}:
        return "primary", family != "meeting_minutes"
    if family == "transmittal_letter":
        return "supporting", False
    if family in {"meeting_invitation", "photo_documentation"}:
        return "supporting", False
    if family == "template_form":
        return "optional", False
    return "reject", False


def _relationship_hints(family: str, text: str) -> list[dict]:
    if family != "transmittal_letter":
        return []
    referenced = []
    if "peta risiko" in text or "matriks risiko" in text:
        referenced.append("risk_matrix")
    if "laporan mr" in text or "laporan monitoring" in text or "laporan pemantauan" in text:
        referenced.append("monitoring_report")
    period_match = re.search(r"\b((?:19|20)\d{2}\s*[-–]\s*(?:19|20)?\d{2})\b", text)
    return [{
        "relation_type": "transmits_or_references",
        "referenced_document_types": referenced,
        "referenced_period": period_match.group(1).replace(" ", "") if period_match else None,
    }]


def _normalize(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _count_phrases(value: str, phrases: tuple[str, ...]) -> int:
    return sum(
        bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", value))
        for phrase in phrases
    )


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


assert set(FAMILY_LABELS) == DOCUMENT_FAMILIES
assert DOCUMENT_EVIDENCE_ROLES == {"primary", "supporting", "optional", "reject"}
