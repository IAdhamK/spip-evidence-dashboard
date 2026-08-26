from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import Database
from app.operational_summary import build_operational_summary
from app.routes import create_router


def folder(kode: str, status: str, *, scanned_at: str, error: str | None = None) -> dict:
    return {
        "kk_id": "KK3.1",
        "kode": kode,
        "subunsur_name": f"Subunsur {kode}",
        "status": status,
        "status_reason": f"Alasan {status}",
        "file_count": 2,
        "last_scanned_at": scanned_at,
        "error_message": error,
    }


class OperationalSummaryDomainTests(unittest.TestCase):
    def test_summary_uses_existing_metadata_and_orders_recent_documents(self) -> None:
        summary = build_operational_summary(
            [
                folder("1.1", "Kosong", scanned_at="2026-08-25T09:00:00+00:00"),
                folder("1.2", "Terisi Sebagian", scanned_at="2026-08-26T10:00:00+00:00", error="Timeout"),
                folder("1.3", "Terisi Penuh", scanned_at="2026-08-26T11:00:00+00:00"),
            ],
            [
                {"id": 1, "kk_id": "KK3.1", "kode": "1.1", "name": "Grade D/Lama.pdf", "modified_at": "2026-08-20T00:00:00+00:00"},
                {"id": 2, "kk_id": "KK3.1", "kode": "1.3", "name": "Grade C/Baru.pdf", "modified_at": "2026-08-24T00:00:00+00:00"},
            ],
        )

        self.assertEqual(summary["last_checked_at"], "2026-08-26T11:00:00+00:00")
        self.assertEqual(summary["latest_documents"][0]["base_name"], "Baru.pdf")
        self.assertNotIn("href", summary["latest_documents"][0])


class OperationalSummaryApiTests(unittest.TestCase):
    def test_dashboard_adds_operational_summary_without_changing_existing_fields(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = Database(str(Path(temporary_directory) / "operational.db"))
            database.ensure_mapping()
            with database.connect() as connection:
                connection.execute(
                    """
                    UPDATE folders
                    SET status = 'Terisi Sebagian', file_count = 1,
                        last_scanned_at = '2026-08-26T10:00:00+00:00'
                    WHERE kk_id = 'KK3.1' AND kode = '1.1'
                    """
                )
                connection.execute(
                    """
                    INSERT INTO files
                        (kk_id, kode, name, href, is_folder, size_bytes, mime_type, modified_at)
                    VALUES ('KK3.1', '1.1', 'Grade C/Laporan terbaru.pdf', '/dav/file', 0, 123,
                            'application/pdf', '2026-08-25T00:00:00+00:00')
                    """
                )
            application = FastAPI()
            application.include_router(create_router(database))
            with TestClient(application) as client:
                response = client.get("/api/dashboard")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for existing_field in ("total_folders", "total_files", "status_counts", "kk_summary", "folders"):
            self.assertIn(existing_field, payload)
        summary = payload["operational_summary"]
        self.assertEqual(summary["source"], "synchronized_metadata")
        self.assertEqual(summary["latest_documents"][0]["base_name"], "Laporan terbaru.pdf")

    def test_progress_endpoint_returns_many_recent_documents_without_raw_content(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database = Database(str(Path(temporary_directory) / "progress.db"))
            database.ensure_mapping()
            with database.connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO files
                        (kk_id, kode, name, href, is_folder, size_bytes, mime_type, modified_at)
                    VALUES ('KK3.1', '1.1', ?, ?, 0, 123, 'application/pdf', ?)
                    """,
                    [
                        (f"Grade C/Laporan {index}.pdf", f"/dav/{index}", f"2026-08-{index + 1:02d}T00:00:00+00:00")
                        for index in range(12)
                    ],
                )
            application = FastAPI()
            application.include_router(create_router(database))
            with TestClient(application) as client:
                response = client.get("/api/operational-progress", params={"limit": 10})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 10)
        self.assertEqual(payload["limit"], 10)
        self.assertEqual(payload["latest_documents"][0]["base_name"], "Laporan 11.pdf")
        self.assertNotIn("href", payload["latest_documents"][0])
        self.assertNotIn("content", payload["latest_documents"][0])


if __name__ == "__main__":
    unittest.main()
