from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.provider import MappingReasoningItem, MappingReasoningResponse
from app.analysis.mapping_reasoning import ConstrainedMappingReasoningEngine
from app.analysis.routing import ComputeRoutingEngine


class ComputeRoutingEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = DocumentIdentity(
            "evidence.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            1000,
            "sha-routing",
            "xlsx",
        )
        self.units = [
            {
                "id": 1,
                "unit_key": "sheet-1",
                "status": "processed",
                "text": "Evaluasi pengendalian telah dilaksanakan tahun 2026.",
                "char_count": 55,
                "heading_path": ["Evaluasi"],
                "metadata": {},
            },
            {
                "id": 2,
                "unit_key": "sheet-2",
                "status": "processed",
                "text": "Relasi antar indikator perlu dianalisis lebih lanjut.",
                "char_count": 51,
                "heading_path": ["Evaluasi", "Indikator"],
                "metadata": {},
            },
        ]
        self.coverage = {
            "coverage_status": "complete",
            "coverage_percentage": 100,
        }
        self.facts = [
            {
                "id": 1,
                "fact_key": "fact-1",
                "claim": "Evaluasi pengendalian telah dilaksanakan tahun 2026.",
                "fact_type": "evaluation",
                "period": "2026",
                "organization": "Ditjen PDP",
                "sources": [{"source_quote": "Evaluasi pengendalian telah dilaksanakan"}],
            }
        ]
        self.engine = ComputeRoutingEngine()

    def test_fact_route_depends_on_ambiguity_and_capability_not_user_mode(self) -> None:
        screening, screening_result = self.engine.route_fact_extraction(
            self.identity,
            self.units,
            self.coverage,
            [self.units[1]],
            requested_mode="screening",
            external_ai_allowed=True,
            provider_available=True,
            minimum_complexity=0.25,
        )
        full, _ = self.engine.route_fact_extraction(
            self.identity,
            self.units,
            self.coverage,
            [self.units[1]],
            requested_mode="full_audit",
            external_ai_allowed=True,
            provider_available=True,
            minimum_complexity=0.25,
        )
        self.assertTrue(screening["selected"])
        self.assertEqual(screening["selected"], full["selected"])
        self.assertGreater(screening["complexity_score"], 0)
        self.assertEqual(screening_result.engine_name, "compute_routing_fact")
        self.assertEqual(
            screening["authority"],
            "compute_selection_only_no_fact_or_grade_authority",
        )

        local_only, _ = self.engine.route_fact_extraction(
            self.identity,
            self.units,
            self.coverage,
            [self.units[1]],
            requested_mode="full_audit",
            external_ai_allowed=False,
            provider_available=True,
            minimum_complexity=0.25,
        )
        self.assertFalse(local_only["selected"])
        self.assertIn("job_local_only", local_only["reason_codes"])

    def test_compute_routing_policy_has_no_provider_database_or_repository_dependency(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "app" / "analysis" / "routing.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            str(node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue({
            "app.analysis.provider",
            "app.analysis.repository",
            "app.database",
        }.isdisjoint(imported))

    def test_mapping_route_detects_close_candidates_and_remains_demotion_only(self) -> None:
        mappings = [
            {
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.1",
                "mapping_score": 0.81,
                "status": "candidate",
            },
            {
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.2",
                "mapping_score": 0.78,
                "status": "candidate",
            },
        ]
        decision, result = self.engine.route_mapping_reasoning(
            self.identity,
            mappings,
            self.facts,
            base_complexity_score=0.5,
            requested_mode="screening",
            external_ai_allowed=True,
            provider_available=True,
            ambiguity_margin=0.08,
        )
        self.assertTrue(decision["selected"])
        self.assertIn("top_candidate_margin_ambiguous", decision["reason_codes"])
        self.assertEqual(result.engine_name, "compute_routing_mapping")
        self.assertIn("demotion_only", decision["authority"])

    def test_model_verifier_routes_only_deterministically_verified_high_risk(self) -> None:
        mappings = [
            {
                "id": 7,
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.1",
                "mapping_score": 0.5,
            }
        ]
        assessments = [{"mapping_candidate_id": 7, "candidate_grade": "A"}]
        deterministic = [{"mapping_candidate_id": 7, "status": "verified"}]
        decision, result = self.engine.route_model_verification(
            self.identity,
            mappings,
            assessments,
            deterministic,
            self.coverage,
            base_complexity_score=0.6,
            requested_mode="full_audit",
            external_ai_allowed=True,
            provider_available=True,
            minimum_risk=0.45,
        )
        self.assertTrue(decision["selected"])
        self.assertEqual(decision["mapping_candidate_ids"], [7])
        self.assertEqual(result.engine_name, "compute_routing_verification")

        rejected, _ = self.engine.route_model_verification(
            self.identity,
            mappings,
            assessments,
            [{"mapping_candidate_id": 7, "status": "needs_human_review"}],
            self.coverage,
            base_complexity_score=0.6,
            requested_mode="full_audit",
            external_ai_allowed=True,
            provider_available=True,
            minimum_risk=0.1,
        )
        self.assertFalse(rejected["selected"])
        self.assertIn(
            "no_deterministically_verified_mapping",
            rejected["reason_codes"],
        )

    def test_mapping_advisory_can_only_demote_and_does_not_persist_findings(self) -> None:
        class AdvisoryProvider:
            def review_mappings(self, candidates):
                return MappingReasoningResponse(items=[
                    MappingReasoningItem(
                        mapping_key=candidates[0]["mapping_key"],
                        status="plausible",
                        relevance_rank=2,
                        relevance_score=0.72,
                        findings=["sensitive plausible explanation"],
                    ),
                    MappingReasoningItem(
                        mapping_key=candidates[1]["mapping_key"],
                        status="reject",
                        relevance_rank=1,
                        relevance_score=0.88,
                        findings=["sensitive rejected explanation"],
                    ),
                ], warnings=["sensitive warning content"])

        mappings = [
            {
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.1",
                "uraian": "Evaluasi pengendalian",
                "mapping_score": 0.81,
                "status": "candidate",
                "supporting_fact_ids": [1],
                "reasons": [],
                "missing_evidence": [],
            },
            {
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.2",
                "uraian": "Pemantauan pengendalian",
                "mapping_score": 0.79,
                "status": "candidate",
                "supporting_fact_ids": [1],
                "reasons": [],
                "missing_evidence": [],
            },
        ]
        keys = ["KK3.1:3.1:3.1.1", "KK3.1:3.1:3.1.2"]
        updated, result = ConstrainedMappingReasoningEngine(AdvisoryProvider()).run(
            self.identity,
            mappings,
            self.facts,
            candidate_keys=keys,
        )
        by_detail = {item["detail_kode"]: item for item in updated}
        self.assertEqual(by_detail["3.1.1"]["status"], "candidate")
        self.assertEqual(by_detail["3.1.1"]["mapping_score"], 0.81)
        self.assertEqual(by_detail["3.1.2"]["status"], "needs_review")
        self.assertEqual(by_detail["3.1.2"]["mapping_score"], 0.79)
        self.assertEqual(by_detail["3.1.2"]["rag_rank"], 1)
        self.assertEqual(by_detail["3.1.1"]["rag_rank"], 2)
        self.assertIn("model_mapping_advisory_reject", by_detail["3.1.2"]["missing_evidence"])
        self.assertFalse(result.output["findings_content_persisted"])
        self.assertNotIn("sensitive", json.dumps(result.to_dict()))
        self.assertEqual(result.output["provider_warning_count"], 1)
        self.assertEqual(result.output["reranked_count"], 2)

    def test_mapping_advisory_failure_demotes_ambiguous_candidates_fail_closed(self) -> None:
        class FailingProvider:
            def review_mappings(self, candidates):
                raise RuntimeError("provider unavailable")

        mappings = [{
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "mapping_score": 0.8,
            "status": "candidate",
            "supporting_fact_ids": [1],
        }]
        updated, result = ConstrainedMappingReasoningEngine(FailingProvider()).run(
            self.identity,
            mappings,
            self.facts,
            candidate_keys=["KK3.1:3.1:3.1.1"],
        )
        self.assertEqual(result.status, EngineStatus.FAILED)
        self.assertEqual(updated[0]["status"], "needs_review")
        self.assertEqual(result.error_message, "Mapping reasoning provider failed.")


if __name__ == "__main__":
    unittest.main()
