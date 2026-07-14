from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.run_incident_drill import DRILL_VERSION, run_incident_drill


class IncidentDrillTests(unittest.TestCase):
    def test_provider_outage_rollback_and_restore_drill_passes_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            report = run_incident_drill(Path(directory))

        self.assertEqual(report["drill_version"], DRILL_VERSION)
        self.assertTrue(report["passed"])
        self.assertTrue(report["local_only"])
        self.assertFalse(report["external_ai_used"])
        self.assertRegex(report["report_sha256"], r"^[a-f0-9]{64}$")
        outage = report["checks"]["provider_outage_fail_closed"]
        self.assertTrue(outage["primary_blocked"])
        self.assertGreaterEqual(outage["ocr_required_units"], 1)
        self.assertEqual(
            report["checks"]["rollout_guard"]["effective_stage"],
            "development",
        )
        self.assertTrue(
            report["checks"]["backup_restore"]["critical_table_counts_match"]
        )
        self.assertGreaterEqual(
            report["checks"]["backup_restore"]["payload_reference_count"],
            1,
        )
        self.assertTrue(report["checks"]["backup_restore"]["payload_manifest_match"])
        self.assertEqual(report["checks"]["backup_restore"]["payload_orphan_count"], 0)


if __name__ == "__main__":
    unittest.main()
