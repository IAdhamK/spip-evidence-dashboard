from __future__ import annotations

import unittest

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis import RULE_VERSION
from app.analysis.domain.grading import (
    DomainRuleGradeEngine,
    IndependentVerificationEngine,
    ModelSecondPassVerificationEngine,
    compile_parameter_rules,
    detect_disqualifiers,
    resolve_evidence_context,
    rule_checksum,
)
from app.analysis.provider import ModelVerificationItem, ModelVerificationResponse
from app.analysis.domain.retrieval import ParameterRetrievalEngine, SPIPMappingEngine


class DecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = DocumentIdentity("evidence.txt", "text/plain", 100, "sha", "text")
        self.facts = [
            {
                "id": 1,
                "fact_key": "policy",
                "claim": "Kebijakan manajemen risiko telah ditetapkan melalui keputusan pimpinan tahun 2026.",
                "fact_type": "policy",
                "period": "2026",
                "organization": "Ditjen PDP",
                "sources": [{
                    "unit_id": 1,
                    "source_location": "line:1",
                    "source_quote": "Kebijakan manajemen risiko telah ditetapkan",
                    "source_quote_verified": True,
                }],
            },
            {
                "id": 2,
                "fact_key": "implementation",
                "claim": "Peta risiko dan rencana tindak pengendalian telah dilaksanakan pada Ditjen PDP tahun 2026.",
                "fact_type": "implementation",
                "period": "2026",
                "organization": "Ditjen PDP",
                "sources": [{
                    "unit_id": 1,
                    "source_location": "line:2",
                    "source_quote": "Peta risiko dan rencana tindak pengendalian telah dilaksanakan",
                    "source_quote_verified": True,
                }],
            },
            {
                "id": 3,
                "fact_key": "evaluation",
                "claim": "Pelaksanaan manajemen risiko telah dievaluasi secara berkala pada tahun 2026.",
                "fact_type": "evaluation",
                "period": "2026",
                "organization": "Ditjen PDP",
                "sources": [{
                    "unit_id": 2,
                    "source_location": "line:3",
                    "source_quote": "Pelaksanaan manajemen risiko telah dievaluasi secara berkala",
                    "source_quote_verified": True,
                }],
            },
        ]
        self.parameter = {
            "id": 11,
            "kk_id": "KK3.1",
            "kk_title": "Kesekretariatan",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "matrix_subunsur_name": "Penilaian Risiko",
            "subunsur_name": "Identifikasi Risiko",
            "unsur": "Penilaian Risiko",
            "evidence_hint": "Peta risiko dan RTP",
            "uraian": "Manajemen risiko dilaksanakan dan dievaluasi secara berkala",
            "cara_pengujian": "Periksa kebijakan, peta risiko, dan laporan evaluasi",
            "grades": [
                {"grade": "E", "kriteria": "Kebijakan telah ditetapkan"},
                {"grade": "C", "kriteria": "Kebijakan telah diimplementasikan"},
                {"grade": "B", "kriteria": "Implementasi telah dievaluasi secara berkala"},
                {"grade": "A", "kriteria": "Hasil evaluasi telah ditindaklanjuti untuk perbaikan"},
            ],
        }

    def test_parameter_first_mapping_and_draft_rule_gate(self) -> None:
        retrieved, retrieval_result = ParameterRetrievalEngine().run(
            self.identity,
            self.facts,
            [self.parameter],
        )
        self.assertEqual(retrieval_result.status, EngineStatus.COMPLETED)
        self.assertEqual(retrieval_result.output["parameter_scope"], "parameter_only_without_grade")
        mappings, mapping_result = SPIPMappingEngine().run(self.identity, self.facts, retrieved)
        self.assertEqual(mapping_result.status, EngineStatus.COMPLETED)
        self.assertEqual(mappings[0]["detail_kode"], "3.1.1")
        self.assertTrue(mappings[0]["supporting_fact_ids"])

        mapping = {**mappings[0], "id": 21}
        assessments, grade_result = DomainRuleGradeEngine().run(self.identity, [mapping], self.facts)
        self.assertEqual(grade_result.status, EngineStatus.PARTIAL)
        self.assertEqual(assessments[0]["candidate_grade"], "B")
        self.assertFalse(assessments[0]["primary_allowed"])
        self.assertEqual(assessments[0]["rule_trace"]["approval_status"], "draft")

        results, verifier_result = IndependentVerificationEngine().run(
            self.identity,
            {"coverage_status": "complete"},
            [mapping],
            assessments,
            self.facts,
        )
        self.assertEqual(verifier_result.status, EngineStatus.PARTIAL)
        self.assertEqual(results[0]["status"], "needs_human_review")
        self.assertFalse(results[0]["grade_rule_ok"])

    def test_retrieval_abstains_without_overlap(self) -> None:
        facts = [{"id": 1, "fact_key": "x", "claim": "Cuaca cerah di halaman kantor.", "fact_type": "unknown"}]
        retrieved, result = ParameterRetrievalEngine().run(self.identity, facts, [self.parameter])
        self.assertEqual(retrieved, [])
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertIn("abstain", result.warnings[0])

    def test_advanced_rag_matches_conservative_spip_paraphrases(self) -> None:
        parameter = {
            **self.parameter,
            "uraian": "Pelaksanaan kebijakan dipantau dan dievaluasi secara berkala",
            "cara_pengujian": "Periksa laporan monitoring implementasi kebijakan",
            "evidence_hint": "Laporan evaluasi pelaksanaan",
        }
        facts = [{
            "id": 9,
            "fact_key": "paraphrase",
            "claim": "Penerapan pedoman telah direviu secara berkala oleh unit kerja.",
            "fact_type": "evaluation",
        }]
        retrieved, result = ParameterRetrievalEngine(advanced_rag_enabled=True).run(
            self.identity,
            facts,
            [parameter],
            query_expansions=["implementasi kebijakan monitoring berkala asteroid"],
        )
        self.assertEqual(len(retrieved), 1)
        self.assertGreater(retrieved[0]["retrieval_components"]["semantic_vector"], 0)
        self.assertEqual(
            result.output["retrieval_method"],
            "advanced_hybrid_bm25_cosine_semantic_rrf_feedback_v1",
        )
        self.assertTrue(result.output["model_query_expansion_used"])
        self.assertEqual(result.metrics["rejected_model_expansion_token_count"], 1)

        mappings, _ = SPIPMappingEngine().run(self.identity, facts, retrieved)
        self.assertEqual(mappings[0]["rag_rank"], 1)
        self.assertEqual(mappings[0]["rag_method"], "advanced_rag_local_v1")

    def test_mapping_keeps_relevant_policy_when_evaluation_facts_fill_top_twelve(self) -> None:
        facts = [
            {
                "id": index,
                "fact_key": f"evaluation-{index}",
                "claim": "Monitoring risiko dilakukan berkala.",
                "fact_type": "evaluation",
                "evidence_role": "primary",
            }
            for index in range(1, 13)
        ]
        facts.append({
            "id": 99,
            "fact_key": "policy-99",
            "claim": "Kebijakan manajemen risiko ditetapkan pimpinan.",
            "fact_type": "policy",
            "evidence_role": "supporting",
        })
        retrieved = [{
            "parameter_id": 11,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "retrieval_score": 0.9,
            "corpus_tokens": ["monitoring", "risiko", "kebijakan", "manajemen"],
        }]
        mappings, _ = SPIPMappingEngine().run(self.identity, facts, retrieved)
        self.assertIn(99, mappings[0]["supporting_fact_ids"])
        self.assertIn("policy", mappings[0]["supporting_stage_coverage"])

    def test_document_context_supplies_period_and_normalized_organization(self) -> None:
        identity = DocumentIdentity(
            "ND dan Laporan SPIP 2025 PDP.pdf",
            "application/pdf",
            100,
            "sha",
            "pdf",
        )
        supporting = [
            {**self.facts[0], "period": None, "organization": None},
            {**self.facts[1], "period": None, "organization": None},
            {**self.facts[2], "period": None, "organization": None},
        ]
        context_facts = [
            *supporting,
            {"id": 10, "claim": "Konteks dokumen.", "fact_type": "unknown", "period": "2025", "organization": "Ditjen PDP"},
            {"id": 11, "claim": "Konteks dokumen.", "fact_type": "unknown", "period": "2025", "organization": "Direktorat Jenderal Pembangunan Desa dan Perdesaan Tahun 2025"},
        ]
        context = resolve_evidence_context(identity, supporting, context_facts)
        self.assertEqual(context["period"]["values"], ["2025"])
        self.assertEqual(context["period"]["source"], "document_filename")
        self.assertEqual(
            context["organization"]["values"],
            ["Direktorat Jenderal Pembangunan Desa dan Perdesaan"],
        )
        self.assertEqual(context["organization"]["source"], "document_consensus")

    def test_plan_is_not_disqualifying_when_relevant_result_is_present(self) -> None:
        facts = [
            {
                "claim": "Rencana tindak pengendalian disusun oleh unit kerja.",
                "fact_type": "implementation",
                "evidence_role": "primary",
            },
            {
                "claim": "Tindak pengendalian telah diimplementasikan dan efektif menurunkan risiko.",
                "fact_type": "implementation",
                "evidence_role": "primary",
            },
        ]
        self.assertNotIn("plan_without_result", detect_disqualifiers(facts))

    def test_verifier_accepts_conservative_document_context_inheritance(self) -> None:
        identity = DocumentIdentity(
            "Laporan SPIP 2025.pdf",
            "application/pdf",
            100,
            "sha",
            "pdf",
        )
        supporting = [
            {**fact, "period": None, "organization": None}
            for fact in self.facts
        ]
        facts = [
            *supporting,
            {"id": 10, "claim": "Konteks laporan 2025.", "fact_type": "unknown", "period": "2025", "organization": "Ditjen PDP", "sources": []},
            {"id": 11, "claim": "Konteks unit.", "fact_type": "unknown", "period": "2025", "organization": "Direktorat Jenderal Pembangunan Desa dan Perdesaan", "sources": []},
        ]
        mapping = {
            "id": 24,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "status": "candidate",
            "supporting_fact_ids": [1, 2, 3],
        }
        assessment = {
            "mapping_candidate_id": 24,
            "candidate_grade": "B",
            "grade_ceiling": "B",
            "rule_trace": {"approval_status": "approved"},
        }
        results, _ = IndependentVerificationEngine().run(
            identity,
            {"coverage_status": "complete"},
            [mapping],
            [assessment],
            facts,
        )
        self.assertTrue(results[0]["period_ok"])
        self.assertTrue(results[0]["organization_ok"])

    def test_exact_domain_approval_checksum_opens_rule_gate(self) -> None:
        rules = compile_parameter_rules(self.parameter["grades"])
        approvals = [
            {
                "kk_id": self.parameter["kk_id"],
                "kode": self.parameter["kode"],
                "detail_kode": self.parameter["detail_kode"],
                "grade": grade,
                "rule_version": RULE_VERSION,
                "rule_checksum": rule_checksum(rule),
                "status": "approved",
            }
            for grade, rule in rules.items()
        ]
        mapping = {
            "id": 21,
            "kk_id": self.parameter["kk_id"],
            "kode": self.parameter["kode"],
            "detail_kode": self.parameter["detail_kode"],
            "supporting_fact_ids": [1, 2, 3],
            "grades": self.parameter["grades"],
        }
        assessments, result = DomainRuleGradeEngine().run(
            self.identity, [mapping], self.facts, approvals
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertTrue(assessments[0]["primary_allowed"])
        self.assertEqual(assessments[0]["rule_trace"]["approval_status"], "approved")

        approvals[0]["rule_checksum"] = "stale"
        stale, stale_result = DomainRuleGradeEngine().run(
            self.identity, [mapping], self.facts, approvals
        )
        self.assertEqual(stale_result.status, EngineStatus.PARTIAL)
        self.assertFalse(stale[0]["primary_allowed"])

    def test_retrieval_abstains_for_weak_generic_overlap(self) -> None:
        facts = [{"id": 1, "fact_key": "x", "claim": "Dashboard monitoring aplikasi tersedia.", "fact_type": "unknown"}]
        retrieved, result = ParameterRetrievalEngine().run(self.identity, facts, [self.parameter])
        self.assertEqual(retrieved, [])
        self.assertEqual(result.status, EngineStatus.PARTIAL)

    def test_model_second_pass_cannot_override_deterministic_rejection(self) -> None:
        class AlwaysApproveProvider:
            def verify_mappings(self, candidates):
                return ModelVerificationResponse(
                    items=[
                        ModelVerificationItem(
                            mapping_candidate_id=candidates[0]["mapping_candidate_id"],
                            status="verified",
                            findings=[],
                        )
                    ]
                )

        mapping = {
            "id": 21,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "supporting_fact_ids": [1],
        }
        deterministic = [{
            "mapping_candidate_id": 21,
            "status": "needs_human_review",
            "source_coverage_ok": True,
            "grade_rule_ok": False,
            "period_ok": True,
            "organization_ok": True,
        }]
        results, engine_result = ModelSecondPassVerificationEngine(AlwaysApproveProvider()).run(
            self.identity, [mapping], [], self.facts, deterministic
        )
        self.assertEqual(engine_result.status, EngineStatus.PARTIAL)
        self.assertEqual(results[0]["status"], "needs_human_review")
        self.assertTrue(any("tidak dapat override" in item for item in results[0]["findings"]))

    def test_verifier_rejects_overgrade_even_when_rule_is_approved(self) -> None:
        mapping = {
            "id": 21,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "supporting_fact_ids": [1, 2, 3],
        }
        assessment = {
            "mapping_candidate_id": 21,
            "candidate_grade": "A",
            "grade_ceiling": "E",
            "rule_trace": {"approval_status": "approved"},
        }
        results, engine = IndependentVerificationEngine().run(
            self.identity,
            {"coverage_status": "complete"},
            [mapping],
            [assessment],
            self.facts,
        )
        self.assertEqual(engine.status, EngineStatus.PARTIAL)
        self.assertFalse(results[0]["grade_rule_ok"])
        self.assertIn("melampaui grade ceiling", results[0]["findings"][0])

    def test_verifier_rejects_quote_not_found_in_source_unit(self) -> None:
        facts = [{**self.facts[0], "sources": [{
            **self.facts[0]["sources"][0],
            "source_quote_verified": False,
        }]}]
        mapping = {
            "id": 22,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "supporting_fact_ids": [1],
        }
        assessment = {
            "mapping_candidate_id": 22,
            "candidate_grade": "E",
            "grade_ceiling": "E",
            "rule_trace": {"approval_status": "approved"},
        }
        results, engine = IndependentVerificationEngine().run(
            self.identity,
            {"coverage_status": "complete"},
            [mapping],
            [assessment],
            facts,
        )
        self.assertEqual(engine.status, EngineStatus.PARTIAL)
        self.assertFalse(results[0]["source_coverage_ok"])
        self.assertIn("kutipan yang cocok", results[0]["findings"][0])

    def test_verifier_never_promotes_mapping_demoted_by_model_advisory(self) -> None:
        mapping = {
            "id": 23,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "status": "needs_review",
            "supporting_fact_ids": [1],
        }
        assessment = {
            "mapping_candidate_id": 23,
            "candidate_grade": "E",
            "grade_ceiling": "E",
            "rule_trace": {"approval_status": "approved"},
        }
        results, engine = IndependentVerificationEngine().run(
            self.identity,
            {"coverage_status": "complete"},
            [mapping],
            [assessment],
            [self.facts[0]],
        )
        self.assertEqual(engine.status, EngineStatus.PARTIAL)
        self.assertFalse(results[0]["mapping_status_ok"])
        self.assertEqual(results[0]["status"], "needs_human_review")
        self.assertIn("needs_review", results[0]["findings"][0])

    def test_rule_contract_rejects_plan_without_result_and_pre_effective_period(self) -> None:
        grades = [{
            "grade": "E",
            "kriteria": "Kebijakan ditetapkan untuk periode tahun berjalan.",
            "effective_date": "2027-01-01",
        }]
        rules = compile_parameter_rules(grades)
        rule = rules["E"]
        self.assertEqual(rule["effective_date"], "2027-01-01")
        self.assertEqual(rule["prerequisite_grade"], None)
        self.assertIn("policy_document", rule["required_source_types"])
        facts = [{
            **self.facts[0],
            "claim": "Rencana kebijakan Ditjen PDP tahun 2026.",
            "period": "2026",
        }]
        mapping = {
            "id": 31,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "supporting_fact_ids": [1],
            "grades": grades,
        }
        approvals = [{
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "grade": "E",
            "rule_version": RULE_VERSION,
            "rule_checksum": rule_checksum(rule),
            "status": "approved",
        }]
        assessments, result = DomainRuleGradeEngine().run(
            self.identity, [mapping], facts, approvals
        )
        trace = assessments[0]["rule_trace"]["rules"][0]
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertIsNone(assessments[0]["candidate_grade"])
        self.assertIn("disqualifier:plan_without_result", trace["missing_requirements"])
        self.assertIn("period:before_effective_date", trace["missing_requirements"])


if __name__ == "__main__":
    unittest.main()
