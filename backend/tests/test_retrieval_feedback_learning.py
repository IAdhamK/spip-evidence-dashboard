from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.domain.retrieval import ParameterRetrievalEngine, SPIPMappingEngine
from app.analysis.learning import (
    MINIMUM_FEEDBACK_DOCUMENT_SUPPORT,
    MINIMUM_FEEDBACK_PRECISION,
    RETRIEVAL_FEEDBACK_LEARNING_VERSION,
    RetrievalFeedbackLearningEngine,
    compile_retrieval_feedback_registry,
    retrieval_parameter_catalog_sha256,
)
from app.analysis.repository import AnalysisRepository
from app.database import Database, SCHEMA
from app.migrations import MIGRATIONS, run_migrations


class RetrievalFeedbackLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameter = {
            "id": 11,
            "kk_id": "KK3.1",
            "kode": "3.1",
            "detail_kode": "3.1.1",
            "kk_title": "Kesekretariatan",
            "matrix_subunsur_name": "Penilaian Risiko",
            "subunsur_name": "Identifikasi Risiko",
            "unsur": "Penilaian Risiko",
            "evidence_hint": "Peta risiko",
            "uraian": "Manajemen risiko dilaksanakan secara berkala",
            "cara_pengujian": "Periksa kebijakan dan laporan evaluasi",
            "grades": [],
        }
        self.other_parameter = {
            **self.parameter,
            "id": 12,
            "kk_id": "KK4.1",
            "kode": "4.1",
            "detail_kode": "4.1.1",
            "uraian": "Komunikasi informasi dilaksanakan",
        }

    def _gold(self, index: int, *, parameter: dict | None = None, status: str = "expert_gold") -> dict:
        target = parameter or self.parameter
        return {
            "id": index,
            "run_id": index,
            "sha256": f"{index:064x}",
            "outcome": "corrected",
            "dataset_status": status,
            "dataset_partition": "learning",
            "selected_fact_ids": [index * 10],
            "expected_mappings": [{
                "kk_id": target["kk_id"],
                "kode": target["kode"],
                "detail_kode": target["detail_kode"],
            }],
        }

    def _fact(self, index: int, claim: str = "Forum rembug menyepakati pengendalian bersama") -> dict:
        return {
            "id": index * 10,
            "claim": claim,
            "organization": "Unit Sekretariat",
            "period": "2026",
        }

    def test_compiler_uses_only_repeated_consistent_expert_gold_terms(self) -> None:
        items = [self._gold(index) for index in range(1, 4)]
        items.append(self._gold(99, status="expert_candidate"))
        evaluation_item = self._gold(98)
        evaluation_item["dataset_partition"] = "evaluation"
        items.append(evaluation_item)
        facts = {index: [self._fact(index)] for index in range(1, 4)}
        facts[99] = [self._fact(99, "Istilah racunprediksi tidak boleh dipelajari")]
        facts[98] = [self._fact(98, "Istilah holdoutrahasia tidak boleh dipelajari")]
        compiled = compile_retrieval_feedback_registry(
            items,
            facts,
            [self.parameter, self.other_parameter],
            dataset_sha256="d" * 64,
        )
        terms = {item["normalized_term"] for item in compiled["terms"]}
        self.assertIn("rembug", terms)
        self.assertNotIn("racunprediksi", terms)
        self.assertNotIn("holdoutrahasia", terms)
        self.assertNotIn("sekretariat", terms)
        self.assertEqual(compiled["source_label_count"], 3)
        self.assertRegex(compiled["registry_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(compiled["minimum_document_support"], 3)
        reloaded_parameters = [
            {**self.parameter, "id": 901},
            {**self.other_parameter, "id": 902},
        ]
        recompiled = compile_retrieval_feedback_registry(
            items,
            facts,
            reloaded_parameters,
            dataset_sha256="d" * 64,
        )
        self.assertEqual(compiled["registry_sha256"], recompiled["registry_sha256"])

    def test_ambiguous_feedback_below_precision_is_rejected(self) -> None:
        items = [self._gold(index) for index in range(1, 4)]
        items.append(self._gold(4, parameter=self.other_parameter))
        facts = {index: [self._fact(index)] for index in range(1, 5)}
        compiled = compile_retrieval_feedback_registry(
            items,
            facts,
            [self.parameter, self.other_parameter],
            dataset_sha256="e" * 64,
        )
        self.assertFalse(any(
            item["normalized_term"] == "rembug" and item["parameter_id"] == 11
            for item in compiled["terms"]
        ))

    def test_feedback_can_retrieve_without_official_overlap_but_cannot_grade(self) -> None:
        identity = DocumentIdentity("feedback.txt", "text/plain", 20, "sha", "text")
        facts = [{"id": 1, "fact_key": "x", "claim": "Rembug terlaksana", "fact_type": "activity"}]
        baseline, baseline_result = ParameterRetrievalEngine().run(
            identity, facts, [self.parameter]
        )
        self.assertEqual(baseline, [])
        self.assertEqual(baseline_result.status, EngineStatus.PARTIAL)

        feedback = [{
            "parameter_id": 11,
            "kk_id": self.parameter["kk_id"],
            "kode": self.parameter["kode"],
            "detail_kode": self.parameter["detail_kode"],
            "normalized_term": "rembug",
            "term_sha256": hashlib.sha256(b"rembug").hexdigest(),
            "document_support": 3,
            "precision": 1.0,
            "registry_sha256": "a" * 64,
        }]
        retrieved, result = ParameterRetrievalEngine().run(
            identity,
            facts,
            [self.parameter],
            feedback_terms=feedback,
        )
        self.assertEqual([item["parameter_id"] for item in retrieved], [11])
        self.assertEqual(result.output["feedback_authority"], "active_two_person_expert_gold_only")
        self.assertEqual(result.output["feedback_registry_sha256"], "a" * 64)
        self.assertEqual(result.metrics["feedback_assisted_candidate_count"], 1)
        mappings, _ = SPIPMappingEngine().run(identity, facts, retrieved)
        self.assertTrue(mappings[0]["supporting_fact_ids"])
        self.assertNotIn("candidate_grade", mappings[0])
        self.assertTrue(any("grade tetap" in reason for reason in mappings[0]["reasons"]))

    def test_snapshot_is_immutable_versioned_and_stale_dataset_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "feedback.db"))
            db.ensure_mapping()
            db.ensure_parameters()
            repository = AnalysisRepository(db)
            parameter = repository.parameter_index()[0]
            term = "rembug"
            compiled = {
                "dataset_sha256": "a" * 64,
                "pipeline_version": PIPELINE_VERSION,
                "learning_version": RETRIEVAL_FEEDBACK_LEARNING_VERSION,
                "parameter_catalog_sha256": retrieval_parameter_catalog_sha256(
                    repository.parameter_index()
                ),
                "registry_sha256": "b" * 64,
                "source_label_count": 3,
                "term_count": 1,
                "minimum_document_support": MINIMUM_FEEDBACK_DOCUMENT_SUPPORT,
                "minimum_precision": MINIMUM_FEEDBACK_PRECISION,
                "terms": [{
                    "parameter_id": parameter["id"],
                    "kk_id": parameter["kk_id"],
                    "kode": parameter["kode"],
                    "detail_kode": parameter["detail_kode"],
                    "normalized_term": term,
                    "term_sha256": hashlib.sha256(term.encode()).hexdigest(),
                    "document_support": 3,
                    "observed_document_count": 3,
                    "precision": 1.0,
                }],
            }
            saved = repository.save_retrieval_feedback_snapshot(
                compiled, expert_gold_case_count=3
            )
            self.assertTrue(saved["is_active"])
            with patch.object(
                repository,
                "expert_dataset_summary",
                return_value={"learning_dataset_sha256": "a" * 64},
            ):
                active = repository.active_retrieval_feedback_terms()
                self.assertEqual(
                    [item["term_sha256"] for item in active],
                    [hashlib.sha256(term.encode()).hexdigest()],
                )
                self.assertNotIn("normalized_term", active[0])
                self.assertTrue(repository.retrieval_feedback_summary()["active"])
                self.assertTrue(
                    repository.retrieval_feedback_summary()["contains_term_fingerprints"]
                )
            with patch.object(
                repository,
                "expert_dataset_summary",
                return_value={"learning_dataset_sha256": "c" * 64},
            ):
                self.assertEqual(repository.active_retrieval_feedback_terms(), [])
                self.assertFalse(repository.retrieval_feedback_summary()["active"])

            with self.assertRaisesRegex(RuntimeError, "immutable"):
                repository.save_retrieval_feedback_snapshot(
                    {**compiled, "registry_sha256": "d" * 64},
                    expert_gold_case_count=3,
                )
            with db.connect() as conn:
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(retrieval_feedback_terms)")
                }
                self.assertNotIn("normalized_term", columns)
                self.assertNotIn("claim", columns)
                with self.assertRaisesRegex(Exception, "immutable"):
                    conn.execute(
                        "UPDATE retrieval_feedback_terms SET term_sha256 = ?",
                        ("f" * 64,),
                    )
                with self.assertRaisesRegex(Exception, "append-only"):
                    conn.execute("DELETE FROM retrieval_feedback_terms")

    def test_refresh_failure_deactivates_registry(self) -> None:
        class BrokenRepository:
            deactivated = False

            def expert_dataset_summary(self):
                raise RuntimeError("fixture failure")

            def deactivate_retrieval_feedback_snapshots(self):
                self.deactivated = True

        repository = BrokenRepository()
        result = RetrievalFeedbackLearningEngine().refresh_fail_closed(repository)
        self.assertFalse(result["active"])
        self.assertTrue(repository.deactivated)
        self.assertEqual(result["error_type"], "RuntimeError")

    def test_partition_overlap_deactivates_learning_without_compilation(self) -> None:
        class OverlapRepository:
            deactivated = False

            def expert_dataset_summary(self):
                return {
                    "partition_overlap_count": 1,
                    "learning_dataset_sha256": "a" * 64,
                }

            def deactivate_retrieval_feedback_snapshots(self):
                self.deactivated = True

            def list_expert_dataset_items(self):
                raise AssertionError("Overlap harus menghentikan compiler lebih awal.")

        repository = OverlapRepository()
        result = RetrievalFeedbackLearningEngine().refresh(repository)
        self.assertFalse(result["active"])
        self.assertTrue(repository.deactivated)
        self.assertIn("partisi Evaluasi dan Learning", result["reason"])

    def test_v30_upgrade_adds_reconciliation_role_and_template_expectation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "upgrade-v24.db"
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for version, name, sql in MIGRATIONS:
                if version > 24:
                    break
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                    (version, name),
                )
            document_id = conn.execute(
                """
                INSERT INTO documents(file_name, content_type, size_bytes, sha256)
                VALUES ('existing.txt', 'text/plain', 1, ?)
                """,
                ("a" * 64,),
            ).lastrowid
            run_id = conn.execute(
                """
                INSERT INTO analysis_runs(
                    document_id, pipeline_version, parser_version, rule_version,
                    prompt_version, configuration_hash
                ) VALUES (?, '2.0', '2.0', 'draft', '2.0', 'config')
                """,
                (document_id,),
            ).lastrowid
            mapping_id = conn.execute(
                """
                INSERT INTO mapping_candidates(
                    run_id, kk_id, kode, detail_kode, retrieval_score,
                    mapping_score, status
                ) VALUES (?, 'KK-1', '1.1', '1.1.1', 0.8, 0.7, 'candidate')
                """,
                (run_id,),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO extracted_facts(
                    run_id, fact_key, claim, fact_type, extraction_method, status
                ) VALUES (?, 'legacy-fact', 'Existing historical fact.',
                          'unknown', 'legacy', 'extracted')
                """,
                (run_id,),
            )
            for message in ("historical blocked one", "historical blocked two"):
                conn.execute(
                    """
                    INSERT INTO controlled_upload_actions(
                        run_id, mapping_candidate_id, reviewer_id, status,
                        destination_json, message
                    ) VALUES (?, ?, 'legacy-reviewer', 'blocked', '{}', ?)
                    """,
                    (run_id, mapping_id, message),
                )
            conn.execute(
                """
                INSERT INTO expert_review_labels(
                    run_id, reviewer_id, outcome, reason, dataset_status
                ) VALUES (?, 'legacy-owner', 'not_evidence', 'existing label', 'expert_gold')
                """,
                (run_id,),
            )
            conn.execute(
                """
                INSERT INTO retrieval_feedback_snapshots(
                    dataset_sha256, pipeline_version, learning_version,
                    parameter_catalog_sha256, registry_sha256,
                    expert_gold_case_count, source_label_count, term_count,
                    minimum_document_support, minimum_precision, is_active
                ) VALUES (?, '2.0', 'v1', ?, ?, 3, 3, 0, 3, 0.8, 1)
                """,
                ("b" * 64, "c" * 64, "d" * 64),
            )
            evaluation_report_id = conn.execute(
                """
                INSERT INTO evaluation_reports(
                    pipeline_version, dataset_name, dataset_status, case_count,
                    metrics_json, report_sha256, reviewer_id, dataset_sha256,
                    generation_method, details_json
                ) VALUES (
                    '2.0', 'legacy-server-report', 'expert_gold', 50,
                    '{}', ?, 'legacy-evaluator', ?, 'server_derived_v1', '{}'
                )
                """,
                ("e" * 64, "f" * 64),
            ).lastrowid
            conn.commit()
            run_migrations(conn)
            label = conn.execute(
                """
                SELECT dataset_partition, expected_template_status
                FROM expert_review_labels WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            snapshot = conn.execute(
                "SELECT is_active FROM retrieval_feedback_snapshots"
            ).fetchone()
            max_migration = conn.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()["version"]
            report = conn.execute(
                "SELECT release_authority FROM evaluation_reports WHERE id = ?",
                (evaluation_report_id,),
            ).fetchone()
            controlled_upload_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(controlled_upload_actions)"
                ).fetchall()
            }
            controlled_upload_indexes = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA index_list(controlled_upload_actions)"
                ).fetchall()
            }
            reconciliation_tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            reconciliation_triggers = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            fact_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(extracted_facts)"
                ).fetchall()
            }
            legacy_fact = conn.execute(
                """
                SELECT evidence_role, evidence_role_method
                FROM extracted_facts WHERE fact_key = 'legacy-fact'
                """
            ).fetchone()
            historical_uploads = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN idempotency_key IS NULL THEN 1 ELSE 0 END) AS null_keys
                FROM controlled_upload_actions
                """
            ).fetchone()
            with self.assertRaisesRegex(Exception, "append-only"):
                conn.execute(
                    "UPDATE evaluation_reports SET release_authority = 1 WHERE id = ?",
                    (evaluation_report_id,),
                )
            historical_action_id = conn.execute(
                "SELECT MIN(id) AS id FROM controlled_upload_actions"
            ).fetchone()["id"]
            reconciliation_event_id = conn.execute(
                """
                INSERT INTO controlled_upload_reconciliation_events(
                    action_id, reviewer_id, outcome, reason, attested
                ) VALUES (?, 'operator-one', 'needs_investigation',
                          'Historical action checked during upgrade.', 1)
                """,
                (historical_action_id,),
            ).lastrowid
            with self.assertRaisesRegex(Exception, "append-only"):
                conn.execute(
                    "UPDATE controlled_upload_reconciliation_events SET reason = 'changed' WHERE id = ?",
                    (reconciliation_event_id,),
                )
            conn.close()
        self.assertEqual(label["dataset_partition"], "evaluation")
        self.assertEqual(snapshot["is_active"], 0)
        self.assertEqual(report["release_authority"], 0)
        self.assertEqual(max_migration, 31)
        self.assertEqual(label["expected_template_status"], "not_assessed")
        self.assertIn("idempotency_key", controlled_upload_columns)
        self.assertIn(
            "idx_controlled_upload_actions_idempotency",
            controlled_upload_indexes,
        )
        self.assertEqual(historical_uploads["total"], 2)
        self.assertEqual(historical_uploads["null_keys"], 2)
        self.assertIn("controlled_upload_reconciliation_events", reconciliation_tables)
        self.assertIn(
            "trg_controlled_upload_reconciliation_no_update",
            reconciliation_triggers,
        )
        self.assertIn("evidence_role", fact_columns)
        self.assertIn("evidence_role_method", fact_columns)
        self.assertEqual(legacy_fact["evidence_role"], "context")
        self.assertEqual(legacy_fact["evidence_role_method"], "legacy_default_v1")


if __name__ == "__main__":
    unittest.main()
