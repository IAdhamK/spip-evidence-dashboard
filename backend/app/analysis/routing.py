from __future__ import annotations

from time import perf_counter

from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus


ROUTING_POLICY_VERSION = "compute-routing-v1"
FORMAT_COMPLEXITY = {
    "text": 0.10,
    "pdf": 0.35,
    "docx": 0.35,
    "xlsx": 0.60,
    "pptx": 0.70,
    "image": 0.80,
}


def mapping_key(item: dict) -> str:
    return ":".join(
        str(item.get(key) or "") for key in ("kk_id", "kode", "detail_kode")
    )


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _ratio(numerator: int, denominator: int) -> float:
    return _bounded(numerator / max(1, denominator))


def _routing_result(
    identity: DocumentIdentity,
    phase: str,
    decision: dict,
    *,
    input_refs: list[str],
    started: float,
) -> EngineResult:
    return EngineResult(
        engine_name=f"compute_routing_{phase}",
        engine_version=ROUTING_POLICY_VERSION,
        status=EngineStatus.COMPLETED,
        input_checksum=identity.sha256,
        input_refs=input_refs,
        output_refs=[f"route:{phase}:{decision['target']}"],
        coverage={"required": 1, "processed": 1, "failed": 0},
        metrics={
            "duration_ms": max(0, round((perf_counter() - started) * 1000)),
            "complexity_score": decision["complexity_score"],
            "risk_score": decision["risk_score"],
            "selected": int(bool(decision["selected"])),
        },
        output=decision,
    ).finish()


