from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.analysis import PIPELINE_VERSION


GRADE_RANK = {"E": 1, "D": 2, "C": 3, "B": 4, "A": 5}
SERVER_DERIVED_GENERATION_METHOD = "server_derived_v2_partitioned"
EVIDENCE_ROLE_PRIORITY = ("contradictory", "primary", "supporting", "context")


def _mapping_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("kk_id") or ""),
        str(item.get("kode") or ""),
        str(item.get("detail_kode") or ""),
    )


def _aggregate_evidence_role(facts: list[dict[str, Any]]) -> str | None:
    roles = {str(item.get("evidence_role") or "") for item in facts}
    return next((role for role in EVIDENCE_ROLE_PRIORITY if role in roles), None)


def _run_latency_seconds(run: dict[str, Any]) -> float | None:
    started_at = str(run.get("started_at") or "").strip()
    finished_at = str(run.get("finished_at") or "").strip()
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        return max(0.0, (finished - started).total_seconds())
    except (ValueError, TypeError):
        return None


def _predicted_template_status(engine_results: list[dict[str, Any]]) -> str | None:
    result = next(
        (
            item for item in reversed(engine_results)
            if item.get("engine_name") == "template_completeness"
        ),
        None,
    )
    ledger = ((result or {}).get("output") or {}).get("template_ledger") or {}
    checked_units = int(ledger.get("checked_units") or 0)
    if checked_units <= 0:
        return None
    return "template_only" if int(ledger.get("substantive_units") or 0) == 0 else "substantive"


