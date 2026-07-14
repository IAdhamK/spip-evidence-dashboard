from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.legacy_bridge import execute_legacy_controlled_upload
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database
from app.routes import create_router


class LegacyUsageTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "legacy-usage.db"
        self.db = Database(str(self.db_path))
        self.db.ensure_mapping()
        self.db.ensure_parameters()
        self.repository = AnalysisRepository(self.db)
        report = self.repository.save_evaluation_report({
            "pipeline_version": "2.0.0-alpha.1",
            "dataset_name": "stable-cycle-authority",
            "dataset_status": "expert_gold",
            "case_count": 50,
            "metrics": {},
            "report_sha256": "a" * 64,
            "dataset_sha256": "b" * 64,
            "generation_method": "server_derived_v2_partitioned",
            "release_authority": True,
            "details": {},
            "reviewer_id": "evaluation-owner",
            "notes": "Authoritative report fixture.",
        })
        self.authoritative_report_id = int(report["id"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _stable_event(self, cycle: str, *, rollback: bool) -> dict:
        return {
            "release_cycle_id": cycle,
            "release_version": cycle,
            "stage": "canary",
            "decision": "passed",
            "pipeline_version": "2.0.0-alpha.1",
            "rule_version": "2026.2-approved",
            "model": "deepseek-v4-pro",
            "stable_cycle": True,
            "rollback_rehearsed": rollback,
            "critical_incident_count": 0,
            "reviewer_id": "product-owner",
            "reason": "Stable cycle fixture with validated release gates.",
            "evaluation_report_id": self.authoritative_report_id,
            "evidence": {
                "gate_snapshot": {
                    "validated": True,
                    "evaluation_release_authority": True,
                    "evaluation_report_sha256": "a" * 64,
                    "evaluation_recomputed_sha256": "a" * 64,
                }
            },
        }

    def test_daily_usage_is_aggregated_and_contains_no_document_fields(self) -> None:
        first = self.repository.record_legacy_usage("recommendation", "legacy_api")
        second = self.repository.record_legacy_usage("recommendation", "legacy_api")
        self.assertEqual(first["call_count"], 1)
        self.assertEqual(second["call_count"], 2)
        summary = self.repository.legacy_usage_summary()
        self.assertEqual(summary["total_call_count"], 2)
        self.assertFalse(summary["contains_document_content"])
        self.assertEqual(
            summary["by_kind"],
            [{
                "usage_kind": "recommendation",
                "source": "legacy_api",
                "call_count": 2,
                "last_used_at": summary["last_used_at"],
            }],
        )
        with self.db.connect() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(legacy_pipeline_usage_daily)")
            }
        self.assertTrue({"usage_date", "usage_kind", "source", "call_count"} <= columns)
        self.assertTrue(columns.isdisjoint({"file_name", "document_text", "payload", "reviewer_id"}))
        with self.assertRaisesRegex(ValueError, "usage_kind"):
            self.repository.record_legacy_usage("invalid kind!", "legacy_api")

    def test_deprecation_requires_telemetry_coverage_and_zero_v1_calls(self) -> None:
        self.repository.save_release_event(self._stable_event("canary-1", rollback=True))
        self.repository.save_release_event(self._stable_event("canary-2", rollback=False))
        ready = self.repository.release_evidence_summary()
        self.assertEqual(ready["stable_release_cycle_count"], 2)
        self.assertTrue(ready["legacy_usage"]["instrumented"])
        self.assertTrue(ready["legacy_usage"]["observation_coverage_valid"])
        self.assertEqual(ready["legacy_usage"]["observed_call_count"], 0)
        self.assertTrue(ready["legacy_deprecation_eligible"])

        self.repository.record_legacy_usage("action", "legacy_api")
        blocked = self.repository.release_evidence_summary()
        self.assertFalse(blocked["legacy_deprecation_eligible"])
        self.assertEqual(blocked["legacy_usage"]["observed_call_count"], 1)
        self.assertTrue(any("masih dipanggil 1 kali" in item for item in blocked["legacy_deprecation_reasons"]))

    def test_pre_v26_stable_snapshots_are_retained_but_not_authoritative(self) -> None:
        for index in (1, 2):
            event = self._stable_event(f"legacy-canary-{index}", rollback=index == 1)
            event["evidence"] = {"gate_snapshot": {"validated": True}}
            self.repository.save_release_event(event)
        summary = self.repository.release_evidence_summary()
        self.assertEqual(summary["stable_release_cycle_count"], 0)
        self.assertEqual(summary["invalidated_stable_cycle_count"], 2)
        self.assertTrue(any("authority/recompute V26" in item for item in summary["legacy_deprecation_reasons"]))

    def test_legacy_recommendation_endpoint_records_usage_but_disabled_endpoint_does_not(self) -> None:
        app = FastAPI()
        app.include_router(create_router(self.db))
        enabled = Settings(
            _env_file=None,
            smart_upload_enabled=True,
            legacy_smart_upload_enabled=True,
            analysis_pipeline_v2_enabled=False,
        )
        fake_result = {
            "review_id": 1,
            "preview_text": "must be removed",
            "link_crawl": {},
            "candidates": [],
        }
        with patch("app.routes.get_settings", return_value=enabled), patch(
            "app.routes.SmartUploadService.recommend", return_value=fake_result
        ):
            response = TestClient(app).post(
                "/api/smart-upload/recommendations",
                files={"file": ("evidence.txt", b"evidence", "text/plain")},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("preview_text", response.json())
        summary = self.repository.legacy_usage_summary()
        self.assertEqual(summary["by_kind"][0]["usage_kind"], "recommendation")
        self.assertEqual(summary["by_kind"][0]["call_count"], 1)

        disabled = enabled.model_copy(update={"legacy_smart_upload_enabled": False})
        with patch("app.routes.get_settings", return_value=disabled):
            rejected = TestClient(app).post(
                "/api/smart-upload/recommendations",
                files={"file": ("blocked.txt", b"blocked", "text/plain")},
            )
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(self.repository.legacy_usage_summary()["total_call_count"], 1)

    def test_v2_controlled_upload_bridge_records_legacy_dependency(self) -> None:
        payload = b"approved"
        settings = Settings(_env_file=None)
        run = {
            "id": 7,
            "file_name": "approved.txt",
            "content_type": "text/plain",
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload",
            return_value={"status": "uploaded_primary", "message": "fixture"},
        ):
            result = execute_legacy_controlled_upload(
                self.db,
                settings,
                run=run,
                candidate={"kk_id": "KK3.1", "kode": "1.1", "detail_kode": "1.1.1", "grade": "C"},
                source_bytes=payload,
            )
        self.assertEqual(result["upload"]["status"], "uploaded_primary")
        usage = self.repository.legacy_usage_summary()
        self.assertEqual(usage["by_kind"][0]["usage_kind"], "controlled_upload")
        self.assertEqual(usage["by_kind"][0]["source"], "v2_bridge")

    def test_all_remaining_legacy_entrypoints_emit_content_free_usage(self) -> None:
        app = FastAPI()
        app.include_router(create_router(self.db))
        settings = Settings(
            _env_file=None,
            smart_upload_enabled=True,
            legacy_smart_upload_enabled=True,
            analysis_pipeline_v2_enabled=False,
        )
        fake_result = {"review_id": 1, "preview_text": "removed", "link_crawl": {}, "candidates": []}
        with patch("app.routes.get_settings", return_value=settings), patch(
            "app.routes.SmartUploadService.test_ai_connection",
            return_value={"status": "ok"},
        ), patch(
            "app.routes.evidence_link_crawler.start",
            return_value={"running": True},
        ), patch(
            "app.routes.SmartUploadService.recommend",
            return_value=fake_result,
        ), patch(
            "app.routes.SmartUploadService.interpret_batch",
            return_value={"status": "skipped", "analysis": None},
        ), patch(
            "app.routes.SmartUploadService.perform_action",
            return_value={"status": "rejected"},
        ), patch(
            "app.routes.SmartUploadService.confirm_upload",
            return_value={"status": "uploaded_primary"},
        ):
            client = TestClient(app)
            self.assertEqual(client.get("/api/smart-upload/ai-diagnostics").status_code, 200)
            self.assertEqual(client.post("/api/smart-upload/evidence-links/crawl").status_code, 200)
            self.assertEqual(
                client.post(
                    "/api/smart-upload/recommendations/batch",
                    files=[("files", ("one.txt", b"one", "text/plain"))],
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/api/smart-upload/action",
                    json={"review_id": 1, "candidate_index": 0, "action_type": "reject"},
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/api/smart-upload/confirm-upload",
                    json={"review_id": 1, "candidate_index": 0},
                ).status_code,
                200,
            )
        usage = self.repository.legacy_usage_summary()
        self.assertEqual(
            {(item["usage_kind"], item["source"], item["call_count"]) for item in usage["by_kind"]},
            {
                ("action", "legacy_api", 1),
                ("ai_diagnostics", "legacy_api", 1),
                ("batch_recommendation", "legacy_api", 1),
                ("confirm_upload", "legacy_api", 1),
                ("evidence_link_crawl", "legacy_api", 1),
            },
        )


if __name__ == "__main__":
    unittest.main()
