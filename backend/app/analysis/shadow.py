from __future__ import annotations

import hashlib
import json
from typing import Any

from app.analysis.legacy_bridge import legacy_review_candidates
from app.analysis.repository import AnalysisRepository
from app.database import Database


SHADOW_COMPARISON_SCHEMA = "shadow-comparison-v1"


def candidate_key(item: dict[str, Any]) -> str:
    kk_id = str(item.get("kk_id") or item.get("kk") or "").strip()
    detail_code = str(
        item.get("detail_kode")
        or item.get("parameter_code")
        or item.get("kode")
        or ""
    ).strip()
    return f"{kk_id}:{detail_code}".strip(":")


def build_shadow_comparison(
    *,
    legacy_review_id: int,
    v2_run_id: int,
    legacy_candidates: list[dict[str, Any]],
    v2_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    legacy_order = [candidate_key(item) for item in legacy_candidates if candidate_key(item)]
    v2_order = [candidate_key(item) for item in v2_candidates if candidate_key(item)]
    legacy_keys = set(legacy_order)
    v2_keys = set(v2_order)
    intersection = legacy_keys & v2_keys
    union = legacy_keys | v2_keys
    comparison = {
        "schema": SHADOW_COMPARISON_SCHEMA,
        "legacy_review_id": int(legacy_review_id),
        "v2_run_id": int(v2_run_id),
        "legacy_candidate_keys": sorted(legacy_keys),
        "v2_candidate_keys": sorted(v2_keys),
        "intersection": sorted(intersection),
        "legacy_only": sorted(legacy_keys - v2_keys),
        "v2_only": sorted(v2_keys - legacy_keys),
        "metrics": {
            "legacy_candidate_count": len(legacy_keys),
            "v2_candidate_count": len(v2_keys),
            "intersection_count": len(intersection),
            "any_overlap": bool(intersection),
            "exact_set_match": legacy_keys == v2_keys,
            "top_1_match": (
                bool(legacy_order and v2_order and legacy_order[0] == v2_order[0])
                if legacy_order and v2_order
                else None
            ),
            "legacy_coverage_by_v2": (
                round(len(intersection) / len(legacy_keys), 6) if legacy_keys else None
            ),
            "jaccard": round(len(intersection) / len(union), 6) if union else None,
        },
        "decision_authority": "legacy",
        "quality_authority": "none_without_expert_gold",
        "contains_document_content": False,
    }
    canonical = json.dumps(comparison, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return comparison, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_shadow_report(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in pairs if item.get("status") == "completed"]
    metrics = [item.get("comparison", {}).get("metrics", {}) for item in completed]

    def ratio(field: str) -> float | None:
        values = [value[field] for value in metrics if value.get(field) is not None]
        return round(sum(float(value) for value in values) / len(values), 6) if values else None

    summary = {
        "schema": "shadow-report-v1",
        "pair_count": len(pairs),
        "completed_count": len(completed),
        "queued_count": sum(item.get("status") in {"queued", "running"} for item in pairs),
        "failed_count": sum(item.get("status") in {"failed", "cancelled"} for item in pairs),
        "exact_set_match_rate": ratio("exact_set_match"),
        "top_1_match_rate": ratio("top_1_match"),
        "any_overlap_rate": ratio("any_overlap"),
        "average_legacy_coverage_by_v2": ratio("legacy_coverage_by_v2"),
        "average_jaccard": ratio("jaccard"),
        "minimum_review_target": 50,
        "review_target_reached": len(completed) >= 50,
        "quality_authority": "none_without_expert_gold",
        "contains_document_content": False,
        "pair_report_sha256": sorted(
            str(item.get("report_sha256"))
            for item in completed
            if item.get("report_sha256")
        ),
    }
    canonical = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {**summary, "report_sha256": report_sha256}


class ShadowComparisonService:
    def __init__(self, db: Database, repository: AnalysisRepository | None = None):
        self.db = db
        self.repository = repository or AnalysisRepository(db)

    def track(self, legacy_review_id: int, v2_job_id: str) -> dict[str, Any]:
        pair = self.repository.record_shadow_pair(legacy_review_id, v2_job_id)
        return self.refresh_pair(int(pair["id"]))

    def refresh_pair(self, pair_id: int) -> dict[str, Any]:
        pair = self.repository.get_shadow_pair(pair_id)
        if not pair:
            raise KeyError(pair_id)
        if pair.get("status") == "completed":
            return pair
        job = self.repository.get_job(str(pair["v2_job_id"]))
        if not job:
            self.repository.mark_shadow_job_status(str(pair["v2_job_id"]), "failed")
            return self.repository.get_shadow_pair(pair_id) or pair
        if job["status"] in {"failed", "cancelled"}:
            self.repository.mark_shadow_job_status(str(job["id"]), str(job["status"]))
            return self.repository.get_shadow_pair(pair_id) or pair
        if job["status"] in {"queued", "running", "cancel_requested"}:
            self.repository.mark_shadow_job_status(
                str(job["id"]),
                "running" if job["status"] != "queued" else "queued",
            )
            return self.repository.get_shadow_pair(pair_id) or pair
        run_id = job.get("run_id")
        if not run_id:
            self.repository.mark_shadow_job_status(str(job["id"]), "failed")
            return self.repository.get_shadow_pair(pair_id) or pair
        return self.finalize_pair(pair, int(run_id))

    def finalize_job(self, v2_job_id: str, v2_run_id: int) -> list[dict[str, Any]]:
        pairs = [
            pair for pair in self.repository.list_shadow_pairs(limit=5000)
            if pair.get("v2_job_id") == v2_job_id and pair.get("status") != "completed"
        ]
        return [self.finalize_pair(pair, v2_run_id) for pair in pairs]

    def finalize_pair(self, pair: dict[str, Any], v2_run_id: int) -> dict[str, Any]:
        legacy_candidates = legacy_review_candidates(self.db, int(pair["legacy_review_id"]))
        if legacy_candidates is None:
            self.repository.mark_shadow_job_status(str(pair["v2_job_id"]), "failed")
            return self.repository.get_shadow_pair(int(pair["id"])) or pair
        comparison, report_sha256 = build_shadow_comparison(
            legacy_review_id=int(pair["legacy_review_id"]),
            v2_run_id=v2_run_id,
            legacy_candidates=legacy_candidates,
            v2_candidates=self.repository.list_mapping_candidates(v2_run_id),
        )
        return self.repository.save_shadow_comparison(
            int(pair["id"]),
            v2_run_id=v2_run_id,
            comparison=comparison,
            report_sha256=report_sha256,
        )

    def refresh_all(self, limit: int = 500) -> list[dict[str, Any]]:
        return [
            self.refresh_pair(int(pair["id"]))
            for pair in self.repository.list_shadow_pairs(limit=limit)
        ]

    def report(self, *, refresh: bool = False, limit: int = 500) -> dict[str, Any]:
        pairs = self.refresh_all(limit=limit) if refresh else self.repository.list_shadow_pairs(limit)
        return build_shadow_report(pairs)
