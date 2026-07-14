from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from time import perf_counter

from app.analysis import PIPELINE_VERSION, RULE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.provider import VerificationModelProvider


GRADE_ORDER = ("E", "D", "C", "B", "A")
FALLBACK_REQUIREMENTS = {
    "E": {"policy"},
    "D": {"policy", "socialization"},
    "C": {"policy", "implementation"},
    "B": {"policy", "implementation", "evaluation"},
    "A": {"policy", "implementation", "evaluation", "improvement"},
}
STAGE_TERMS = {
    "policy": ("kebijakan", "pedoman", "peraturan", "sop", "ditetapkan", "keputusan"),
    "socialization": ("sosialisasi", "disosialisasikan", "daftar hadir", "undangan"),
    "implementation": ("implementasi", "dilaksanakan", "pelaksanaan", "penerapan"),
    "evaluation": ("evaluasi", "pemantauan", "monitoring", "reviu", "review", "berkala"),
    "improvement": ("perbaikan", "tindak lanjut", "penyempurnaan", "ditindaklanjuti"),
}
STAGE_SOURCE_TYPES = {
    "policy": "policy_document",
    "socialization": "socialization_record",
    "implementation": "implementation_record",
    "evaluation": "evaluation_report",
    "improvement": "improvement_record",
}
SOURCE_TYPE_TERMS = {
    "policy_document": ("kebijakan", "pedoman", "peraturan", "sop", "keputusan"),
    "socialization_record": ("sosialisasi", "daftar hadir", "undangan", "bahan paparan"),
    "implementation_record": ("pelaksanaan", "implementasi", "realisasi", "bukti pelaksanaan"),
    "evaluation_report": ("evaluasi", "monitoring", "pemantauan", "reviu", "hasil pemeriksaan"),
    "improvement_record": ("tindak lanjut", "perbaikan", "penyempurnaan", "revisi"),
}
GRADE_PREREQUISITES = {"E": None, "D": "E", "C": "E", "B": "C", "A": "B"}
PERIOD_TERMS = ("periode", "tahun", "triwulan", "semester", "bulanan", "tahunan")
PLAN_TERMS = ("rencana", "akan dilaksanakan", "akan dilakukan", "target", "jadwal")
RESULT_TERMS = ("telah", "selesai", "realisasi", "hasil", "dilaksanakan", "ditetapkan")
CANONICAL_DITJEN_PDP = "Direktorat Jenderal Pembangunan Desa dan Perdesaan"


