from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.file_search import search_file_records, search_plan
from app.routes import create_router


def record(name: str, *, kk_id: str = "KK3.2", kode: str = "1.8") -> dict:
    return {
        "id": 1,
        "kk_id": kk_id,
        "kode": kode,
        "name": name,
        "href": f"/public.php/dav/files/share/KK 3.2/1.8 Hubungan Kerja/{name}",
        "size_bytes": 1234,
        "mime_type": "application/pdf",
        "modified_at": "2026-08-25T00:00:00+00:00",
        "kk_title": "KK 3.2 Keandalan Pelaporan Keuangan",
        "subunsur_name": "Hubungan Kerja yang Baik",
        "unsur": "Lingkungan Pengendalian",
        "folder_path": "/KK 3.2/1.8 Hubungan Kerja",
        "last_scanned_at": "2026-08-25T00:00:00+00:00",
    }


class FileSearchDomainTests(unittest.TestCase):
    def test_direct_file_name_search_preserves_source_location(self) -> None:
        result = search_file_records(
            [record("1.8.2 Risiko Kemitraan/Grade C/6. Nota Kesepakatan Token IKPA.pdf")],
            "ikpa",
        )
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertEqual(item["base_name"], "6. Nota Kesepakatan Token IKPA.pdf")
        self.assertEqual(item["detail_kode"], "1.8.2")
        self.assertEqual(item["grade"], "C")
        self.assertEqual(item["file_type"], "pdf")
        self.assertIn("Grade C", item["location_path"])

    def test_administrative_alias_finds_filename_without_ai(self) -> None:
        plan = search_plan("pelaksanaan anggaran")
        self.assertEqual(plan["expanded_terms"], ("ikpa", "rpd", "dipa"))
        result = search_file_records(
            [
                record("1.8.2/Grade C/Nota Kesepakatan IKPA.pdf"),
                record("1.8.2/Grade D/Daftar Hadir Umum.pdf"),
            ],
            "pelaksanaan anggaran",
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["match_type"], "related_keyword")
        self.assertIn("ikpa", result["results"][0]["matched_terms"])

    def test_multiple_words_and_filters_are_fail_narrow(self) -> None:
        records = [
            record("1.8.2/Grade C/Laporan Risiko Semester I.pdf"),
            record("1.8.2/Grade C/Laporan Kinerja.pdf"),
            {**record("1.8.2/Grade D/Laporan Risiko.xlsx"), "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ]
        result = search_file_records(records, "laporan risiko", file_type="pdf", grade="C")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["base_name"], "Laporan Risiko Semester I.pdf")


class FileSearchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.database = Database(str(Path(self.temporary_directory.name) / "search.db"))
        self.database.ensure_mapping()
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO files
                    (kk_id, kode, name, href, is_folder, size_bytes, mime_type, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "KK3.2",
                        "1.8",
                        "1.8.2 Risiko Kemitraan/Grade C/6. Nota Kesepakatan Token IKPA.pdf",
                        "/public.php/dav/files/share/KK 3.2/1.8 Hubungan/1.8.2 Risiko Kemitraan/Grade C/6. Nota Kesepakatan Token IKPA.pdf",
                        0,
                        568868,
                        "application/pdf",
                        "2026-08-25T00:00:00+00:00",
                    ),
                    (
                        "KK3.2",
                        "1.8",
                        "1.8.2 Risiko Kemitraan/Grade C",
                        "/public.php/dav/files/share/KK 3.2/1.8 Hubungan/1.8.2 Risiko Kemitraan/Grade C",
                        1,
                        None,
                        None,
                        "2026-08-25T00:00:00+00:00",
                    ),
                ],
            )
        application = FastAPI()
        application.include_router(create_router(self.database))
        self.client = TestClient(application)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_endpoint_returns_content_minimized_search_result(self) -> None:
        settings = Settings(
            _env_file=None,
            lumbung_share_token="CiJYTHFxZaJ83YF",
        )
        with patch("app.routes.get_settings", return_value=settings):
            response = self.client.get(
                "/api/files/search",
                params={"q": "IKPA", "kk_id": "KK3.2", "grade": "C"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["source"], "synchronized_file_metadata")
        self.assertFalse(payload["content_search"])
        item = payload["results"][0]
        self.assertEqual(item["base_name"], "6. Nota Kesepakatan Token IKPA.pdf")
        self.assertNotIn("href", item)
        self.assertIn("lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF", item["public_url"])
        self.assertIn("Grade%20C", item["public_url"])

    def test_endpoint_validates_short_query(self) -> None:
        response = self.client.get("/api/files/search", params={"q": "a"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
