from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.package_routes import create_package_router
from app.config import Settings
from app.database import Database


class PackageApiAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.db = Database(str(Path(self.temporary_directory.name) / "package-api.db"))
        self.settings = Settings(
            _env_file=None,
            database_path=str(Path(self.temporary_directory.name) / "package-api.db"),
            analysis_pipeline_v2_enabled=True,
            analysis_require_reviewer_identity=True,
            analysis_require_reviewer_role=True,
        )
        self.settings_patch = patch(
            "app.analysis.package_routes.get_settings",
            return_value=self.settings,
        )
        self.settings_patch.start()
        app = FastAPI()
        app.include_router(create_package_router(self.db))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.settings_patch.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def headers(role: str) -> dict[str, str]:
        return {
            "X-Reviewer-Identity": f"{role}@example.go.id",
            "X-Reviewer-Roles": role,
        }

    def test_package_routes_require_evidence_reviewer_scope(self) -> None:
        payload = {"name": "Paket RBAC", "run_ids": [1, 2]}
        self.assertEqual(
            self.client.post("/api/analysis-packages", json=payload).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/analysis-packages",
                json=payload,
                headers=self.headers("domain_owner"),
            ).status_code,
            403,
        )
        authorized = self.client.post(
            "/api/analysis-packages",
            json=payload,
            headers=self.headers("evidence_reviewer"),
        )
        self.assertEqual(authorized.status_code, 409, authorized.text)

        self.assertEqual(
            self.client.get(
                "/api/analysis-packages/999",
                headers=self.headers("domain_owner"),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/analysis-packages/999",
                headers=self.headers("evidence_reviewer"),
            ).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
