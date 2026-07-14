from __future__ import annotations

from collections import defaultdict
from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.domain.grading import FALLBACK_REQUIREMENTS, GRADE_ORDER
from app.analysis.repository import AnalysisRepository
from app.analysis.contracts import utc_now_iso


class CrossDocumentSynthesisEngine:
    name = "cross_document_synthesis"
    version = PIPELINE_VERSION

    def __init__(self, repository: AnalysisRepository):
        self.repository = repository

    def run(self, package_id: int) -> dict:
        started = perf_counter()
        package = self.repository.get_package(package_id)
        if not package:
            raise KeyError(package_id)
        self.repository.update_package(package_id, status="synthesizing")
        member_runs = package.get("members") or []
        invalid_members = [
            run
            for run in member_runs
            if run.get("analysis_mode") != "full_audit"
            or run.get("status") not in {"approved", "uploaded"}
            or run.get("coverage_status") != "complete"
        ]
        if invalid_members:
            reasons = [
                f"Run #{run['id']} belum merupakan full audit terminal yang eligible."
                for run in invalid_members
            ]
            self.repository.update_package(
                package_id,
                status="blocked",
                primary_blocked=True,
                block_reasons_json=reasons,
                finished_at=utc_now_iso(),
            )
            self.repository.save_package_engine_result(
                package_id,
                engine_name=self.name,
                engine_version=self.version,
                status="blocked",
                output={"assessment_count": 0},
                warnings=reasons,
                metrics={"duration_ms": _duration_ms(started)},
            )
            return self.repository.get_package(package_id) or {}

        grouped: dict[tuple[str, str, str, str, str], dict] = defaultdict(
            lambda: {
                "fact_ids": set(),
                "run_ids": set(),
                "stages": set(),
                "contradictions": set(),
            }
        )
        requested_org = str(package.get("organization") or "").strip()
        requested_period = str(package.get("period") or "").strip()
        for run in member_runs:
            run_id = int(run["id"])
            facts = self.repository.list_facts(run_id)
            facts_by_id = {int(item["id"]): item for item in facts}
            approved_mapping_ids = {
                int(item["mapping_candidate_id"])
                for item in self.repository.list_human_review_decisions(run_id)
                if item.get("decision") == "approve" and item.get("mapping_candidate_id") is not None
            }
            verification_by_mapping: dict[int, list[dict]] = defaultdict(list)
            for verification in self.repository.list_verification_results(run_id):
                if verification.get("mapping_candidate_id") is not None:
                    verification_by_mapping[int(verification["mapping_candidate_id"])].append(verification)
            for mapping in self.repository.list_mapping_candidates(run_id):
                mapping_id = int(mapping["id"])
                checks = verification_by_mapping.get(mapping_id) or []
                if mapping_id not in approved_mapping_ids or not checks or not all(
                    item.get("status") == "verified" for item in checks
                ):
                    continue
                supporting = [
                    facts_by_id[fact_id]
                    for fact_id in mapping.get("supporting_fact_ids") or []
                    if fact_id in facts_by_id
                ]
                organizations = {str(item["organization"]).strip() for item in supporting if item.get("organization")}
                periods = {str(item["period"]).strip() for item in supporting if item.get("period")}
                organization = requested_org or (next(iter(organizations)) if len(organizations) == 1 else "unknown")
                period = requested_period or (next(iter(periods)) if len(periods) == 1 else "unknown")
                key = (mapping["kk_id"], mapping["kode"], mapping["detail_kode"], organization, period)
                group = grouped[key]
                group["run_ids"].add(run_id)
                for fact in supporting:
                    group["fact_ids"].add(int(fact["id"]))
                    if fact.get("fact_type") in {"policy", "socialization", "implementation", "evaluation", "improvement"}:
                        group["stages"].add(str(fact["fact_type"]))
                if len(organizations) > 1:
                    group["contradictions"].add("Satu mapping mencampurkan lebih dari satu organisasi.")
                if len(periods) > 1:
                    group["contradictions"].add("Satu mapping mencampurkan lebih dari satu periode.")
                if requested_org and organizations and any(item != requested_org for item in organizations):
                    group["contradictions"].add("Organisasi fakta tidak sesuai scope paket.")
                if requested_period and periods and any(item != requested_period for item in periods):
                    group["contradictions"].add("Periode fakta tidak sesuai scope paket.")
                if organization == "unknown":
                    group["contradictions"].add("Organisasi evidence belum dapat dipastikan.")
                if period == "unknown":
                    group["contradictions"].add("Periode evidence belum dapat dipastikan.")

        if not grouped:
            reasons = [
                "Tidak ada mapping member yang sekaligus human-approved dan fully verified."
            ]
            self.repository.update_package(
                package_id,
                status="blocked",
                primary_blocked=True,
                block_reasons_json=reasons,
                finished_at=utc_now_iso(),
            )
            self.repository.save_package_engine_result(
                package_id,
                engine_name=self.name,
                engine_version=self.version,
                status="blocked",
                output={"assessment_count": 0},
                warnings=reasons,
                metrics={"duration_ms": _duration_ms(started), "member_count": len(member_runs)},
            )
            return self.repository.get_package(package_id) or {}

        assessments = []
        for (kk_id, kode, detail_kode, organization, period), group in grouped.items():
            stages = set(group["stages"])
            safe_grade = _safe_grade(stages)
            missing = _next_missing(safe_grade, stages)
            contradictions = sorted(group["contradictions"])
            assessments.append(
                {
                    "kk_id": kk_id,
                    "kode": kode,
                    "detail_kode": detail_kode,
                    "organization": organization,
                    "period": period,
                    "chain": {stage: stage in stages for stage in ("policy", "socialization", "implementation", "evaluation", "improvement")},
                    "supporting_run_ids": sorted(group["run_ids"]),
                    "supporting_fact_ids": sorted(group["fact_ids"]),
                    "safe_grade": None if contradictions else safe_grade,
                    "contradictions": contradictions,
                    "missing_requirements": missing,
                    "status": "needs_human_review" if not contradictions else "contradicted",
                }
            )
        assessments.sort(key=lambda item: (item["kk_id"], item["detail_kode"], item["organization"], item["period"]))
        self.repository.save_package_assessments(package_id, assessments)
        warnings = [
            "Package grade memakai maturity fallback draft dan tidak dapat mengaktifkan primary upload."
        ]
        contradiction_count = sum(bool(item["contradictions"]) for item in assessments)
        if contradiction_count:
            warnings.append(f"{contradiction_count} assessment paket memiliki kontradiksi scope/sumber.")
        self.repository.save_package_engine_result(
            package_id,
            engine_name=self.name,
            engine_version=self.version,
            status="partial",
            output={"assessment_count": len(assessments), "contradiction_count": contradiction_count},
            warnings=warnings,
            metrics={"duration_ms": _duration_ms(started), "member_count": len(member_runs)},
        )
        block_reasons = [
            "Package rule masih draft dan memerlukan domain review.",
            "Setiap assessment paket memerlukan human review sebelum digunakan untuk keputusan.",
        ]
        if contradiction_count:
            block_reasons.append("Paket memiliki kontradiksi organisasi/periode yang harus diselesaikan.")
        self.repository.update_package(
            package_id,
            status="review_required",
            primary_blocked=True,
            block_reasons_json=block_reasons,
            finished_at=utc_now_iso(),
        )
        return self.repository.get_package(package_id) or {}


def _safe_grade(stages: set[str]) -> str | None:
    safe = None
    for grade in GRADE_ORDER:
        if FALLBACK_REQUIREMENTS[grade] <= stages:
            safe = grade
    return safe


def _next_missing(safe_grade: str | None, stages: set[str]) -> list[str]:
    start = GRADE_ORDER.index(safe_grade) + 1 if safe_grade in GRADE_ORDER else 0
    for grade in GRADE_ORDER[start:]:
        missing = sorted(FALLBACK_REQUIREMENTS[grade] - stages)
        if missing:
            return missing
    return []


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
