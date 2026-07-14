from __future__ import annotations

import unittest

from app.analysis import PIPELINE_VERSION
from app.analysis.expert_evaluation import build_expert_gold_evaluation
from app.analysis.learning import EvaluationLearningEngine


class EvaluationLearningTests(unittest.TestCase):
    def test_promotion_gate_requires_expert_metrics_rules_and_security(self) -> None:
        report = {
            "pipeline_version": PIPELINE_VERSION,
            "dataset_status": "expert_gold",
            "release_authority": True,
            "case_count": 50,
            "metrics": {
                "retrieval_recall_at_5": 0.96,
                "source_accuracy": 0.97,
                "overgrade_rate": 0.01,
                "grade_label_coverage": 0.98,
                "grade_assessment_coverage": 0.97,
                "evidence_role_label_coverage": 1.0,
                "template_label_coverage": 1.0,
                "template_detection_recall": 1.0,
            },
        }
        readiness = EvaluationLearningEngine().promotion_readiness(
            [report], approved_rule_count=920, total_rule_count=920, high_security_findings=0
        )
        self.assertTrue(readiness["shadow"]["ready"])
        self.assertTrue(readiness["canary"]["ready"])
        self.assertFalse(readiness["general_release"]["ready"])

        missing_template_recall = EvaluationLearningEngine().promotion_readiness(
            [{**report, "metrics": {**report["metrics"], "template_detection_recall": 0.0}}],
            approved_rule_count=920,
            total_rule_count=920,
            high_security_findings=0,
        )
        self.assertFalse(missing_template_recall["shadow"]["ready"])
        self.assertTrue(any(
            "Recall deteksi template kosong" in reason
            for reason in missing_template_recall["shadow"]["reasons"]
        ))

        blocked = EvaluationLearningEngine().promotion_readiness(
            [report],
            approved_rule_count=919,
            total_rule_count=920,
            high_security_findings=1,
            vision_required=True,
            vision_ready=False,
        )
        self.assertFalse(blocked["canary"]["ready"])
        self.assertEqual(len(blocked["canary"]["reasons"]), 3)
        self.assertTrue(blocked["vision_required"])

        storage_blocked = EvaluationLearningEngine().promotion_readiness(
            [report],
            approved_rule_count=920,
            total_rule_count=920,
            high_security_findings=0,
            storage_ready=False,
        )
        self.assertTrue(storage_blocked["shadow"]["ready"])
        self.assertFalse(storage_blocked["canary"]["ready"])
        self.assertFalse(storage_blocked["storage_ready"])
        self.assertTrue(
            any("Enkripsi volume" in reason for reason in storage_blocked["canary"]["reasons"])
        )

    def test_synthetic_or_missing_report_never_unlocks_shadow(self) -> None:
        readiness = EvaluationLearningEngine().promotion_readiness(
            [{"pipeline_version": PIPELINE_VERSION, "dataset_status": "synthetic_smoke"}],
            approved_rule_count=920,
            total_rule_count=920,
            high_security_findings=0,
        )
        self.assertFalse(readiness["shadow"]["ready"])

    def test_manual_report_with_perfect_metrics_has_no_promotion_authority(self) -> None:
        readiness = EvaluationLearningEngine().promotion_readiness(
            [{
                "pipeline_version": PIPELINE_VERSION,
                "dataset_status": "expert_gold",
                "release_authority": False,
                "case_count": 200,
                "metrics": {
                    "retrieval_recall_at_5": 1.0,
                    "source_accuracy": 1.0,
                    "overgrade_rate": 0.0,
                    "grade_label_coverage": 1.0,
                    "grade_assessment_coverage": 1.0,
                },
            }],
            approved_rule_count=920,
            total_rule_count=920,
            high_security_findings=0,
        )
        self.assertFalse(readiness["shadow"]["ready"])
        self.assertIsNone(readiness["latest_expert_report"])

    def test_server_derived_expert_metrics_are_deterministic_and_content_free(self) -> None:
        class FakeRepository:
            def expert_dataset_summary(self) -> dict:
                return {"expert_gold_case_count": 2, "dataset_sha256": "d" * 64}

            def list_expert_dataset_items(self) -> list[dict]:
                return [
                    {
                        "id": 1,
                        "run_id": 10,
                        "sha256": "a" * 64,
                        "outcome": "corrected",
                        "dataset_status": "expert_gold",
                        "dataset_partition": "evaluation",
                        "selected_fact_ids": [11, 12],
                        "expected_template_status": "substantive",
                        "expected_mappings": [
                            {
                                "kk_id": "KK3.1", "kode": "3.1",
                                "detail_kode": "3.1.1", "grade": "C",
                                "evidence_role": "primary",
                            }
                        ],
                    },
                    {
                        "id": 2,
                        "run_id": 20,
                        "sha256": "b" * 64,
                        "outcome": "not_evidence",
                        "dataset_status": "expert_gold",
                        "dataset_partition": "evaluation",
                        "selected_fact_ids": [],
                        "expected_template_status": "template_only",
                        "expected_mappings": [],
                    },
                    {
                        "id": 3,
                        "run_id": 30,
                        "sha256": "c" * 64,
                        "outcome": "corrected",
                        "dataset_status": "expert_gold",
                        "dataset_partition": "learning",
                        "selected_fact_ids": [31],
                        "expected_template_status": "substantive",
                        "expected_mappings": [
                            {"kk_id": "KK9.9", "kode": "9.9", "detail_kode": "9.9.9", "grade": "A"}
                        ],
                    },
                ]

            def list_mapping_candidates(self, run_id: int) -> list[dict]:
                if run_id == 30:
                    raise AssertionError("Partisi learning tidak boleh dievaluasi.")
                if run_id == 10:
                    return [{
                        "id": 101,
                        "kk_id": "KK3.1",
                        "kode": "3.1",
                        "detail_kode": "3.1.1",
                        "supporting_fact_ids": [11],
                        "secret_document_text": "must never be copied",
                    }]
                return [{
                    "id": 201,
                    "kk_id": "KK3.2",
                    "kode": "4.1",
                    "detail_kode": "4.1.1",
                    "supporting_fact_ids": [],
                }]

            def list_facts(self, run_id: int) -> list[dict]:
                if run_id == 10:
                    return [{"id": 11, "evidence_role": "primary"}]
                return []

            def get_run(self, run_id: int) -> dict:
                return {
                    "started_at": "2026-07-13 10:00:00",
                    "finished_at": "2026-07-13 10:00:10",
                    "estimated_cost_usd": 0.1 if run_id == 10 else 0.2,
                }

            def list_engine_results(self, run_id: int) -> list[dict]:
                substantive_units = 1 if run_id == 10 else 0
                return [{
                    "engine_name": "template_completeness",
                    "output": {
                        "template_ledger": {
                            "checked_units": 1,
                            "substantive_units": substantive_units,
                        }
                    },
                }]

            def list_grade_assessments(self, run_id: int) -> list[dict]:
                return [{"mapping_candidate_id": 101, "candidate_grade": "B"}] if run_id == 10 else []

        first = build_expert_gold_evaluation(FakeRepository())
        second = build_expert_gold_evaluation(FakeRepository())
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        self.assertEqual(first["metrics"]["retrieval_recall_at_5"], 1.0)
        self.assertEqual(first["metrics"]["source_accuracy"], 0.5)
        self.assertEqual(first["metrics"]["overgrade_rate"], 1.0)
        self.assertEqual(first["metrics"]["grade_label_coverage"], 1.0)
        self.assertEqual(first["metrics"]["grade_assessment_coverage"], 1.0)
        self.assertEqual(first["metrics"]["negative_false_positive_rate"], 1.0)
        self.assertEqual(first["metrics"]["mapping_precision_at_5"], 0.5)
        self.assertEqual(first["metrics"]["evidence_role_accuracy"], 1.0)
        self.assertEqual(first["metrics"]["evidence_role_label_coverage"], 1.0)
        self.assertEqual(first["metrics"]["abstention_accuracy"], 0.0)
        self.assertEqual(first["metrics"]["average_run_latency_seconds"], 10.0)
        self.assertEqual(first["metrics"]["average_estimated_cost_usd"], 0.15)
        self.assertEqual(first["metrics"]["template_detection_accuracy"], 1.0)
        self.assertEqual(first["metrics"]["template_detection_recall"], 1.0)
        self.assertEqual(first["metrics"]["template_label_coverage"], 1.0)
        self.assertNotIn("must never be copied", str(first))
        self.assertEqual(first["counters"]["case_count"], 2)

    def test_evaluation_fails_closed_when_learning_partition_overlaps(self) -> None:
        class OverlapRepository:
            def expert_dataset_summary(self) -> dict:
                return {
                    "expert_gold_case_count": 1,
                    "dataset_sha256": "d" * 64,
                    "partition_overlap_count": 1,
                }

            def list_expert_dataset_items(self) -> list[dict]:
                raise AssertionError("Item tidak boleh dibaca setelah overlap terdeteksi.")

        with self.assertRaisesRegex(ValueError, "partisi Evaluasi dan Learning"):
            build_expert_gold_evaluation(OverlapRepository())


if __name__ == "__main__":
    unittest.main()
