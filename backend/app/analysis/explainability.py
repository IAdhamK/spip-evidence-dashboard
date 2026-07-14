from __future__ import annotations

from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus


class OutputExplainabilityEngine:
    name = "output_explainability"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        coverage: dict,
        facts: list[dict],
        mappings: list[dict],
        assessments: list[dict],
        verifications: list[dict],
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        assessment_by_mapping = {
            int(item["mapping_candidate_id"]): item
            for item in assessments if item.get("mapping_candidate_id") is not None
        }
        verification_by_mapping: dict[int, list[dict]] = {}
        for item in verifications:
            if item.get("mapping_candidate_id") is not None:
                verification_by_mapping.setdefault(int(item["mapping_candidate_id"]), []).append(item)
        fact_by_id = {int(item["id"]): item for item in facts if item.get("id") is not None}
        recommendations = []
        for mapping in mappings:
            mapping_id = int(mapping["id"])
            assessment = assessment_by_mapping.get(mapping_id)
            checks = verification_by_mapping.get(mapping_id) or []
            cited_facts = [
                fact_by_id[fact_id]
                for fact_id in mapping.get("supporting_fact_ids") or []
                if fact_id in fact_by_id
            ]
            recommendations.append(
                {
                    "mapping_candidate_id": mapping_id,
                    "parameter": {
                        "kk_id": mapping["kk_id"],
                        "kode": mapping["kode"],
                        "detail_kode": mapping["detail_kode"],
                    },
                    "match_score": mapping.get("mapping_score"),
                    "candidate_grade": (assessment or {}).get("candidate_grade"),
                    "grade_ceiling": (assessment or {}).get("grade_ceiling"),
                    "rule_approval_status": ((assessment or {}).get("rule_trace") or {}).get("approval_status"),
                    "verification_status": (
                        "verified" if checks and all(item.get("status") == "verified" for item in checks)
                        else "needs_human_review"
                    ),
                    "reasons": mapping.get("reasons") or [],
                    "missing_requirements": (assessment or {}).get("missing_requirements") or [],
                    "citations": [
                        {
                            "fact_id": fact.get("id"),
                            "claim": fact.get("claim"),
                            "sources": [
                                {
                                    "unit_key": source.get("unit_key"),
                                    "source_location": source.get("source_location"),
                                    "source_quote": source.get("source_quote"),
                                }
                                for source in (fact.get("sources") or [])[:5]
                            ],
                        }
                        for fact in cited_facts[:12]
                    ],
                    "warnings": [
                        finding
                        for check in checks
                        for finding in (check.get("findings") or [])
                    ][:20],
                }
            )
        output = {
            "coverage": coverage,
            "recommendation_count": len(recommendations),
            "recommendations": recommendations,
            "labels": {
                "match_score": "skor kecocokan",
                "candidate_grade": "grade kandidat, bukan keputusan final",
            },
            "decision_note": "Output ini hanya menjelaskan hasil engine dan tidak mengubah gate keputusan.",
        }
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED,
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{item.get('id')}" for item in mappings],
            output_refs=["explainability:root"],
            coverage={"required": len(mappings), "processed": len(recommendations), "failed": 0},
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "recommendation_count": len(recommendations),
                "citation_count": sum(len(item["citations"]) for item in recommendations),
            },
            output={"recommendation_count": len(recommendations)},
        ).finish()
        return output, result
