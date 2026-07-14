from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analysis.orchestrator import AnalysisOrchestrator
from app.config import Settings
from app.database import Database
from scripts.analysis_db_backup import backup, restore_verified, verify_database


class BackupRestoreTests(unittest.TestCase):
    def test_online_backup_and_restore_preserve_analysis_counts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            backup_path = root / "backup.db"
            restored = root / "restored.db"
            db = Database(str(source))
            db.ensure_mapping()
            db.ensure_parameters()
            settings = Settings(_env_file=None, database_path=str(source))
            AnalysisOrchestrator(db, settings).start(
                file_name="evidence.txt",
                content_type="text/plain",
                payload=b"Kebijakan telah ditetapkan dan dievaluasi berkala tahun 2026.",
            )

            backup_report = backup(source, backup_path)
            restore_report = restore_verified(backup_path, restored)
            self.assertEqual(backup_report["integrity_check"], "ok")
            self.assertEqual(restore_report["integrity_check"], "ok")
            self.assertEqual(
                backup_report["critical_table_counts"],
                restore_report["critical_table_counts"],
            )
            self.assertIn(
                "controlled_upload_actions",
                restore_report["critical_table_counts"],
            )
            self.assertIn(
                "controlled_upload_reconciliation_events",
                restore_report["critical_table_counts"],
            )
            self.assertEqual(verify_database(restored)["integrity_check"], "ok")

            with self.assertRaises(FileExistsError):
                restore_verified(backup_path, restored)


if __name__ == "__main__":
    unittest.main()
