from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.jobs import AnalysisJobManager
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.routes import create_analysis_router
from app.analysis.repository import AnalysisRepository
from app.analysis.shadow import (
    ShadowComparisonService,
    build_shadow_comparison,
    build_shadow_report,
)
from app.config import Settings
from app.database import Database
from app.routes import create_router


class ShadowComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "shadow.db"
        self.db = Database(str(self.db_path))
        self.db.ensure_mapping()
        self.db.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=str(self.db_path),
            analysis_pipeline_v2_enabled=True,
            analysis_structured_model_enabled=False,
            analysis_model_verifier_enabled=False,
        )
        self.repository = AnalysisRepository(self.db, settings=self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _legacy_review(self, candidates: list[dict]) -> int:
        payload = b"Kebijakan indikator telah ditetapkan dan dievaluasi tahun 2026."
        return self.db.record_smart_upload_review(
            file_name="shadow.txt",
            content_type="text/plain",
            size_bytes=len(payload),
            file_sha256=hashlib.sha256(payload).hexdigest(),
            preview_text="",
            candidates=candidates,
            ai_status="ok",
            ai_message="legacy shadow fixture",
            payload=payload,
        )

    def test_comparison_and_report_are_deterministic_and_content_free(self) -> None:
        legacy = [{"kk_id": "KK3.1", "detail_kode": "1.1.1"}]
        v2 = [
            {"kk_id": "KK3.1", "detail_kode": "1.1.1"},
            {"kk_id": "KK3.2", "detail_kode": "2.1.1"},
        ]
        first, first_sha = build_shadow_comparison(
            legacy_review_id=1,
            v2_run_id=2,
            legacy_candidates=legacy,
            v2_candidates=v2,
        )
        second, second_sha = build_shadow_comparison(
            legacy_review_id=1,
            v2_run_id=2,
            legacy_candidates=legacy,
            v2_candidates=v2,
        )
        self.assertEqual(first, second)
        self.assertEqual(first_sha, second_sha)
        self.assertTrue(first["metrics"]["top_1_match"])
        self.assertEqual(first["metrics"]["legacy_coverage_by_v2"], 1.0)
        self.assertFalse(first["contains_document_content"])
        report = build_shadow_report([{
            "status": "completed",
            "comparison": first,
            "report_sha256": first_sha,
        }])
        self.assertEqual(report["completed_count"], 1)
        self.assertEqual(report["top_1_match_rate"], 1.0)
        self.assertFalse(report["review_target_reached"])
        self.assertEqual(len(report["report_sha256"]), 64)
        self.assertNotIn("shadow.txt", str(report))

    def test_completed_job_is_finalized_and_tracking_is_idempotent(self) -> None:
        result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="shadow.txt",
            content_type="text/plain",
            payload=b"Kebijakan indikator telah ditetapkan dan dievaluasi tahun 2026.",
            analysis_mode="full_audit",
        )
        run_id = int(result["run"]["id"])
        mapping = result["mappings"][0]
        legacy_review_id = self._legacy_review([{
            "kk_id": mapping["kk_id"],
            "detail_kode": mapping["detail_kode"],
        }])
        job = self.repository.enqueue_job(
            file_name="shadow.txt",
            content_type="text/plain",
            payload=b"shadow job",
            analysis_mode="full_audit",
        )
        self.repository.complete_job(job["id"], run_id)
        service = ShadowComparisonService(self.db, self.repository)
        first = service.track(legacy_review_id, job["id"])
        second = service.track(legacy_review_id, job["id"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["v2_run_id"], run_id)
        self.assertTrue(first["comparison"]["metrics"]["top_1_match"])
        self.assertEqual(first["report_sha256"], second["report_sha256"])
        report = service.report()
        self.assertEqual(report["pair_count"], 1)
        self.assertEqual(report["completed_count"], 1)

    def test_failed_or_cancelled_jobs_remain_explicit_not_silently_dropped(self) -> None:
        legacy_review_id = self._legacy_review([])
        job = self.repository.enqueue_job(
            file_name="failed.txt",
            content_type="text/plain",
            payload=b"failed shadow",
            analysis_mode="screening",
        )
        service = ShadowComparisonService(self.db, self.repository)
        queued = service.track(legacy_review_id, job["id"])
        self.assertEqual(queued["status"], "queued")
        self.repository.fail_job(job["id"], "synthetic failure")
        failed = self.repository.find_shadow_pair(legacy_review_id=legacy_review_id)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "v2_job_failed")
        report = service.report()
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["completed_count"], 0)

    def test_legacy_shadow_request_tracks_and_finalizes_pair_automatically(self) -> None:
        settings = self.settings.model_copy(update={
            "smart_upload_enabled": True,
            "smart_upload_require_ai": False,
            "ai_reasoning_enabled": False,
            "analysis_pipeline_v2_shadow": True,
            "analysis_worker_limit": 1,
        })
        manager = AnalysisJobManager(self.db, settings)
        self.addCleanup(manager.stop)
        app = FastAPI()
        app.include_router(create_router(self.db, manager))
        app.include_router(create_analysis_router(self.db, manager))
        with patch("app.routes.get_settings", return_value=settings), patch(
            "app.analysis.routes.get_settings", return_value=settings
        ), TestClient(app) as client:
            response = client.post(
                "/api/smart-upload/recommendations",
                files={
                    "file": (
                        "shadow-auto.txt",
                        b"Kebijakan indikator telah ditetapkan dan dievaluasi tahun 2026.",
                        "text/plain",
                    )
                },
                data={"analysis_mode": "full"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body["v2_shadow"]["comparison_tracked"])
            job_id = body["v2_shadow"]["job_id"]
            deadline = time.monotonic() + 5
            pair = None
            while time.monotonic() < deadline:
                job = self.repository.get_job(job_id)
                pair = self.repository.find_shadow_pair(v2_job_id=job_id)
                if (
                    job
                    and job["status"] in {"completed", "failed", "cancelled"}
                    and pair
                    and pair["status"] in {"completed", "failed", "cancelled"}
                ):
                    break
                time.sleep(0.02)
            self.assertIsNotNone(pair)
            self.assertEqual(pair["status"], "completed")
            report = client.get("/api/analysis-runs/shadow-comparison-report")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()["report"]["completed_count"], 1)
            self.assertFalse(
                report.json()["report"]["contains_document_content"]
            )
        manager.stop()


if __name__ == "__main__":
    unittest.main()