class DomainRuleGradeEngine:
    name = "domain_rule_grade"
    version = RULE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        facts: list[dict],
        rule_approvals: list[dict] | None = None,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        facts_by_id = {int(fact["id"]): fact for fact in facts if fact.get("id") is not None}
        assessments = []
        approval_by_key = {
            (
                item["kk_id"], item["kode"], item["detail_kode"],
                item["grade"], item["rule_version"],
            ): item
            for item in (rule_approvals or [])
        }
        for mapping in mappings:
            gate_present = "grade_eligible" in mapping or "grade_status" in mapping
            mapping_grade_eligible = (
                bool(mapping.get("grade_eligible")) if gate_present else True
            )
            gate_status = str(mapping.get("grade_status") or "direction_only")
            gate_block_reasons = list(mapping.get("grade_block_reasons") or [])
            if not mapping_grade_eligible or gate_status in {"not_applicable", "blocked"}:
                assessments.append({
                    "mapping_candidate_id": mapping.get("id"),
                    "kk_id": mapping["kk_id"],
                    "kode": mapping["kode"],
                    "detail_kode": mapping["detail_kode"],
                    "candidate_grade": None,
                    "grade_ceiling": None,
                    "rule_version": RULE_VERSION,
                    "rule_trace": {
                        "approval_status": "not_evaluated",
                        "source": "document_family_grade_eligibility_gate",
                        "grade_status": gate_status,
                        "grade_eligible": False,
                        "grade_block_reasons": gate_block_reasons,
                        "rules": [],
                    },
                    "missing_requirements": gate_block_reasons,
                    "primary_allowed": False,
                    "grade_eligible": False,
                    "grade_status": gate_status,
                    "grade_block_reasons": gate_block_reasons,
                })
                continue
            supporting = [facts_by_id[item] for item in mapping.get("supporting_fact_ids") or [] if item in facts_by_id]
            present_stages = {
                str(fact.get("fact_type"))
                for fact in supporting
                if fact.get("fact_type") in STAGE_TERMS
            }
            present_source_types = set().union(
                *(infer_source_types(fact) for fact in supporting),
            ) if supporting else set()
            context_resolution = resolve_evidence_context(identity, supporting, facts)
            periods = set(context_resolution["period"]["values"])
            organizations = set(context_resolution["organization"]["values"])
            active_disqualifiers = detect_disqualifiers(supporting)
            compiled_rules = compile_parameter_rules(mapping.get("grades") or [])
            approved_rule_count = 0
            for grade, rule in compiled_rules.items():
                approval = approval_by_key.get(
                    (mapping["kk_id"], mapping["kode"], mapping["detail_kode"], grade, RULE_VERSION)
                )
                approved = bool(
                    approval
                    and approval.get("status") == "approved"
                    and approval.get("rule_checksum") == rule_checksum(rule)
                )
                rule["approval_status"] = "approved" if approved else "draft"
                rule["rule_checksum"] = rule_checksum(rule)
                approved_rule_count += int(approved)
            parameter_rules_approved = bool(compiled_rules) and approved_rule_count == len(compiled_rules)
            passed_grade = None
            passed_grades: set[str] = set()
            traces = []
            for grade in GRADE_ORDER:
                rule = compiled_rules.get(grade) or {
                    "grade": grade,
                    "required_stages": sorted(FALLBACK_REQUIREMENTS[grade]),
                    "required_source_types": sorted(
                        STAGE_SOURCE_TYPES[stage] for stage in FALLBACK_REQUIREMENTS[grade]
                    ),
                    "period_policy": "required_single",
                    "organization_policy": "required_single",
                    "prerequisite_grade": GRADE_PREREQUISITES[grade],
                    "disqualifiers": ["template_only", "plan_without_result"],
                    "effective_date": None,
                    "criterion": "",
                    "source": "generic_fallback",
                    "approval_status": "draft",
                    "rule_checksum": None,
                }
                missing = sorted(set(rule["required_stages"]) - present_stages)
                missing_sources = sorted(
                    set(rule.get("required_source_types") or []) - present_source_types
                )
                prerequisite = rule.get("prerequisite_grade")
                prerequisite_ok = not prerequisite or prerequisite in passed_grades
                period_ok = rule.get("period_policy") != "required_single" or len(periods) == 1
                organization_ok = (
                    rule.get("organization_policy") != "required_single"
                    or len(organizations) == 1
                )
                blocking_disqualifiers = sorted(
                    set(rule.get("disqualifiers") or []) & active_disqualifiers
                )
                effective_date_ok = effective_date_allows_periods(
                    rule.get("effective_date"), periods
                )
                missing_requirements = [
                    *[f"stage:{item}" for item in missing],
                    *[f"source_type:{item}" for item in missing_sources],
                ]
                if not prerequisite_ok:
                    missing_requirements.append(f"prerequisite_grade:{prerequisite}")
                if not period_ok:
                    missing_requirements.append("period:required_single")
                if not organization_ok:
                    missing_requirements.append("organization:required_single")
                if not effective_date_ok:
                    missing_requirements.append("period:before_effective_date")
                missing_requirements.extend(
                    f"disqualifier:{item}" for item in blocking_disqualifiers
                )
                passed = not missing_requirements
                traces.append({
                    **rule,
                    "present_stages": sorted(present_stages),
                    "present_source_types": sorted(present_source_types),
                    "periods": sorted(periods),
                    "organizations": sorted(organizations),
                    "active_disqualifiers": sorted(active_disqualifiers),
                    "missing_stages": missing,
                    "missing_source_types": missing_sources,
                    "missing_requirements": missing_requirements,
                    "prerequisite_ok": prerequisite_ok,
                    "period_ok": period_ok,
                    "organization_ok": organization_ok,
                    "effective_date_ok": effective_date_ok,
                    "passed": passed,
                })
                if passed:
                    passed_grades.add(grade)
                    passed_grade = grade
            next_missing = []
            for trace in traces:
                if trace["grade"] == passed_grade:
                    continue
                if trace["missing_requirements"]:
                    next_missing = trace["missing_requirements"]
                    break
            primary_allowed = bool(parameter_rules_approved and passed_grade)
            # Independent Verification runs after this engine. Even an approved
            # rule remains a direction until every verification result passes.
            final_grade_status = "direction_only" if passed_grade else "blocked"
            final_block_reasons = [] if passed_grade else ["parameter_requirements_not_met"]
            assessments.append(
                {
                    "mapping_candidate_id": mapping.get("id"),
                    "kk_id": mapping["kk_id"],
                    "kode": mapping["kode"],
                    "detail_kode": mapping["detail_kode"],
                    "candidate_grade": passed_grade,
                    "grade_ceiling": passed_grade,
                    "rule_version": RULE_VERSION,
                    "rule_trace": {
                        "approval_status": "approved" if parameter_rules_approved else "draft",
                        "source": "spip_parameters.json + conservative fallback",
                        "present_stages": sorted(present_stages),
                        "context_resolution": context_resolution,
                        "plan_result_evidence_found": has_result_evidence(supporting),
                        "grade_status": final_grade_status,
                        "grade_eligible": True,
                        "grade_block_reasons": final_block_reasons,
                        "rules": traces,
                    },
                    "missing_requirements": next_missing,
                    "primary_allowed": primary_allowed,
                    "grade_eligible": True,
                    "grade_status": final_grade_status,
                    "grade_block_reasons": final_block_reasons,
                }
            )
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=(
                EngineStatus.COMPLETED
                if assessments and all(item["primary_allowed"] for item in assessments)
                else EngineStatus.PARTIAL if assessments else EngineStatus.SKIPPED
            ),
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{item.get('id')}" for item in mappings],
            output_refs=[f"grade:{item.get('mapping_candidate_id')}" for item in assessments],
            coverage={"required": len(mappings), "processed": len(mappings), "failed": 0},
            warnings=(
                ["Sebagian rule parameter masih draft; grade terkait diblokir sampai disahkan domain owner."]
                if assessments and not all(item["primary_allowed"] for item in assessments)
                else [] if assessments else ["Tidak ada mapping untuk dinilai."]
            ),
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "assessment_count": len(assessments),
            },
            output={
                "assessment_count": len(assessments),
                "approved_assessment_count": sum(item["primary_allowed"] for item in assessments),
            },
        ).finish()
        return assessments, result