def build_expert_gold_evaluation(repository: Any) -> dict[str, Any]:
    """Evaluate active expert-gold labels against persisted V2 predictions.

    The report is deterministic and content-free: it stores IDs, mapping keys,
    counters, and checksums, never document text or source quotations.
    """
    dataset_summary = repository.expert_dataset_summary()
    if int(dataset_summary.get("partition_overlap_count") or 0):
        raise ValueError(
            "Dataset expert gold memiliki dokumen yang sama pada partisi Evaluasi dan Learning."
        )
    gold_items = [
        item for item in repository.list_expert_dataset_items()
        if item.get("dataset_status") == "expert_gold"
        and (item.get("dataset_partition") or "evaluation") == "evaluation"
    ]
    details: list[dict[str, Any]] = []
    positive_count = 0
    retrieval_hits = 0
    expected_source_count = 0
    matched_source_count = 0
    graded_positive_count = 0
    assessed_grade_count = 0
    overgrade_count = 0
    negative_count = 0
    negative_with_candidates = 0
    top_five_candidate_count = 0
    relevant_top_five_count = 0
    expected_mapping_count = 0
    expected_evidence_role_count = 0
    matched_evidence_role_count = 0
    measured_latency_count = 0
    total_latency_seconds = 0.0
    measured_cost_count = 0
    total_estimated_cost_usd = 0.0
    expected_template_count = 0
    matched_template_count = 0
    expected_template_only_count = 0
    matched_template_only_count = 0

    for label in gold_items:
        run_id = int(label["run_id"])
        candidates = repository.list_mapping_candidates(run_id)
        expected = label.get("expected_mappings") or []
        expected_keys = {_mapping_key(item) for item in expected if any(_mapping_key(item))}
        top_five_keys = {_mapping_key(item) for item in candidates[:5]}
        top_five_candidate_count += len(candidates[:5])
        relevant_top_five_count += len(expected_keys & top_five_keys)
        is_positive = bool(expected_keys)
        retrieval_hit = bool(expected_keys & top_five_keys) if is_positive else not candidates
        if is_positive:
            positive_count += 1
            retrieval_hits += int(retrieval_hit)
        else:
            negative_count += 1
            negative_with_candidates += int(bool(candidates))

        expected_fact_ids = {int(item) for item in (label.get("selected_fact_ids") or [])}
        matching_candidates = [
            item for item in candidates if _mapping_key(item) in expected_keys
        ]
        facts = repository.list_facts(run_id)
        facts_by_id = {int(item["id"]): item for item in facts}
        predicted_fact_ids = {
            int(fact_id)
            for candidate in matching_candidates
            for fact_id in (candidate.get("supporting_fact_ids") or [])
        }
        expected_source_count += len(expected_fact_ids)
        source_matches = len(expected_fact_ids & predicted_fact_ids)
        matched_source_count += source_matches

        evidence_role_results = []
        for expected_mapping in expected:
            mapping_key = _mapping_key(expected_mapping)
            if not any(mapping_key):
                continue
            expected_mapping_count += 1
            expected_role = str(expected_mapping.get("evidence_role") or "")
            matching_candidate = next(
                (item for item in candidates if _mapping_key(item) == mapping_key),
                None,
            )
            predicted_role = None
            if matching_candidate:
                predicted_role = _aggregate_evidence_role([
                    facts_by_id[int(fact_id)]
                    for fact_id in (matching_candidate.get("supporting_fact_ids") or [])
                    if int(fact_id) in facts_by_id
                ])
            role_matched = False
            if expected_role in EVIDENCE_ROLE_PRIORITY:
                expected_evidence_role_count += 1
                role_matched = predicted_role == expected_role
                matched_evidence_role_count += int(role_matched)
            evidence_role_results.append({
                "mapping_key": "|".join(mapping_key),
                "expected_role": expected_role or None,
                "predicted_role": predicted_role,
                "matched": role_matched,
            })

        run = repository.get_run(run_id) or {}
        predicted_template_status = _predicted_template_status(
            repository.list_engine_results(run_id)
        )
        expected_template_status = str(
            label.get("expected_template_status") or "not_assessed"
        )
        template_matched = False
        if expected_template_status in {"template_only", "substantive"}:
            expected_template_count += 1
            template_matched = predicted_template_status == expected_template_status
            matched_template_count += int(template_matched)
            if expected_template_status == "template_only":
                expected_template_only_count += 1
                matched_template_only_count += int(template_matched)
        latency_seconds = _run_latency_seconds(run)
        if latency_seconds is not None:
            measured_latency_count += 1
            total_latency_seconds += latency_seconds
        estimated_cost_usd = run.get("estimated_cost_usd")
        if estimated_cost_usd is not None:
            measured_cost_count += 1
            total_estimated_cost_usd += max(0.0, float(estimated_cost_usd or 0))

        assessments = {
            int(item["mapping_candidate_id"]): item
            for item in repository.list_grade_assessments(run_id)
        }
        expected_grades = [
            str(item.get("grade") or "").upper()
            for item in expected
            if str(item.get("grade") or "").upper() in GRADE_RANK
        ]
        expected_grade = expected_grades[0] if expected_grades else None
        predicted_grade = None
        if matching_candidates:
            assessment = assessments.get(int(matching_candidates[0]["id"])) or {}
            candidate_grade = str(assessment.get("candidate_grade") or "").upper()
            predicted_grade = candidate_grade if candidate_grade in GRADE_RANK else None
        overgraded = False
        if expected_grade:
            graded_positive_count += 1
            if predicted_grade:
                assessed_grade_count += 1
                overgraded = GRADE_RANK[predicted_grade] > GRADE_RANK[expected_grade]
                overgrade_count += int(overgraded)

        details.append({
            "run_id": run_id,
            "label_id": int(label["id"]),
            "document_sha256": label["sha256"],
            "outcome": label["outcome"],
            "expected_mapping_keys": ["|".join(item) for item in sorted(expected_keys)],
            "top_five_mapping_keys": [
                "|".join(_mapping_key(item)) for item in candidates[:5]
            ],
            "retrieval_hit": retrieval_hit,
            "expected_source_count": len(expected_fact_ids),
            "matched_source_count": source_matches,
            "expected_grade": expected_grade,
            "predicted_grade": predicted_grade,
            "overgraded": overgraded,
            "evidence_role_results": evidence_role_results,
            "expected_template_status": expected_template_status,
            "predicted_template_status": predicted_template_status,
            "template_matched": template_matched,
            "latency_seconds": round(latency_seconds, 6) if latency_seconds is not None else None,
            "estimated_cost_usd": (
                round(max(0.0, float(estimated_cost_usd or 0)), 6)
                if estimated_cost_usd is not None else None
            ),
        })

    case_count = len(gold_items)
    metrics = {
        "retrieval_recall_at_5": round(retrieval_hits / positive_count, 6) if positive_count else 0.0,
        "source_accuracy": round(matched_source_count / expected_source_count, 6) if expected_source_count else 0.0,
        "overgrade_rate": round(overgrade_count / assessed_grade_count, 6) if assessed_grade_count else 1.0,
        "grade_label_coverage": round(graded_positive_count / positive_count, 6) if positive_count else 0.0,
        "grade_assessment_coverage": round(assessed_grade_count / graded_positive_count, 6) if graded_positive_count else 0.0,
        "negative_false_positive_rate": round(negative_with_candidates / negative_count, 6) if negative_count else 0.0,
        "mapping_precision_at_5": round(
            relevant_top_five_count / top_five_candidate_count, 6
        ) if top_five_candidate_count else 0.0,
        "evidence_role_accuracy": round(
            matched_evidence_role_count / expected_evidence_role_count, 6
        ) if expected_evidence_role_count else 0.0,
        "evidence_role_label_coverage": round(
            expected_evidence_role_count / expected_mapping_count, 6
        ) if expected_mapping_count else 0.0,
        "abstention_accuracy": round(
            (negative_count - negative_with_candidates) / negative_count, 6
        ) if negative_count else 0.0,
        "average_run_latency_seconds": round(
            total_latency_seconds / measured_latency_count, 6
        ) if measured_latency_count else 0.0,
        "run_latency_coverage": round(
            measured_latency_count / case_count, 6
        ) if case_count else 0.0,
        "average_estimated_cost_usd": round(
            total_estimated_cost_usd / measured_cost_count, 6
        ) if measured_cost_count else 0.0,
        "run_cost_coverage": round(
            measured_cost_count / case_count, 6
        ) if case_count else 0.0,
        "template_detection_accuracy": round(
            matched_template_count / expected_template_count, 6
        ) if expected_template_count else 0.0,
        "template_detection_recall": round(
            matched_template_only_count / expected_template_only_count, 6
        ) if expected_template_only_count else 0.0,
        "template_label_coverage": round(
            expected_template_count / case_count, 6
        ) if case_count else 0.0,
    }
    counters = {
        "case_count": case_count,
        "positive_case_count": positive_count,
        "negative_case_count": negative_count,
        "retrieval_hit_count": retrieval_hits,
        "expected_source_count": expected_source_count,
        "matched_source_count": matched_source_count,
        "graded_positive_count": graded_positive_count,
        "assessed_grade_count": assessed_grade_count,
        "overgrade_count": overgrade_count,
        "negative_with_candidates": negative_with_candidates,
        "top_five_candidate_count": top_five_candidate_count,
        "relevant_top_five_count": relevant_top_five_count,
        "expected_mapping_count": expected_mapping_count,
        "expected_evidence_role_count": expected_evidence_role_count,
        "matched_evidence_role_count": matched_evidence_role_count,
        "measured_latency_count": measured_latency_count,
        "measured_cost_count": measured_cost_count,
        "expected_template_count": expected_template_count,
        "matched_template_count": matched_template_count,
        "expected_template_only_count": expected_template_only_count,
        "matched_template_only_count": matched_template_only_count,
    }
    report_payload = {
        "pipeline_version": PIPELINE_VERSION,
        "dataset_sha256": dataset_summary.get("dataset_sha256"),
        "metrics": metrics,
        "counters": counters,
        "cases": sorted(details, key=lambda item: (item["document_sha256"], item["label_id"])),
    }
    encoded = json.dumps(
        report_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "dataset_summary": dataset_summary,
        "metrics": metrics,
        "counters": counters,
        "details": {"cases": report_payload["cases"]},
        "report_sha256": hashlib.sha256(encoded).hexdigest(),
    }
