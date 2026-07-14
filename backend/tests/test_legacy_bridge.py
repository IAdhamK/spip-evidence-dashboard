from __future__ import annotations

import ast
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.analysis.legacy_bridge import (
    LegacyBridgeError,
    execute_legacy_controlled_upload,
    legacy_review_candidates,
)
from app.config import Settings
from app.database import Database
from app.evidence_structure import canonical_folder_path
from app.smart_upload import SmartUploadError


class LegacyBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "bridge.db"))
        self.settings = Settings(_env_file=None, smart_upload_allow_real_upload=True)
        self.run = {
            "id": 7,
            "file_name": "evidence.txt",
            "content_type": "text/plain",
            "size_bytes": 8,
            "sha256": "a" * 64,
        }
        self.candidate = {
            "kk_id": "KK3.1",
            "kode": "1.1",
            "detail_kode": "1.1.1",
            "grade": "C",
            "folder_path": "/evidence/C",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_controlled_upload_bridge_is_the_only_legacy_transport_contract(self) -> None:
        uploaded = {
            "status": "uploaded_primary",
            "message": "uploaded",
            "candidate": self.candidate,
        }
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload",
            return_value=uploaded,
        ) as confirm:
            result = execute_legacy_controlled_upload(
                self.db,
                self.settings,
                run=self.run,
                candidate=self.candidate,
                source_bytes=b"evidence",
            )
        confirm.assert_called_once_with(result["legacy_review_id"], 0)
        self.assertEqual(result["upload"], uploaded)
        stored = self.db.smart_upload_review(result["legacy_review_id"])
        self.assertEqual(stored["ai_status"], "v2_controlled")
        expected_candidate = {
            **self.candidate,
            "folder_path": canonical_folder_path(self.candidate["folder_path"]),
        }
        self.assertEqual(
            legacy_review_candidates(self.db, result["legacy_review_id"]),
            [expected_candidate],
        )

    def test_bridge_error_keeps_legacy_audit_id(self) -> None:
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload",
            side_effect=SmartUploadError("transport blocked"),
        ):
            with self.assertRaises(LegacyBridgeError) as caught:
                execute_legacy_controlled_upload(
                    self.db,
                    self.settings,
                    run=self.run,
                    candidate=self.candidate,
                    source_bytes=b"evidence",
                )
        self.assertGreater(caught.exception.legacy_review_id, 0)
        self.assertIn("transport blocked", str(caught.exception))
        self.assertIsNotNone(self.db.smart_upload_review(caught.exception.legacy_review_id))

    def test_missing_legacy_review_is_distinct_from_empty_candidates(self) -> None:
        self.assertIsNone(legacy_review_candidates(self.db, 9999))
        review_id = self.db.record_smart_upload_review(
            file_name="empty.txt",
            content_type="text/plain",
            size_bytes=0,
            file_sha256="b" * 64,
            preview_text="",
            candidates=[],
            ai_status="skipped",
            ai_message="empty",
            payload=b"",
        )
        self.assertEqual(legacy_review_candidates(self.db, review_id), [])

    def test_analysis_modules_may_import_legacy_service_only_through_bridge(self) -> None:
        analysis_dir = Path(__file__).resolve().parents[1] / "app" / "analysis"
        violations = []
        for path in analysis_dir.rglob("*.py"):
            if path.name == "legacy_bridge.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "app.smart_upload":
                    violations.append(str(path.relative_to(analysis_dir)))
                if isinstance(node, ast.Import):
                    if any(alias.name == "app.smart_upload" for alias in node.names):
                        violations.append(str(path.relative_to(analysis_dir)))
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
