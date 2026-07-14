from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.analysis.jobs import AnalysisJobManager
from app.analysis.package_routes import create_package_router
from app.analysis.reviewer_identity import (
    ALLOWED_REVIEWER_ROLES,
    AUTHORIZATION_POLICY_VERSION,
    PROXY_BOUNDARY_OPERATIONS,
    SECURED_OPERATION_ROLES,
    authorize_api_request,
    authorization_contract_summary,
)
from app.analysis.routes import create_analysis_router
from app.config import Settings
from app.database import Database


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


class AuthorizationContractTests(unittest.TestCase):
    def test_every_v2_openapi_operation_has_exactly_one_access_policy(self) -> None:
        with TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "authorization-contract.db"))
            settings = Settings(
                _env_file=None,
                database_path=str(Path(directory) / "authorization-contract.db"),
                analysis_pipeline_v2_enabled=True,
            )
            manager = AnalysisJobManager(database, settings)
            app = FastAPI()
            app.include_router(create_analysis_router(database, manager))
            app.include_router(create_package_router(database))
            try:
                schema = app.openapi()
            finally:
                manager.stop()

        actual_operations = {
            (method.upper(), path)
            for path, definition in schema["paths"].items()
            for method in definition
            if method in HTTP_METHODS
        }
        secured = set(SECURED_OPERATION_ROLES)
        proxy_boundary = set(PROXY_BOUNDARY_OPERATIONS)
        self.assertFalse(secured & proxy_boundary)
        self.assertEqual(actual_operations, secured | proxy_boundary)
        self.assertEqual(len(actual_operations), 61)
        self.assertTrue(all(method == "GET" for method, _path in proxy_boundary))
        self.assertTrue(
            all(
                (method, path) in secured
                for method, path in actual_operations
                if method in {"POST", "PUT", "PATCH", "DELETE"}
            )
        )

    def test_role_contract_only_uses_known_nonempty_scopes(self) -> None:
        for operation, roles in SECURED_OPERATION_ROLES.items():
            self.assertTrue(roles, operation)
            self.assertLessEqual(roles, ALLOWED_REVIEWER_ROLES, operation)
        self.assertEqual(
            set(PROXY_BOUNDARY_OPERATIONS.values()),
            {"authenticated_proxy", "internal_network"},
        )
        summary = authorization_contract_summary()
        self.assertEqual(summary["policy_version"], AUTHORIZATION_POLICY_VERSION)
        self.assertEqual(summary["secured_operation_count"], 55)
        self.assertEqual(summary["proxy_boundary_operation_count"], 6)
        self.assertEqual(summary["classified_operation_count"], 61)
        self.assertTrue(summary["all_mutations_role_secured"])

    def test_unregistered_guarded_route_fails_closed_even_in_development(self) -> None:
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/api/analysis-runs/unregistered",
            "headers": [],
            "route": SimpleNamespace(path="/api/analysis-runs/unregistered"),
        })
        settings = Settings(
            _env_file=None,
            analysis_require_reviewer_identity=False,
            analysis_require_reviewer_role=False,
        )
        with self.assertRaises(HTTPException) as missing:
            authorize_api_request(request, settings, "")
        self.assertEqual(missing.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