def finalize_grade_statuses(
    assessments: list[dict],
    verification_results: list[dict],
) -> list[dict]:
    """Promote a Grade to supported only after all verification passes."""
    verification_by_mapping: dict[int, list[dict]] = {}
    for item in verification_results:
        mapping_id = item.get("mapping_candidate_id")
        if mapping_id is not None:
            verification_by_mapping.setdefault(int(mapping_id), []).append(item)

    finalized: list[dict] = []
    for assessment in assessments:
        mapping_id = assessment.get("mapping_candidate_id")
        current_status = str(assessment.get("grade_status") or "blocked")
        checks = verification_by_mapping.get(int(mapping_id), []) if mapping_id is not None else []
        verification_passed = bool(checks) and all(
            item.get("status") == "verified" for item in checks
        )
        supported = bool(
            current_status in {"direction_only", "supported"}
            and assessment.get("candidate_grade")
            and assessment.get("primary_allowed")
            and verification_passed
        )
        final_status = (
            "supported" if supported
            else "direction_only"
            if current_status in {"direction_only", "supported"}
            else current_status
        )
        block_reasons = [
            reason
            for reason in (assessment.get("grade_block_reasons") or [])
            if reason != "independent_verification_not_passed"
        ]
        if (
            final_status == "direction_only"
            and assessment.get("candidate_grade")
            and assessment.get("primary_allowed")
            and not verification_passed
        ):
            block_reasons.append("independent_verification_not_passed")
        block_reasons = list(dict.fromkeys(block_reasons))
        finalized.append({
            **assessment,
            "grade_status": final_status,
            "grade_block_reasons": block_reasons,
            "rule_trace": {
                **(assessment.get("rule_trace") or {}),
                "grade_status": final_status,
                "grade_block_reasons": block_reasons,
                "verification_passed": verification_passed,
            },
        })
    return finalized


