from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.contracts import DocumentIdentity
from app.analysis.orchestrator import AnalysisOrchestrator, configuration_hash
from app.analysis.provider import MappingReasoningItem, MappingReasoningResponse
from app.config import Settings
from app.database import Database


class AnalysisOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "analysis.db"
        self.db = Database(str(self.db_path))
        self.db.ensure_mapping()
        self.db.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=str(self.db_path),
            analysis_pipeline_v2_enabled=True,
            smart_upload_max_bytes=1024 * 1024,
        )
        self.orchestrator = AnalysisOrchestrator(self.db, self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_migrations_create_engine_trace_tables(self) -> None:
        with self.db.connect() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            triggers = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
            }
            visual_review_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(visual_review_decisions)"
                ).fetchall()
            }
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
            fact_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(extracted_facts)"
                ).fetchall()
            }
            expert_label_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(expert_review_labels)"
                ).fetchall()
            }
            mapping_columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(mapping_candidates)"
                ).fetchall()
            }
        self.assertIn("analysis_runs", tables)
        self.assertIn("engine_results", tables)
        self.assertIn("security_findings", tables)
        self.assertIn("analysis_unit_checkpoints", tables)
        self.assertIn("expert_review_labels", tables)
        self.assertIn("analysis_batch_intakes", tables)
        self.assertIn("analysis_batch_members", tables)
        self.assertIn("domain_rule_approval_events", tables)
        self.assertIn("vision_capability_probes", tables)
        self.assertIn("vision_governance_decisions", tables)
        self.assertIn("visual_review_decisions", tables)
        self.assertIn("legacy_pipeline_usage_daily", tables)
        self.assertIn("retrieval_feedback_snapshots", tables)
        self.assertIn("retrieval_feedback_terms", tables)
        self.assertIn("controlled_upload_reconciliation_events", tables)
        self.assertIn("trg_visual_review_decisions_no_update", triggers)
        self.assertIn("trg_visual_review_decisions_no_delete", triggers)
        self.assertIn("trg_controlled_upload_reconciliation_no_update", triggers)
        self.assertIn("trg_controlled_upload_reconciliation_no_delete", triggers)
        self.assertIn("review_kind", visual_review_columns)
        self.assertIn("idempotency_key", controlled_upload_columns)
        self.assertIn(
            "idx_controlled_upload_actions_idempotency",
            controlled_upload_indexes,
        )
        self.assertIn("evidence_role", fact_columns)
        self.assertIn("evidence_role_method", fact_columns)
        self.assertIn("expected_template_status", expert_label_columns)
        self.assertTrue({"rag_rank", "rag_relevance", "rag_method"} <= mapping_columns)
        self.assertEqual(versions, list(range(1, 32)))

    def test_compute_routing_policy_changes_invalidate_resume_configuration_hash(self) -> None:
        baseline = configuration_hash(self.settings, "full_audit")
        changed = self.settings.model_copy(update={
            "analysis_routing_mapping_margin": 0.20,
        })
        self.assertNotEqual(
            baseline,
            configuration_hash(changed, "full_audit"),
        )

    def test_advanced_rag_policy_changes_invalidate_resume_configuration_hash(self) -> None:
        baseline = configuration_hash(self.settings, "full_audit")
        for field, value in (
            ("analysis_advanced_rag_enabled", True),
            ("analysis_advanced_rag_deepseek_enabled", True),
            ("analysis_advanced_rag_min_confidence", 0.75),
            ("analysis_advanced_rag_ambiguity_margin", 0.15),
        ):
            changed = self.settings.model_copy(update={field: value})
            self.assertNotEqual(
                baseline,
                configuration_hash(changed, "full_audit"),
                field,
            )

    def test_ocr_budget_changes_invalidate_resume_configuration_hash(self) -> None:
        baseline = configuration_hash(self.settings, "full_audit")
        for field, value in (
            ("analysis_local_ocr_timeout_seconds", 31),
            ("analysis_local_ocr_unit_budget_seconds", 181),
            ("analysis_local_ocr_document_budget_seconds", 901),
            ("analysis_local_ocr_max_attempts_per_unit", 25),
            ("analysis_local_ocr_render_batch_units", 5),
        ):
            with self.subTest(field=field):
                changed = self.settings.model_copy(update={field: value})
                self.assertNotEqual(
                    baseline,
                    configuration_hash(changed, "full_audit"),
                )

    def test_partial_visual_checkpoint_reuses_only_durable_successes(self) -> None:
        payload = b"partial-visual-resume"
        import hashlib

        checksum = hashlib.sha256(payload).hexdigest()
        document = self.orchestrator.repository.upsert_document(
            file_name="resume.pdf",
            content_type="application/pdf",
            size_bytes=len(payload),
            sha256=checksum,
            payload=payload,
            ttl_hours=1,
        )
        run_id = self.orchestrator.repository.create_run(
            document_id=document["id"],
            analysis_mode="full_audit",
            pipeline_version=PIPELINE_VERSION,
            parser_version=PARSER_VERSION,
            rule_version=RULE_VERSION,
            prompt_version=PROMPT_VERSION,
            provider=None,
            model=None,
            configuration_hash=configuration_hash(self.settings, "full_audit"),
            external_ai_allowed=False,
        )
        units = [
            {
                "unit_key": "page-1",
                "unit_type": "page",
                "ordinal": 1,
                "heading_path": [],
                "source_location": {"page": 1},
                "text": "Teks OCR tersimpan.",
                "status": "processed",
                "warnings": [],
                "metadata": {"ocr_provider": "local"},
            },
            {
                "unit_key": "page-2",
                "unit_type": "page",
                "ordinal": 2,
                "heading_path": [],
                "source_location": {"page": 2},
                "text": "",
                "status": "ocr_required",
                "warnings": ["OCR diperlukan."],
                "metadata": {},
            },
            {
                "unit_key": "page-3",
                "unit_type": "page",
                "ordinal": 3,
                "heading_path": [],
                "source_location": {"page": 3},
                "text": "Teks OCR kedua tersimpan.",
                "status": "processed",
                "warnings": [],
                "metadata": {"ocr_provider": "local"},
            },
        ]
        self.orchestrator.repository.save_document_units(run_id, units)
        self.orchestrator._checkpoint_units(
            run_id,
            "visual_ocr_manifest",
            units,
        )
        self.orchestrator.repository.save_unit_checkpoint(
            run_id,
            unit_key="page-1",
            stage="visual_ocr_batch",
            status="completed",
            input_checksum=self.orchestrator._unit_checkpoint_checksum(units[0]),
        )
        self.orchestrator.repository.save_unit_checkpoint(
            run_id,
            unit_key="page-3",
            stage="visual_ocr_batch",
            status="completed",
            input_checksum=self.orchestrator._unit_checkpoint_checksum(units[2]),
        )

        loaded = self.orchestrator._load_resumable_units(
            run_id,
            DocumentIdentity(
                "resume.pdf", "application/pdf", len(payload), checksum, "pdf"
            ),
            "full_audit",
        )
        self.assertIsNotNone(loaded)
        loaded_units, inventory, visual_complete = loaded
        self.assertEqual(inventory, {})
        self.assertFalse(visual_complete)
        self.assertEqual(
            {unit["unit_key"]: unit["status"] for unit in loaded_units},
            {
                "page-1": "processed",
                "page-2": "ocr_required",
                "page-3": "processed",
            },
        )

        tampered_units = [{**unit, "metadata": dict(unit["metadata"])} for unit in units]
        tampered_units[0]["text"] = "Teks berubah setelah checkpoint."
        self.orchestrator.repository.save_document_units(run_id, tampered_units)
        self.assertIsNone(self.orchestrator._load_resumable_units(
            run_id,
            DocumentIdentity(
                "resume.pdf", "application/pdf", len(payload), checksum, "pdf"
            ),
            "full_audit",
        ))

        self.orchestrator.repository.save_document_units(run_id, units)
        with self.db.connect() as conn:
            conn.execute(
                "DELETE FROM document_units WHERE run_id = ? AND unit_key = ?",
                (run_id, "page-2"),
            )
        self.assertIsNone(self.orchestrator._load_resumable_units(
            run_id,
            DocumentIdentity(
                "resume.pdf", "application/pdf", len(payload), checksum, "pdf"
            ),
            "full_audit",
        ))

        self.orchestrator.repository.save_document_units(run_id, units)
        metadata_tampered = [
            {**unit, "metadata": dict(unit["metadata"])} for unit in units
        ]
        metadata_tampered[2]["metadata"]["ocr_confidence"] = 1.0
        self.orchestrator.repository.save_document_units(run_id, metadata_tampered)
        self.assertIsNone(self.orchestrator._load_resumable_units(
            run_id,
            DocumentIdentity(
                "resume.pdf", "application/pdf", len(payload), checksum, "pdf"
            ),
            "full_audit",
        ))

    def test_legacy_review_backfill_is_idempotent_and_cannot_authorize_v2(self) -> None:
        review_id = self.db.record_smart_upload_review(
            file_name="legacy.txt",
            content_type="text/plain",
            size_bytes=6,
            file_sha256="legacy-sha",
            preview_text="legacy",
            candidates=[],
            ai_status="skipped",
            ai_message=None,
            payload=b"legacy",
        )
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE smart_upload_reviews
                SET upload_status = 'uploaded_primary', confirmed_at = CURRENT_TIMESTAMP,
                    confirmed_candidate_json = '{"kk_id":"KK3.1"}'
                WHERE id = ?
                """,
                (review_id,),
            )
        Database(str(self.db_path))
        Database(str(self.db_path))
        with self.db.connect() as conn:
            imported = conn.execute(
                "SELECT * FROM legacy_analysis_imports WHERE legacy_review_id = ?",
                (review_id,),
            ).fetchall()
            run = conn.execute(
                """
                SELECT analysis_runs.* FROM analysis_runs
                JOIN legacy_analysis_imports ON legacy_analysis_imports.run_id = analysis_runs.id
                WHERE legacy_analysis_imports.legacy_review_id = ?
                """,
                (review_id,),
            ).fetchone()
        self.assertEqual(len(imported), 1)
        self.assertEqual(run["analysis_mode"], "legacy_import")
        self.assertEqual(run["status"], "approved")
        self.assertTrue(run["primary_blocked"])

    def test_valid_file_builds_document_map_with_primary_blocked(self) -> None:
        payload = b"Kebijakan telah dilaksanakan dan dievaluasi."
        result = self.orchestrator.start(
            file_name="evidence.txt",
            content_type="text/plain",
            payload=payload,
            analysis_mode="full_audit",
        )
        self.assertEqual(result["run"]["status"], "review_required")
        self.assertEqual(result["run"]["coverage_status"], "complete")
        self.assertEqual(result["run"]["coverage_percentage"], 100.0)
        self.assertTrue(result["run"]["primary_blocked"])
        self.assertEqual([item["engine_name"] for item in result["engines"]], [
            "file_intake_security",
            "file_router",
            "native_parsing",
            "visual_ocr",
            "document_structure",
            "unitization_coverage",
            "template_completeness",
            "fact_extraction",
            "compute_routing_fact",
            "parameter_retrieval",
            "spip_mapping",
            "compute_routing_mapping",
            "domain_rule_grade",
            "independent_verification",
            "compute_routing_verification",
            "output_explainability",
        ])
        routing_results = {
            item["engine_name"]: item
            for item in result["engines"]
            if item["engine_name"].startswith("compute_routing_")
        }
        self.assertEqual(set(routing_results), {
            "compute_routing_fact",
            "compute_routing_mapping",
            "compute_routing_verification",
        })
        self.assertEqual(
            routing_results["compute_routing_fact"]["output"]["requested_mode"],
            "full_audit",
        )
        self.assertIn(
            "complexity_score",
            routing_results["compute_routing_mapping"]["output"],
        )
        self.assertIn(
            "risk_score",
            routing_results["compute_routing_verification"]["output"],
        )
        self.assertEqual(result["engines"][0]["metrics"]["size_bytes"], len(payload))
        self.assertEqual(result["events"][-1]["stage"], "verification")
        self.assertEqual(len(result["document_units"]), 1)
        self.assertEqual(result["document_units"][0]["source_location"]["line_start"], 1)
        self.assertEqual(result["document_structures"][0]["structure_type"], "document_map")
        self.assertTrue(result["facts"])

    def test_invalid_signature_is_blocked_and_audited(self) -> None:
        result = self.orchestrator.start(
            file_name="evidence.pdf",
            content_type="application/pdf",
            payload=b"not-a-pdf",
        )
        self.assertEqual(result["run"]["status"], "blocked")
        self.assertTrue(result["run"]["primary_blocked"])
        self.assertTrue(result["security_findings"])
        self.assertTrue(any(item["blocking"] for item in result["security_findings"]))

    def test_ambiguous_mapping_is_routed_to_demotion_only_deepseek_adapter(self) -> None:
        class RejectAmbiguousProvider:
            def review_mappings(self, candidates):
                return MappingReasoningResponse(items=[
                    MappingReasoningItem(
                        mapping_key=item["mapping_key"],
                        status="reject",
                        findings=["not persisted"],
                    )
                    for item in candidates
                ])

        settings = self.settings.model_copy(update={
            "analysis_mapping_reasoning_enabled": True,
            "analysis_routing_mapping_margin": 0.20,
            "sumopod_api_key": "test-key",
        })
        with patch(
            "app.analysis.orchestrator.configured_mapping_provider",
            return_value=RejectAmbiguousProvider(),
        ):
            orchestrator = AnalysisOrchestrator(self.db, settings)
        result = orchestrator.start(
            file_name="ambiguous.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan manajemen risiko telah ditetapkan, dilaksanakan, "
                b"dan dievaluasi tahun 2026."
            ),
            analysis_mode="full_audit",
        )
        engines = {item["engine_name"]: item for item in result["engines"]}
        self.assertTrue(engines["compute_routing_mapping"]["output"]["selected"])
        self.assertGreater(
            engines["constrained_mapping_reasoning"]["output"]["demoted_count"],
            0,
        )
        self.assertTrue(any(item["status"] == "needs_review" for item in result["mappings"]))
        self.assertTrue(
            all(
                item["status"] != "verified"
                for item in result["verification_results"]
                if item["mapping_candidate_id"] in {
                    mapping["id"]
                    for mapping in result["mappings"]
                    if mapping["status"] == "needs_review"
                }
            )
        )

    def test_same_document_reuses_identity_but_creates_new_run(self) -> None:
        first = self.orchestrator.start(
            file_name="first.txt",
            content_type="text/plain",
            payload=b"same evidence",
        )
        second = self.orchestrator.start(
            file_name="second.txt",
            content_type="text/plain",
            payload=b"same evidence",
        )
        self.assertEqual(first["run"]["document_id"], second["run"]["document_id"])
        self.assertNotEqual(first["run"]["id"], second["run"]["id"])
        with self.db.connect() as conn:
            document_count = conn.execute("SELECT COUNT(*) AS total FROM documents").fetchone()["total"]
            run_count = conn.execute("SELECT COUNT(*) AS total FROM analysis_runs").fetchone()["total"]
        self.assertEqual(document_count, 1)
        self.assertEqual(run_count, 2)

    def test_reverify_supersedes_active_results_and_preserves_history(self) -> None:
        result = self.orchestrator.start(
            file_name="reverify.txt",
            content_type="text/plain",
            payload=b"Kebijakan indikator kinerja telah ditetapkan dan dievaluasi secara berkala.",
            analysis_mode="full_audit",
        )
        run_id = result["run"]["id"]
        initial_assessment_count = len(result["grade_assessments"])
        refreshed = self.orchestrator.reverify(run_id)
        self.assertEqual(len(refreshed["grade_assessments"]), initial_assessment_count)
        from app.analysis.repository import AnalysisRepository

        repository = AnalysisRepository(self.db)
        history = repository.list_grade_assessments(run_id, include_history=True)
        self.assertEqual(len(history), initial_assessment_count * 2)
        self.assertEqual(sum(item["is_active"] for item in history), initial_assessment_count)
        self.assertEqual(refreshed["events"][-1]["event_type"], "reverification_completed")


if __name__ == "__main__":
    unittest.main()
