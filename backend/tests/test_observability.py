from __future__ import annotations

import unittest

from app.analysis.observability import OperationalAlertEngine


class OperationalAlertTests(unittest.TestCase):
    def test_alerts_on_parser_security_model_and_upload_spikes(self) -> None:
        result = OperationalAlertEngine(cost_alert_usd_per_hour=10.0).evaluate(
            {
                "queue_by_status": {"queued": 21},
                "job_recovery": {"active_recovery_loop_count": 1},
                "engines": {
                    "native_parsing:completed": 97,
                    "native_parsing:failed": 3,
                    "structured_fact_extraction:failed": 2,
                },
                "security_findings_by_severity": {"high": 1},
                "controlled_uploads_by_status": {"blocked_ambiguous": 3},
                "stale_controlled_upload_reservation_count": 1,
                "unresolved_controlled_upload_ambiguity_count": 2,
                "average_duration_seconds": 301,
                "estimated_cost_usd_last_hour": 10.25,
                "human_review": {"override_ratio": 0.375},
            }
        )
        self.assertEqual(result["status"], "critical")
        codes = {item["code"] for item in result["alerts"]}
        self.assertEqual(
            codes,
            {
                "queue_depth", "parser_failure_spike", "security_findings",
                "model_failures", "upload_failure_spike", "latency_anomaly",
                "job_recovery_loop",
                "controlled_upload_reservation_stale",
                "controlled_upload_ambiguity_unresolved",
                "cost_anomaly",
            },
        )
        self.assertEqual(result["derived"]["unresolved_upload_ambiguities"], 2)
        self.assertEqual(result["derived"]["estimated_cost_usd_last_hour"], 10.25)
        self.assertEqual(result["derived"]["cost_alert_usd_per_hour"], 10.0)
        self.assertEqual(result["derived"]["human_review_override_ratio"], 0.375)

    def test_healthy_metrics_have_no_alerts(self) -> None:
        result = OperationalAlertEngine().evaluate(
            {
                "engines": {"native_parsing:completed": 100, "native_parsing:failed": 1},
                "average_duration_seconds": 20,
                "estimated_cost_usd_last_hour": 9.99,
            }
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["alerts"], [])


if __name__ == "__main__":
    unittest.main()