def compile_parameter_rules(grades: list[dict]) -> dict[str, dict]:
    compiled = {}
    for item in grades:
        grade = str(item.get("grade") or "").strip().upper()
        if grade not in GRADE_ORDER:
            continue
        criterion = " ".join(
            str(item.get(key) or "")
            for key in ("kriteria", "penjelasan", "cara_pengujian")
        ).strip()
        lowered = criterion.lower()
        inferred = {
            stage
            for stage, terms in STAGE_TERMS.items()
            if any(term in lowered for term in terms)
        }
        required = set(FALLBACK_REQUIREMENTS[grade]) | inferred
        required_source_types = {
            STAGE_SOURCE_TYPES[stage]
            for stage in required
        }
        required_source_types.update(
            source_type
            for source_type, terms in SOURCE_TYPE_TERMS.items()
            if any(term in lowered for term in terms)
        )
        effective_date = str(
            item.get("effective_date") or item.get("tanggal_berlaku") or ""
        ).strip() or None
        compiled[grade] = {
            "grade": grade,
            "required_stages": sorted(required),
            "required_source_types": sorted(required_source_types),
            "period_policy": (
                "required_single" if any(term in lowered for term in PERIOD_TERMS)
                else "required_single"
            ),
            "organization_policy": "required_single",
            "prerequisite_grade": GRADE_PREREQUISITES[grade],
            "disqualifiers": ["template_only", "plan_without_result"],
            "effective_date": effective_date,
            "criterion": criterion[:1600],
            "source": "parameter_criterion_draft",
        }
    return compiled


