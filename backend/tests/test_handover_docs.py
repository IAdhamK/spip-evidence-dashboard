from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


class HandoverDocumentationTests(unittest.TestCase):
    def test_handover_docs_cover_openapi_and_schema_contracts(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO_ROOT / "backend")
        result = subprocess.run(
            [sys.executable, "scripts/validate_handover_docs.py"],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"])
        self.assertEqual(report["document_count"], 5)
        self.assertGreaterEqual(report["openapi_path_count"], 50)
        self.assertGreaterEqual(report["openapi_operation_count"], 50)
        self.assertEqual(report["authorization_policy_version"], "analysis-rbac-v1")
        self.assertEqual(report["role_secured_operation_count"], 55)
        self.assertEqual(report["proxy_boundary_operation_count"], 6)
        self.assertGreaterEqual(report["schema_table_count"], 40)
        self.assertEqual(report["errors"], [])

    def test_quality_workflow_runs_handover_validator(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/validate_handover_docs.py", workflow)


if __name__ == "__main__":
    unittest.main()
