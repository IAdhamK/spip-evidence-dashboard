from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis import RULE_VERSION
from app.analysis.contracts import EngineResult, EngineStatus
from app.analysis.jobs import AnalysisJobManager
from app.analysis.repository import AnalysisRepository
from app.analysis.routes import create_analysis_router
from app.config import Settings
from app.database import Database


class WorkbookEvidenceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        database_path = str(Path(self.temporary_directory.name) / "workbook-api.db")
        self.database = Database(database_path)
        self.database.ensure_mapping()
        self.database.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=database_path,
            analysis_pipeline_v2_enabled=True,
            analysis_worker_limit=1,
            analysis_structured_model_enabled=False,
            analysis_model_verifier_enabled=False,
        )
        self.repository = AnalysisRepository(self.database, settings=self.settings)
        self.manager = AnalysisJobManager(self.database, self.settings)
        self.settings_patch = patch(
            "app.analysis.routes.get_settings",
            return_value=self.settings,
        )
        self.settings_patch.start()
        application = FastAPI()
        application.include_router(create_analysis_router(self.database, self.manager))
        self.client = TestClient(application)

    def tearDown(self) -> None:
        self.client.close()
        self.manager.stop()
        self.settings_patch.stop()
        self.temporary_directory.cleanup()

    def _create_run(
        self,
        *,
        file_name: str,
        content_type: str,
        file_kind: str,
        status: str = "review_required",
        sheet_names: list[str | None] | None = None,
        mappings: list[tuple[str, list[int], float]] | None = None,
        claims: list[str] | None = None,
    ) -> tuple[int, dict[int, int]]:
        payload = f"canonical fixture:{file_name}".encode()
        sha256 = hashlib.sha256(payload).hexdigest()
        document = self.repository.upsert_document(
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(payload),
            sha256=sha256,
            payload=payload,
            ttl_hours=1,
        )
        run_id = self.repository.create_run(
            document_id=int(document["id"]),
            analysis_mode="full_audit",
            pipeline_version="2.0-workbook-api-test",
            parser_version="native-test",
            rule_version=RULE_VERSION,
            prompt_version="prompt-test",
            provider=None,
            model=None,
            configuration_hash="workbook-api-test",
        )
        self.repository.save_engine_result(
            run_id,
            EngineResult(
                engine_name="file_router",
                engine_version="test",
                status=EngineStatus.COMPLETED,
                input_checksum=sha256,
                coverage={"required": 1, "processed": 1, "failed": 0},
                metrics={"duration_ms": 1},
                output={"file_kind": file_kind},
            ),
        )

        fact_ids_by_sheet: dict[int, int] = {}
        units = []
        for ordinal, sheet_name in enumerate(sheet_names or [], start=1):
            source_location = {"sheet_index": ordinal, "hidden": False}
            metadata = {"state": "visible", "unit_evidence_role": "primary"}
            if sheet_name is not None:
                source_location["sheet"] = sheet_name
                metadata["name"] = sheet_name
            units.append({
                "unit_key": f"sheet-{ordinal}",
                "unit_type": "sheet",
                "ordinal": ordinal,
                "source_location": source_location,
                "text": f"Bukti substantif unik {ordinal} untuk {sheet_name or 'tanpa nama'}. ",
                "status": "processed",
                "metadata": metadata,
                "warnings": [],
            })
        unit_ids = self.repository.save_document_units(run_id, units)
        for ordinal, (unit_id, sheet_name) in enumerate(
            zip(unit_ids, sheet_names or []),
            start=1,
        ):
            source_location = {"sheet_index": ordinal, "cell": "A1"}
            if sheet_name is not None:
                source_location["sheet"] = sheet_name
            claim = (
                claims[ordinal - 1]
                if claims and ordinal <= len(claims)
                else f"RAW-CONTENT-SENTINEL-{ordinal} bukti substantif tahun 2026."
            )
            saved = self.repository.save_extracted_facts(run_id, [{
                "fact_key": f"fact-sheet-{ordinal}",
                "claim": claim,
                "fact_type": "implementation",
                "organization": "Unit Pengujian",
                "period": "2026",
                "confidence": 0.95,
                "extraction_method": "test",
                "status": "extracted",
                "evidence_role": "primary",
                "evidence_role_method": "test",
                "source": {
                    "unit_id": unit_id,
                    "unit_key": f"sheet-{ordinal}",
                    "source_location": source_location,
                    "source_quote": claim,
                },
            }])
            fact_ids_by_sheet[ordinal] = int(saved[0]["id"])

        saved_mappings = self.repository.save_mapping_candidates(run_id, [
            {
                "kk_id": "KK3.1",
                "kode": ".".join(detail_kode.split(".")[:2]),
                "detail_kode": detail_kode,
                "retrieval_score": score,
                "mapping_score": score,
                "status": "candidate",
                "supporting_fact_ids": [
                    fact_ids_by_sheet[index] for index in supporting_sheet_indexes
                ],
                "reasons": ["fixture"],
                "missing_evidence": [],
                "decision_status": "needs_review",
                "document_family": "risk_management",
                "document_role": "primary",
                "family_parameter_compatible": True,
                "grade_eligible": False,
                "grade_status": "direction_only",
                "grade_block_reasons": ["Rule belum disahkan."],
            }
            for detail_kode, supporting_sheet_indexes, score in (mappings or [])
        ])
        for mapping in saved_mappings:
            self.repository.save_grade_assessment(run_id, {
                "mapping_candidate_id": int(mapping["id"]),
                "candidate_grade": "B",
                "grade_ceiling": "B",
                "rule_version": RULE_VERSION,
                "rule_trace": {"authority": "existing_test_assessment"},
                "missing_requirements": ["Pengesahan rule"],
                "primary_allowed": False,
                "grade_eligible": False,
                "grade_status": "direction_only",
                "grade_block_reasons": ["Rule belum disahkan."],
            })
            self.repository.save_verification_result(run_id, {
                "mapping_candidate_id": int(mapping["id"]),
                "verifier_type": "deterministic",
                "status": "needs_human_review",
                "findings": ["Rule belum disahkan."],
                "source_coverage_ok": True,
                "grade_rule_ok": False,
                "period_ok": True,
                "organization_ok": True,
            })
        self.repository.update_run(
            run_id,
            status=status,
            coverage_status="complete" if status == "review_required" else "pending",
            primary_blocked=True,
        )
        return run_id, fact_ids_by_sheet

    def test_xlsx_endpoint_adds_multi_evidence_and_preserves_existing_fields(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="multi-evidence.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=["Register Risiko", "RTP"],
            mappings=[
                ("2.1.2", [1], 0.91),
                ("2.2.3", [2], 0.88),
            ],
        )

        expected_facts = self.repository.list_facts(run_id)
        expected_mappings = self.repository.list_mapping_candidates(run_id)
        expected_assessments = self.repository.list_grade_assessments(run_id)
        expected_verifications = self.repository.list_verification_results(run_id)
        expected_events = self.repository.list_events(run_id)
        expected_upload_actions = self.repository.list_controlled_upload_actions(run_id)
        response = self.client.get(f"/api/analysis-runs/{run_id}")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue({
            "run", "facts", "mappings", "grade_assessments",
            "verification_results", "workbook_evidence",
        }.issubset(body))
        self.assertEqual(body["facts"], expected_facts)
        self.assertEqual(body["grade_assessments"], expected_assessments)
        self.assertEqual(body["verification_results"], expected_verifications)
        self.assertEqual(body["events"], expected_events)
        self.assertEqual(body["controlled_upload_actions"], expected_upload_actions)
        self.assertEqual(self.repository.list_mapping_candidates(run_id), expected_mappings)
        self.assertEqual(self.repository.list_events(run_id), expected_events)
        self.assertEqual(
            self.repository.list_controlled_upload_actions(run_id),
            expected_upload_actions,
        )
        workbook = body["workbook_evidence"]
        self.assertEqual(workbook["status"], "ready")
        self.assertTrue(workbook["applicable"])
        self.assertTrue(workbook["is_multi_evidence"])
        sources = {
            item["detail_kode"]: [source["sheet_name"] for source in item["source_sheets"]]
            for item in workbook["workbook_summary"]["parameter_sources"]
        }
        self.assertEqual(sources["2.1.2"], ["Register Risiko"])
        self.assertEqual(sources["2.2.3"], ["RTP"])
        primary = workbook["sheet_results"][0]["primary_parameter"]
        self.assertNotIn("grade", primary)
        self.assertEqual(
            primary["existing_grade_assessment"]["source"],
            "existing_grade_assessment",
        )
        self.assertFalse(primary["existing_grade_assessment"]["primary_allowed"])

    def test_pdf_endpoint_returns_not_applicable_without_changing_existing_response(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="evidence.pdf",
            content_type="application/pdf",
            file_kind="pdf",
        )

        response = self.client.get(f"/api/analysis-runs/{run_id}")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["workbook_evidence"]["status"], "not_applicable")
        self.assertFalse(body["workbook_evidence"]["applicable"])
        self.assertEqual(body["workbook_evidence"]["sheet_results"], [])
        self.assertIn("document_units", body)
        self.assertIn("controlled_upload_actions", body)

    def test_unfinished_xlsx_does_not_publish_partial_sheet_results(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="still-running.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            status="intake",
            sheet_names=["Register Risiko"],
            mappings=[("2.1.2", [1], 0.91)],
        )

        workbook = self.client.get(
            f"/api/analysis-runs/{run_id}"
        ).json()["workbook_evidence"]

        self.assertEqual(workbook["status"], "pending")
        self.assertTrue(workbook["applicable"])
        self.assertFalse(workbook["is_multi_evidence"])
        self.assertEqual(workbook["sheet_results"], [])
        self.assertEqual(workbook["workbook_summary"]["parameter_sources"], [])

    def test_failed_xlsx_returns_terminal_safe_structure(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="failed.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            status="failed",
            sheet_names=["Register Risiko"],
            mappings=[("2.1.2", [1], 0.91)],
        )

        body = self.client.get(f"/api/analysis-runs/{run_id}").json()
        workbook = body["workbook_evidence"]

        self.assertEqual(workbook["status"], "failed")
        self.assertEqual(workbook["sheet_results"], [])
        self.assertIn(
            "RUN_FAILED",
            workbook["workbook_summary"]["warning_codes"],
        )
        self.assertTrue(bool(body["run"]["primary_blocked"]))

    def test_empty_terminal_xlsx_returns_additive_warning_without_crash(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="empty.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=[],
            mappings=[],
        )

        response = self.client.get(f"/api/analysis-runs/{run_id}")

        self.assertEqual(response.status_code, 200, response.text)
        workbook = response.json()["workbook_evidence"]
        self.assertEqual(workbook["status"], "ready")
        self.assertEqual(workbook["sheet_results"], [])
        self.assertIn(
            "NO_WORKBOOK_SHEETS",
            workbook["workbook_summary"]["warning_codes"],
        )

    def test_ambiguous_sheet_remains_fail_closed(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="ambiguous.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=["Analisis Risiko"],
            mappings=[
                ("2.2.1", [1], 0.80),
                ("2.2.2", [1], 0.77),
            ],
        )

        body = self.client.get(f"/api/analysis-runs/{run_id}").json()
        result = body["workbook_evidence"]["sheet_results"][0]

        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["primary_eligible"])
        self.assertTrue(bool(body["run"]["primary_blocked"]))
        self.assertEqual(result["primary_parameter"]["selection_status"], "ambiguous")
        self.assertTrue(any("selisih mapping score" in item for item in result["warnings"]))
        self.assertTrue(all(
            not assessment["primary_allowed"]
            for assessment in body["grade_assessments"]
        ))

    def test_missing_sheet_name_is_null_with_warning_instead_of_guessed(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="unnamed.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=[None],
            mappings=[("2.1.2", [1], 0.90)],
        )

        result = self.client.get(
            f"/api/analysis-runs/{run_id}"
        ).json()["workbook_evidence"]["sheet_results"][0]

        self.assertIsNone(result["sheet_name"])
        self.assertNotEqual(result["sheet_key"], "sheet:unknown")
        self.assertTrue(any("tidak tersedia" in warning for warning in result["warnings"]))

    def test_workbook_view_does_not_add_raw_content_to_operational_metrics(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="metrics-safe.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=["Register Risiko"],
            mappings=[("2.1.2", [1], 0.90)],
        )
        detail = self.client.get(f"/api/analysis-runs/{run_id}")
        self.assertEqual(detail.status_code, 200, detail.text)

        metrics = self.client.get("/api/analysis-runs/metrics")

        self.assertEqual(metrics.status_code, 200, metrics.text)
        serialized = json.dumps(metrics.json(), ensure_ascii=False)
        self.assertNotIn("RAW-CONTENT-SENTINEL", serialized)
        self.assertNotIn("source_quote", serialized)
        self.assertNotIn("workbook_evidence", serialized)

    def test_sheet_fallback_is_derived_and_does_not_write_canonical_mapping(self) -> None:
        run_id, fact_ids = self._create_run(
            file_name="register-fallback.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=["Register Risiko"],
            mappings=[],
            claims=[
                "Risiko telah diidentifikasi dan dituangkan dalam register risiko beserta pemilik, penyebab, dan dampaknya."
            ],
        )
        self.assertEqual(self.repository.list_mapping_candidates(run_id), [])
        engine_results_before = self.repository.list_engine_results(run_id)

        response = self.client.get(f"/api/analysis-runs/{run_id}")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        sheet = body["workbook_evidence"]["sheet_results"][0]
        self.assertEqual(sheet["primary_parameter"]["detail_kode"], "2.1.2")
        self.assertEqual(
            sheet["primary_parameter"]["provenance"]["origin"],
            "sheet_retrieval",
        )
        self.assertEqual(
            sheet["primary_parameter"]["supporting_fact_ids"],
            [fact_ids[1]],
        )
        self.assertFalse(sheet["primary_parameter"]["primary_allowed"])
        self.assertFalse(sheet["primary_eligible"])
        self.assertEqual(body["mappings"], [])
        self.assertEqual(body["grade_assessments"], [])
        self.assertEqual(self.repository.list_mapping_candidates(run_id), [])
        self.assertEqual(self.repository.list_engine_results(run_id), engine_results_before)

    def test_run_endpoint_keeps_existing_authorization_scope(self) -> None:
        run_id, _fact_ids = self._create_run(
            file_name="authorized.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            file_kind="xlsx",
            sheet_names=["Register Risiko"],
            mappings=[("2.1.2", [1], 0.91)],
        )
        self.settings.analysis_require_reviewer_identity = True
        self.settings.analysis_require_reviewer_role = True
        path = f"/api/analysis-runs/{run_id}"

        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(
            self.client.get(
                path,
                headers={
                    "X-Reviewer-Identity": "domain-owner",
                    "X-Reviewer-Roles": "domain_owner",
                },
            ).status_code,
            403,
        )
        allowed = self.client.get(
            path,
            headers={
                "X-Reviewer-Identity": "evidence-reviewer",
                "X-Reviewer-Roles": "evidence_reviewer",
            },
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)


if __name__ == "__main__":
    unittest.main()