def rule_checksum(rule: dict) -> str:
    stable = {
        "grade": rule.get("grade"),
        "required_stages": sorted(rule.get("required_stages") or []),
        "required_source_types": sorted(rule.get("required_source_types") or []),
        "period_policy": rule.get("period_policy") or "",
        "organization_policy": rule.get("organization_policy") or "",
        "prerequisite_grade": rule.get("prerequisite_grade"),
        "disqualifiers": sorted(rule.get("disqualifiers") or []),
        "effective_date": rule.get("effective_date"),
        "criterion": rule.get("criterion") or "",
        "source": rule.get("source") or "",
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def infer_source_types(fact: dict) -> set[str]:
    inferred = set()
    fact_type = str(fact.get("fact_type") or "")
    if fact_type in STAGE_SOURCE_TYPES:
        inferred.add(STAGE_SOURCE_TYPES[fact_type])
    text = " ".join(
        [
            str(fact.get("claim") or ""),
            *[
                str(source.get("source_quote") or "")
                for source in fact.get("sources") or []
            ],
        ]
    ).lower()
    inferred.update(
        source_type
        for source_type, terms in SOURCE_TYPE_TERMS.items()
        if any(term in text for term in terms)
    )
    return inferred


def detect_disqualifiers(facts: list[dict]) -> set[str]:
    disqualifiers = set()
    result_found = has_result_evidence(facts)
    for fact in facts:
        if str(fact.get("status") or "") == "template_only":
            disqualifiers.add("template_only")
        text = str(fact.get("claim") or "").lower()
        if (
            any(term in text for term in PLAN_TERMS)
            and not any(term in text for term in RESULT_TERMS)
            and not result_found
        ):
            disqualifiers.add("plan_without_result")
    return disqualifiers


def has_result_evidence(facts: list[dict]) -> bool:
    return any(
        str(fact.get("evidence_role") or "") != "contradictory"
        and str(fact.get("fact_type") or "") in {"implementation", "evaluation", "improvement"}
        and any(term in str(fact.get("claim") or "").lower() for term in RESULT_TERMS)
        for fact in facts
    )


def normalize_organization(value: str | None) -> str | None:
    normalized = " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))
    if not normalized:
        return None
    if "ditjen pdp" in normalized or "direktorat jenderal pembangunan desa dan perdesaan" in normalized:
        return CANONICAL_DITJEN_PDP
    return " ".join(word.capitalize() for word in normalized.split())


def resolve_evidence_context(
    identity: DocumentIdentity,
    supporting: list[dict],
    all_facts: list[dict],
) -> dict:
    direct_periods = {
        str(fact.get("period")) for fact in supporting if fact.get("period")
    }
    direct_organizations = {
        normalized
        for fact in supporting
        if (normalized := normalize_organization(fact.get("organization")))
    }

    period_values = set(direct_periods)
    period_source = "supporting_facts" if period_values else "unresolved"
    if not period_values:
        file_years = re.findall(r"\b(?:19|20)\d{2}\b", str(identity.file_name or ""))
        if file_years:
            period_values = {file_years[-1]}
            period_source = "document_filename"
        else:
            period_counts = Counter(
                str(fact.get("period")) for fact in all_facts if fact.get("period")
            )
            dominant_period = _dominant_context_value(period_counts, minimum_share=0.65)
            if dominant_period:
                period_values = {dominant_period}
                period_source = "document_consensus"

    organization_values = set(direct_organizations)
    organization_source = "supporting_facts" if organization_values else "unresolved"
    if not organization_values:
        organization_counts = Counter(
            normalized
            for fact in all_facts
            if (normalized := normalize_organization(fact.get("organization")))
        )
        dominant_organization = _dominant_context_value(
            organization_counts,
            minimum_share=0.50,
        )
        if dominant_organization:
            organization_values = {dominant_organization}
            organization_source = "document_consensus"

    return {
        "period": {
            "values": sorted(period_values),
            "source": period_source,
            "inherited": period_source not in {"supporting_facts", "unresolved"},
        },
        "organization": {
            "values": sorted(organization_values),
            "source": organization_source,
            "inherited": organization_source not in {"supporting_facts", "unresolved"},
        },
    }


def _dominant_context_value(
    counts: Counter,
    *,
    minimum_share: float,
) -> str | None:
    if not counts:
        return None
    value, count = counts.most_common(1)[0]
    total = sum(counts.values())
    if count < 2 or count / max(1, total) < minimum_share:
        return None
    return str(value)


def effective_date_allows_periods(effective_date: str | None, periods: set[str]) -> bool:
    if not effective_date:
        return True
    try:
        effective_year = int(str(effective_date)[:4])
    except (TypeError, ValueError):
        return False
    numeric_periods = []
    for period in periods:
        try:
            numeric_periods.append(int(str(period)[:4]))
        except (TypeError, ValueError):
            return False
    return bool(numeric_periods) and min(numeric_periods) >= effective_year


def build_rule_catalog(parameters: list[dict], approvals: list[dict]) -> list[dict]:
    approval_by_key = {
        (item["kk_id"], item["kode"], item["detail_kode"], item["grade"], item["rule_version"]): item
        for item in approvals
    }
    catalog = []
    for parameter in parameters:
        compiled = compile_parameter_rules(parameter.get("grades") or [])
        for grade in GRADE_ORDER:
            rule = compiled.get(grade)
            if not rule:
                continue
            checksum = rule_checksum(rule)
            approval = approval_by_key.get(
                (
                    parameter["kk_id"], parameter["kode"], parameter["detail_kode"],
                    grade, RULE_VERSION,
                )
            )
            approval_valid = bool(
                approval and approval.get("status") == "approved"
                and approval.get("rule_checksum") == checksum
            )
            approval_rejected = bool(
                approval and approval.get("status") == "rejected"
                and approval.get("rule_checksum") == checksum
            )
            catalog.append(
                {
                    "kk_id": parameter["kk_id"],
                    "kode": parameter["kode"],
                    "detail_kode": parameter["detail_kode"],
                    "parameter_no": parameter.get("parameter_no"),
                    "uraian": parameter.get("uraian"),
                    "grade": grade,
                    "rule_version": RULE_VERSION,
                    "rule_checksum": checksum,
                    "rule_definition": rule,
                    "approval_status": (
                        "approved" if approval_valid
                        else "rejected" if approval_rejected
                        else "draft"
                    ),
                    "approval_stale": bool(
                        approval and approval.get("rule_checksum") != checksum
                    ),
                    "approval": approval,
                }
            )
    return catalog


class IndependentVerificationEngine:
    name = "independent_verification"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        coverage_ledger: dict,
        mappings: list[dict],
        assessments: list[dict],
        facts: list[dict],
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        facts_by_id = {int(fact["id"]): fact for fact in facts if fact.get("id") is not None}
        assessment_by_mapping = {
            int(item["mapping_candidate_id"]): item
            for item in assessments
            if item.get("mapping_candidate_id") is not None
        }
        results = []
        for mapping in mappings:
            mapping_id = int(mapping["id"])
            mapping_status_ok = mapping.get("status", "candidate") == "candidate"
            supporting = [facts_by_id[item] for item in mapping.get("supporting_fact_ids") or [] if item in facts_by_id]
            source_coverage_ok = bool(supporting) and all(
                fact.get("sources")
                and all(
                    source.get("source_location")
                    and str(source.get("source_quote") or "").strip()
                    and source.get("source_quote_verified") is True
                    for source in fact.get("sources") or []
                )
                for fact in supporting
            )
            assessment = assessment_by_mapping.get(mapping_id)
            rule_approval = ((assessment or {}).get("rule_trace") or {}).get("approval_status")
            candidate_grade = str((assessment or {}).get("candidate_grade") or "")
            grade_ceiling = str((assessment or {}).get("grade_ceiling") or "")
            candidate_rank = GRADE_ORDER.index(candidate_grade) if candidate_grade in GRADE_ORDER else -1
            ceiling_rank = GRADE_ORDER.index(grade_ceiling) if grade_ceiling in GRADE_ORDER else -1
            grade_rule_ok = bool(
                assessment
                and rule_approval == "approved"
                and candidate_rank >= 0
                and ceiling_rank >= 0
                and candidate_rank <= ceiling_rank
            )
            context_resolution = resolve_evidence_context(identity, supporting, facts)
            periods = set(context_resolution["period"]["values"])
            organizations = set(context_resolution["organization"]["values"])
            period_ok = len(periods) == 1
            organization_ok = len(organizations) == 1
            coverage_ok = coverage_ledger.get("coverage_status") == "complete"
            findings = []
            if not mapping_status_ok:
                findings.append(
                    "Mapping berstatus needs_review; advisory/model tidak boleh mempromosikannya."
                )
            if not coverage_ok:
                findings.append("Coverage dokumen belum lengkap.")
            if not source_coverage_ok:
                findings.append("Sebagian klaim belum memiliki lokasi dan kutipan yang cocok dengan unit sumber.")
            if not grade_rule_ok:
                if candidate_rank > ceiling_rank >= 0:
                    findings.append("Candidate grade melampaui grade ceiling rule.")
                else:
                    findings.append("Rule parameter belum disahkan atau grade ceiling tidak valid.")
            if not period_ok:
                findings.append("Periode fakta pendukung tidak tunggal atau belum diketahui.")
            if not organization_ok:
                findings.append("Organisasi fakta pendukung tidak tunggal atau belum diketahui.")
            verified = (
                mapping_status_ok
                and coverage_ok
                and source_coverage_ok
                and grade_rule_ok
                and period_ok
                and organization_ok
            )
            results.append(
                {
                    "mapping_candidate_id": mapping_id,
                    "verifier_type": "deterministic_v1",
                    "status": "verified" if verified else "needs_human_review",
                    "findings": findings,
                    "mapping_status_ok": mapping_status_ok,
                    "source_coverage_ok": source_coverage_ok,
                    "grade_rule_ok": grade_rule_ok,
                    "period_ok": period_ok,
                    "organization_ok": organization_ok,
                    "context_resolution": context_resolution,
                }
            )
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=(
                EngineStatus.COMPLETED
                if results and all(item["status"] == "verified" for item in results)
                else EngineStatus.PARTIAL if results else EngineStatus.SKIPPED
            ),
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{item.get('id')}" for item in mappings],
            output_refs=[f"verification:{item['mapping_candidate_id']}" for item in results],
            coverage={"required": len(mappings), "processed": len(results), "failed": 0},
            warnings=["Primary upload tetap diblokir hingga seluruh verification result berstatus verified."],
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "verification_count": len(results),
                "verified_count": sum(item["status"] == "verified" for item in results),
            },
            output={
                "verification_count": len(results),
                "verified_count": sum(item["status"] == "verified" for item in results),
            },
        ).finish()
        return results, result