class _FactRoutingPolicy:
    version = ROUTING_POLICY_VERSION

    def route_fact_extraction(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        coverage: dict,
        ambiguous_units: list[dict],
        *,
        requested_mode: str,
        external_ai_allowed: bool,
        provider_available: bool,
        minimum_complexity: float,
        resumed: bool = False,
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        total = len(units)
        ambiguous_count = len(ambiguous_units)
        visual_pending = sum(
            (unit.get("metadata") or {}).get("visual_semantics_status")
            == "pending_review_or_vision"
            for unit in units
        )
        ocr_pending = sum(unit.get("status") == "ocr_required" for unit in units)
        unit_scale = _bounded(total / 40)
        ambiguity_ratio = _ratio(ambiguous_count, total)
        visual_ratio = _ratio(visual_pending, total)
        ocr_ratio = _ratio(ocr_pending, total)
        coverage_gap = _bounded(
            1 - float(coverage.get("coverage_percentage") or 0) / 100
        )
        heading_depth = max(
            (len(unit.get("heading_path") or []) for unit in units),
            default=0,
        )
        heading_factor = _bounded(heading_depth / 6)
        long_unit_ratio = _ratio(
            sum(int(unit.get("char_count") or len(str(unit.get("text") or ""))) > 6000 for unit in units),
            total,
        )
        format_factor = FORMAT_COMPLEXITY.get(identity.file_kind, 0.5)
        complexity = _bounded(
            (format_factor * 0.25)
            + (unit_scale * 0.15)
            + (visual_ratio * 0.20)
            + (ambiguity_ratio * 0.25)
            + (heading_factor * 0.05)
            + (long_unit_ratio * 0.10)
        )
        risk = _bounded(
            (coverage_gap * 0.35)
            + (ocr_ratio * 0.25)
            + (visual_ratio * 0.20)
            + (ambiguity_ratio * 0.20)
        )
        threshold = _bounded(minimum_complexity)
        required = bool(ambiguous_count)
        complexity_eligible = bool(
            complexity >= threshold or ambiguity_ratio >= 0.25
        )
        selected = bool(
            required
            and complexity_eligible
            and not resumed
            and external_ai_allowed
            and provider_available
            and coverage.get("coverage_status") != "failed"
        )
        reasons = []
        if resumed:
            reasons.append("checkpoint_artifact_reused")
        if not ambiguous_count:
            reasons.append("deterministic_facts_cover_eligible_units")
        if required:
            reasons.append("eligible_units_without_deterministic_fact")
        if required and not complexity_eligible:
            reasons.append("complexity_below_structured_threshold")
        if not external_ai_allowed:
            reasons.append("job_local_only")
        if not provider_available:
            reasons.append("structured_provider_unavailable")
        if coverage.get("coverage_status") == "failed":
            reasons.append("coverage_failed")
        decision = {
            "policy_version": self.version,
            "phase": "fact",
            "target": "structured_fact_extraction" if selected else "deterministic_fact_extraction",
            "selected": selected,
            "required": required,
            "requested_mode": requested_mode,
            "complexity_score": complexity,
            "risk_score": risk,
            "threshold": threshold,
            "reason_codes": sorted(set(reasons)),
            "factors": {
                "file_kind": identity.file_kind,
                "unit_count": total,
                "format_factor": format_factor,
                "ambiguous_unit_count": ambiguous_count,
                "ambiguity_ratio": ambiguity_ratio,
                "visual_pending_ratio": visual_ratio,
                "ocr_pending_ratio": ocr_ratio,
                "coverage_gap": coverage_gap,
                "heading_depth_factor": heading_factor,
                "long_unit_ratio": long_unit_ratio,
            },
            "authority": "compute_selection_only_no_fact_or_grade_authority",
        }
        return decision, _routing_result(
            identity,
            "fact",
            decision,
            input_refs=[f"unit:{unit.get('unit_key')}" for unit in units],
            started=started,
        )


class ComputeRoutingEngine(_FactRoutingPolicy):
    version = ROUTING_POLICY_VERSION

    def route_mapping_reasoning(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        facts: list[dict],
        *,
        base_complexity_score: float,
        requested_mode: str,
        external_ai_allowed: bool,
        provider_available: bool,
        ambiguity_margin: float,
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        scores = sorted(
            (float(item.get("mapping_score") or 0) for item in mappings),
            reverse=True,
        )
        observed_margin = _bounded(scores[0] - scores[1]) if len(scores) > 1 else 1.0
        margin_threshold = _bounded(ambiguity_margin)
        close_top_candidates = len(scores) > 1 and observed_margin <= margin_threshold
        low_score_ratio = _ratio(sum(score < 0.55 for score in scores), len(scores))
        needs_review_ratio = _ratio(
            sum(item.get("status") != "candidate" for item in mappings),
            len(mappings),
        )
        periods = {str(fact.get("period")) for fact in facts if fact.get("period")}
        organizations = {
            str(fact.get("organization")) for fact in facts if fact.get("organization")
        }
        period_ambiguity = float(len(periods) != 1)
        organization_ambiguity = float(len(organizations) != 1)
        risk = _bounded(
            (float(close_top_candidates) * 0.30)
            + (low_score_ratio * 0.25)
            + (needs_review_ratio * 0.25)
            + (period_ambiguity * 0.10)
            + (organization_ambiguity * 0.10)
        )
        complexity = _bounded(
            (base_complexity_score * 0.6)
            + (_bounded(len(mappings) / 10) * 0.2)
            + (float(close_top_candidates) * 0.2)
        )
        required = bool(
            mappings
            and (
                close_top_candidates
                or needs_review_ratio > 0
                or low_score_ratio >= 0.5
            )
        )
        selected = bool(
            required and external_ai_allowed and provider_available
        )
        reasons = []
        if not mappings:
            reasons.append("no_mapping_candidates")
        if close_top_candidates:
            reasons.append("top_candidate_margin_ambiguous")
        if low_score_ratio >= 0.5:
            reasons.append("majority_mapping_score_low")
        if needs_review_ratio > 0:
            reasons.append("mapping_without_source_support")
        if not external_ai_allowed:
            reasons.append("job_local_only")
        if not provider_available:
            reasons.append("mapping_reasoning_provider_unavailable")
        decision = {
            "policy_version": self.version,
            "phase": "mapping",
            "target": "constrained_mapping_reasoning" if selected else "deterministic_mapping",
            "selected": selected,
            "required": required,
            "requested_mode": requested_mode,
            "complexity_score": complexity,
            "risk_score": risk,
            "threshold": margin_threshold,
            "reason_codes": sorted(set(reasons)),
            "candidate_keys": [mapping_key(item) for item in mappings[:8]],
            "factors": {
                "candidate_count": len(mappings),
                "top_candidate_margin": observed_margin,
                "low_score_ratio": low_score_ratio,
                "needs_review_ratio": needs_review_ratio,
                "period_ambiguity": period_ambiguity,
                "organization_ambiguity": organization_ambiguity,
            },
            "authority": "demotion_only_no_mapping_promotion_or_grade_authority",
        }
        return decision, _routing_result(
            identity,
            "mapping",
            decision,
            input_refs=[
                *[f"fact:{fact.get('fact_key')}" for fact in facts],
                *[f"mapping:{mapping_key(item)}" for item in mappings],
            ],
            started=started,
        )


    def route_model_verification(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        assessments: list[dict],
        deterministic_results: list[dict],
        coverage: dict,
        *,
        base_complexity_score: float,
        requested_mode: str,
        external_ai_allowed: bool,
        provider_available: bool,
        minimum_risk: float,
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        verified_ids = {
            int(item["mapping_candidate_id"])
            for item in deterministic_results
            if item.get("mapping_candidate_id") is not None
            and item.get("status") == "verified"
        }
        mapping_by_id = {
            int(item["id"]): item for item in mappings if item.get("id") is not None
        }
        assessment_by_id = {
            int(item["mapping_candidate_id"]): item
            for item in assessments
            if item.get("mapping_candidate_id") is not None
        }
        verified_mappings = [mapping_by_id[item] for item in verified_ids if item in mapping_by_id]
        low_score_ratio = _ratio(
            sum(float(item.get("mapping_score") or 0) < 0.65 for item in verified_mappings),
            len(verified_mappings),
        )
        high_grade_ratio = _ratio(
            sum(
                str((assessment_by_id.get(mapping_id) or {}).get("candidate_grade") or "")
                in {"A", "B"}
                for mapping_id in verified_ids
            ),
            len(verified_ids),
        )
        human_ratio = _ratio(
            sum(item.get("status") != "verified" for item in deterministic_results),
            len(deterministic_results),
        )
        coverage_gap = _bounded(
            1 - float(coverage.get("coverage_percentage") or 0) / 100
        )
        risk = _bounded(
            (low_score_ratio * 0.35)
            + (high_grade_ratio * 0.35)
            + (coverage_gap * 0.20)
            + (human_ratio * 0.10)
        )
        complexity = _bounded(
            (base_complexity_score * 0.7)
            + (_bounded(len(verified_ids) / 20) * 0.3)
        )
        threshold = _bounded(minimum_risk)
        required = bool(verified_ids and risk >= threshold)
        selected = bool(
            required and external_ai_allowed and provider_available
        )
        reasons = []
        if not verified_ids:
            reasons.append("no_deterministically_verified_mapping")
        if low_score_ratio > 0:
            reasons.append("verified_mapping_score_below_high_confidence")
        if high_grade_ratio > 0:
            reasons.append("high_grade_requires_second_pass")
        if risk < threshold:
            reasons.append("risk_below_model_verifier_threshold")
        if not external_ai_allowed:
            reasons.append("job_local_only")
        if not provider_available:
            reasons.append("model_verifier_provider_unavailable")
        decision = {
            "policy_version": self.version,
            "phase": "verification",
            "target": "model_second_pass_verification" if selected else "deterministic_verification",
            "selected": selected,
            "required": required,
            "requested_mode": requested_mode,
            "complexity_score": complexity,
            "risk_score": risk,
            "threshold": threshold,
            "reason_codes": sorted(set(reasons)),
            "mapping_candidate_ids": sorted(verified_ids),
            "factors": {
                "deterministically_verified_count": len(verified_ids),
                "deterministic_human_review_ratio": human_ratio,
                "low_score_ratio": low_score_ratio,
                "high_grade_ratio": high_grade_ratio,
                "coverage_gap": coverage_gap,
            },
            "authority": "second_pass_veto_only_no_deterministic_override",
        }
        return decision, _routing_result(
            identity,
            "verification",
            decision,
            input_refs=[
                *[f"mapping:{item.get('id')}" for item in mappings],
                *[f"verification:{item.get('mapping_candidate_id')}" for item in deterministic_results],
            ],
            started=started,
        )
