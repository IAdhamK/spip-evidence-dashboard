from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.routes import create_router


class StaticJobManager:
    def __init__(self, state: dict):
        self.state = state

    def status(self) -> dict:
        return dict(self.state)


class HealthProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "health.db"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_readiness_rejects_stopping_or_draining_worker_but_liveness_stays_up(self) -> None:
        settings = Settings(_env_file=None, analysis_pipeline_v2_enabled=True)
        manager = StaticJobManager({
            "started": True,
            "stopping": False,
            "draining": False,
            "accepting_jobs": True,
            "leader_lease_active": True,
            "queue_backend": "sqlite",
            "blocked_reason": None,
        })
        app = FastAPI()
        app.include_router(create_router(self.db, manager))
        with patch("app.routes.get_settings", return_value=settings), TestClient(app) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            live = client.get("/api/health/live")
            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.json()["status"], "live")
            ready = client.get("/api/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertTrue(ready.json()["worker"]["accepting_jobs"])

            manager.state.update({
                "stopping": True,
                "accepting_jobs": False,
                "blocked_reason": "content-free internal shutdown state",
            })
            stopping = client.get("/api/health/ready")
            self.assertEqual(stopping.status_code, 503)
            self.assertIn("worker_stopping", stopping.json()["reason_codes"])
            self.assertIn("worker_blocked", stopping.json()["reason_codes"])
            self.assertNotIn("content-free internal", stopping.text)

            manager.state.update({
                "started": False,
                "stopping": True,
                "draining": True,
            })
            draining = client.get("/api/health/ready")
            self.assertEqual(draining.status_code, 503)
            self.assertIn("worker_draining", draining.json()["reason_codes"])
            self.assertIn("worker_not_started", draining.json()["reason_codes"])
            self.assertEqual(client.get("/api/health/live").status_code, 200)

    def test_readiness_does_not_require_v2_worker_when_pipeline_is_disabled(self) -> None:
        settings = Settings(_env_file=None, analysis_pipeline_v2_enabled=False)
        app = FastAPI()
        app.include_router(create_router(self.db, None))
        with patch("app.routes.get_settings", return_value=settings), TestClient(app) as client:
            ready = client.get("/api/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json()["ok"])
        self.assertFalse(ready.json()["analysis_pipeline_v2_required"])
        self.assertEqual(ready.json()["reason_codes"], [])


if __name__ == "__main__":
    unittest.main()
