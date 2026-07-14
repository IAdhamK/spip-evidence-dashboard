from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.analysis.governance import VISION_POLICY_VERSION, run_synthetic_vision_probe, synthetic_probe_png
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.provider import VisionOCRItem, VisionOCRResponse
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database


class SyntheticVisionProvider:
    def analyze_images(self, images):
        return VisionOCRResponse(
            items=[
                VisionOCRItem(
                    unit_key=images[0]["unit_key"],
                    ocr_text="SPIP 2026",
                    confidence=0.99,
                )
            ]
        )


class GovernanceTests(unittest.TestCase):
    def test_synthetic_probe_is_valid_png_and_contains_no_user_document(self) -> None:
        png = synthetic_probe_png()
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 100)
        report = run_synthetic_vision_probe(
            Settings(_env_file=None, sumopod_api_key="test-key"),
            SyntheticVisionProvider(),
        )
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertFalse(report["user_document_sent"])
        self.assertEqual(len(report["report_sha256"]), 64)

    def test_runtime_vision_provider_closes_immediately_when_consent_is_revoked(self) -> None:
        with TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "governance.db"))
            settings = Settings(
                _env_file=None,
                database_path=str(Path(directory) / "governance.db"),
                vision_analysis_enabled=True,
                analysis_vision_provider_validated=True,
                sumopod_api_key="test-key",
            )
            repository = AnalysisRepository(db)
            report = run_synthetic_vision_probe(settings, SyntheticVisionProvider())
            repository.save_vision_capability_probe(report, "technical-reviewer")
            expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            common = {
                "status": "approved",
                "provider": settings.ai_provider,
                "model": settings.deepseek_model,
                "api_surface": "chat_completions_vision",
                "sensitivity_scope": "restricted",
                "policy_version": VISION_POLICY_VERSION,
                "reason": "Governance integration test approval.",
                "expires_at": expires_at,
            }
            repository.save_vision_governance_decision({
                **common,
                "scope": "capability_validation",
                "evidence_sha256": report["report_sha256"],
                "reviewer_id": "technical-reviewer",
            })
            repository.save_vision_governance_decision({
                **common,
                "scope": "external_data_processing",
                "evidence_sha256": None,
                "reviewer_id": "data-owner",
            })
            fake_provider = SyntheticVisionProvider()
            with (
                patch("app.analysis.orchestrator.shutil.which", return_value="/usr/bin/pdftoppm"),
                patch("app.analysis.orchestrator.configured_vision_provider", return_value=fake_provider),
            ):
                enabled = AnalysisOrchestrator(db, settings)
            self.assertIs(enabled.visual_ocr_engine.provider, fake_provider)

            repository.save_vision_governance_decision({
                **common,
                "scope": "external_data_processing",
                "status": "revoked",
                "evidence_sha256": None,
                "reviewer_id": "data-owner",
                "reason": "Consent revoked for runtime gate test.",
            })
            with (
                patch("app.analysis.orchestrator.shutil.which", return_value="/usr/bin/pdftoppm"),
                patch("app.analysis.orchestrator.configured_vision_provider", return_value=fake_provider),
            ):
                revoked = AnalysisOrchestrator(db, settings)
            self.assertIsNone(revoked.visual_ocr_engine.provider)


if __name__ == "__main__":
    unittest.main()