class ModelSecondPassVerificationEngine:
    name = "model_second_pass_verification"
    version = PIPELINE_VERSION

    def __init__(self, provider: VerificationModelProvider):
        self.provider = provider

    def run(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        assessments: list[dict],
        facts: list[dict],
        deterministic_results: list[dict],
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        facts_by_id = {int(item["id"]): item for item in facts if item.get("id") is not None}
        assessment_by_mapping = {
            int(item["mapping_candidate_id"]): item
            for item in assessments if item.get("mapping_candidate_id") is not None
        }
        deterministic_by_mapping = {
            int(item["mapping_candidate_id"]): item
            for item in deterministic_results if item.get("mapping_candidate_id") is not None
        }
        payload = []
        for mapping in mappings:
            mapping_id = int(mapping["id"])
            supporting = [
                facts_by_id[item]
                for item in mapping.get("supporting_fact_ids") or []
                if item in facts_by_id
            ]
            payload.append(
                {
                    "mapping_candidate_id": mapping_id,
                    "parameter": {
                        "kk_id": mapping["kk_id"],
                        "kode": mapping["kode"],
                        "detail_kode": mapping["detail_kode"],
                    },
                    "assessment": assessment_by_mapping.get(mapping_id),
                    "deterministic_verification": deterministic_by_mapping.get(mapping_id),
                    "facts": [
                        {
                            "id": fact.get("id"),
                            "claim": fact.get("claim"),
                            "fact_type": fact.get("fact_type"),
                            "organization": fact.get("organization"),
                            "period": fact.get("period"),
                            "sources": [
                                {
                                    "unit_key": source.get("unit_key"),
                                    "source_location": source.get("source_location"),
                                    "source_quote": source.get("source_quote"),
                                }
                                for source in fact.get("sources") or []
                            ],
                        }
                        for fact in supporting
                    ],
                }
            )
        try:
            response = self.provider.verify_mappings(payload)
            usage_metrics = response.usage_metrics
            response_by_mapping = {item.mapping_candidate_id: item for item in response.items}
            warnings = list(response.warnings)
            provider_error = None
        except Exception as exc:
            usage_metrics = {}
            response_by_mapping = {}
            warnings = [f"Model verifier gagal: {exc}"]
            provider_error = str(exc)

        results = []
        for mapping in mappings:
            mapping_id = int(mapping["id"])
            deterministic = deterministic_by_mapping.get(mapping_id) or {}
            model_item = response_by_mapping.get(mapping_id)
            findings = list(model_item.findings if model_item else [])
            if deterministic.get("status") != "verified":
                findings.append("Deterministic verifier belum menyetujui kandidat; model tidak dapat override.")
            if not model_item:
                findings.append("Model verifier tidak mengembalikan hasil untuk kandidat ini.")
            verified = bool(
                deterministic.get("status") == "verified"
                and model_item
                and model_item.status == "verified"
            )
            results.append(
                {
                    "mapping_candidate_id": mapping_id,
                    "verifier_type": "model_second_pass_v1",
                    "status": "verified" if verified else "needs_human_review",
                    "findings": findings,
                    "source_coverage_ok": bool(deterministic.get("source_coverage_ok")),
                    "grade_rule_ok": bool(deterministic.get("grade_rule_ok")),
                    "period_ok": bool(deterministic.get("period_ok")),
                    "organization_ok": bool(deterministic.get("organization_ok")),
                }
            )
        verified_count = sum(item["status"] == "verified" for item in results)
        engine_status = (
            EngineStatus.COMPLETED
            if results and verified_count == len(results)
            else EngineStatus.FAILED if provider_error else EngineStatus.PARTIAL if results else EngineStatus.SKIPPED
        )
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=engine_status,
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{item.get('id')}" for item in mappings],
            output_refs=[f"model-verification:{item['mapping_candidate_id']}" for item in results],
            coverage={"required": len(mappings), "processed": len(response_by_mapping), "failed": int(bool(provider_error))},
            warnings=warnings[:20],
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "verification_count": len(results),
                "verified_count": verified_count,
                **usage_metrics,
            },
            output={"verification_count": len(results), "verified_count": verified_count},
            error_message=provider_error,
        ).finish()
        return results, result
