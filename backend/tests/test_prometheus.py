from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from app.analysis.prometheus import render_prometheus_metrics


class PrometheusMetricsTests(unittest.TestCase):
    def test_metrics_are_numeric_label_safe_and_do_not_expose_document_content(self) -> None:
        payload = render_prometheus_metrics(
            {
                "queue_by_status": {"queued": 3},
                "job_recovery": {
                    "recovered_job_count": 2,
                    "lease_retry_attempt_count": 3,
                    "resume_lineage_job_count": 4,
                    "active_recovery_loop_count": 1,
                },
                "runs_by_status": {"review_required": 2},
                "engines": {"native_parsing:completed": 4},
                "run_count": 2,
                "average_duration_seconds": 12.5,
                "complete_coverage_count": 1,
                "ocr_run_count": 1,
                "ocr_resource": {
                    "attempt_count": 43,
                    "timeout_count": 1,
                    "budget_exhausted_unit_count": 2,
                    "durable_checkpoint_batch_count": 4,
                    "document_elapsed_ms": 90058,
                    "budget_exhaustion_reason_counts": {
                        "document_time_budget_exhausted": 2,
                    },
                },
                "primary_blocked_count": 2,
                "estimated_cost_usd": 0.125,
                "verification": {"total": 3, "rejected": 1},
                "human_review": {"total": 10, "corrected": 2, "rejected": 1},
                "security_findings_by_severity": {"high": 1},
                "controlled_uploads_by_status": {
                    "blocked_ambiguous": 2,
                    "uploading": 1,
                },
                "stale_controlled_upload_reservation_count": 1,
                "resolved_controlled_upload_ambiguity_count": 1,
                "unresolved_controlled_upload_ambiguity_count": 2,
                "compute_routing": {
                    "mapping": {
                        "total": 5,
                        "selected": 2,
                        "average_complexity_score": 0.42,
                        "average_risk_score": 0.51,
                    }
                },
                "legacy_pipeline_calls_by_kind": {"recommendation:legacy_api": 5},
                "retrieval_feedback": {
                    "active": True,
                    "term_count": 7,
                    "source_label_count": 12,
                },
                "evaluation_reports_by_authority": {
                    "informational": 2,
                    "release": 1,
                },
                "document_text": "RAHASIA DOKUMEN TIDAK BOLEH MUNCUL",
            },
            {
                "alerts": [{"code": 'unsafe"label', "severity": "critical"}],
            },
            {
                "worker_count": 2,
                "alive_workers": 2,
                "stopping": True,
                "draining": True,
                "draining_seconds": 625.25,
                "accepting_jobs": False,
                "started": True,
                "queue_backend": "sqlite",
                "queue_adapter": {"adapter_known": True},
                "expected_replicas": 1,
                "multi_instance_supported": False,
                "single_leader_enforced": True,
                "leader_lease_active": True,
            },
            pipeline_version="2.0-test",
            cost_alert_usd_per_hour=2.5,
            storage_encryption_attestation={
                "effective": False,
                "checks": {"validation_flag_enabled": True},
                "reasons": ["signature_valid", "storage_binding_valid"],
                "seconds_until_expiry": 86400,
            },
        )
        self.assertIn('spip_analysis_queue_jobs{status="queued"} 3', payload)
        self.assertIn(
            'spip_analysis_job_recovery_total{event="lease_retry_attempt"} 3',
            payload,
        )
        self.assertIn("spip_analysis_job_recovery_active_loops 1", payload)
        self.assertIn('code="unsafe\\"label"', payload)
        self.assertIn("spip_analysis_run_duration_average_seconds 12.5", payload)
        self.assertIn("spip_analysis_estimated_cost_usd_total 0.125", payload)
        self.assertIn("spip_analysis_cost_budget_usd_per_hour 2.5", payload)
        self.assertIn(
            'spip_analysis_human_reviews_total{outcome="approved"} 7', payload
        )
        self.assertIn(
            'spip_analysis_human_reviews_total{outcome="corrected"} 2', payload
        )
        self.assertIn(
            'spip_analysis_human_reviews_total{outcome="rejected"} 1', payload
        )
        self.assertIn("spip_analysis_human_review_override_ratio 0.3", payload)
        self.assertIn(
            'spip_analysis_controlled_uploads_total{status="blocked_ambiguous"} 2',
            payload,
        )
        self.assertIn("spip_analysis_controlled_upload_reservations 1", payload)
        self.assertIn(
            "spip_analysis_controlled_upload_reservations_stale 1",
            payload,
        )
        self.assertIn(
            'spip_analysis_controlled_upload_ambiguities{resolution="resolved"} 1',
            payload,
        )
        self.assertIn(
            'spip_analysis_controlled_upload_ambiguities{resolution="unresolved"} 2',
            payload,
        )
        self.assertNotIn(
            'spip_analysis_controlled_uploads_total{status="uploading"}',
            payload,
        )
        self.assertIn(
            'spip_analysis_ocr_resource_events_total{event="attempt"} 43', payload
        )
        self.assertIn(
            'spip_analysis_ocr_resource_events_total{event="budget_exhausted"} 2',
            payload,
        )
        self.assertIn(
            'spip_analysis_ocr_resource_events_total{event="checkpoint_batch"} 4',
            payload,
        )
        self.assertIn(
            'spip_analysis_ocr_budget_exhaustions_total{reason="document_time_budget_exhausted"} 2',
            payload,
        )
        self.assertIn("spip_analysis_ocr_document_elapsed_seconds_total 90.058", payload)
        self.assertIn(
            'spip_analysis_worker_leader_lease{enforced="true",queue_backend="sqlite"} 1',
            payload,
        )
        self.assertIn(
            'spip_analysis_multi_instance_ready{adapter_known="true",queue_backend="sqlite"} 0',
            payload,
        )
        self.assertIn('spip_analysis_workers{state="draining"} 1', payload)
        self.assertIn('spip_analysis_workers{state="stopping"} 1', payload)
        self.assertIn('spip_analysis_workers{state="accepting_jobs"} 0', payload)
        self.assertIn("spip_analysis_worker_drain_seconds 625.25", payload)
        self.assertIn(
            'spip_legacy_pipeline_calls_total{source="legacy_api",usage_kind="recommendation"} 5',
            payload,
        )
        self.assertIn(
            'spip_compute_routing_decisions{phase="mapping",selected="true"} 2',
            payload,
        )
        self.assertIn(
            'spip_compute_routing_decisions{phase="mapping",selected="false"} 3',
            payload,
        )
        self.assertIn(
            'spip_compute_routing_average_score{phase="mapping",score="risk"} 0.51',
            payload,
        )
        self.assertIn("spip_retrieval_feedback_registry_active 1", payload)
        self.assertIn("spip_retrieval_feedback_terms 7", payload)
        self.assertIn("spip_retrieval_feedback_source_labels 12", payload)
        self.assertIn(
            'spip_evaluation_reports_total{authority="informational"} 2', payload
        )
        self.assertIn('spip_evaluation_reports_total{authority="release"} 1', payload)
        self.assertIn("spip_storage_encryption_attestation_valid 0", payload)
        self.assertIn("spip_storage_encryption_validation_claimed 1", payload)
        self.assertIn("spip_storage_encryption_attestation_failed_checks 2", payload)
        self.assertIn(
            "spip_storage_encryption_attestation_seconds_until_expiry 86400",
            payload,
        )
        self.assertNotIn("RAHASIA DOKUMEN", payload)
        self.assertTrue(payload.endswith("\n"))

    def test_prometheus_and_alertmanager_profiles_have_expected_safe_defaults(self) -> None:
        root = Path(__file__).resolve().parents[2]
        prometheus = yaml.safe_load((root / "ops/prometheus/prometheus.yml").read_text())
        alerts = yaml.safe_load((root / "ops/prometheus/alerts.yml").read_text())
        alertmanager = yaml.safe_load((root / "ops/alertmanager/alertmanager.yml").read_text())
        scrape = prometheus["scrape_configs"][0]
        self.assertEqual(scrape["metrics_path"], "/api/analysis-runs/metrics/prometheus")
        self.assertEqual(scrape["static_configs"][0]["targets"], ["backend:8000"])
        rules_by_name = {
            rule["alert"]: rule
            for group in alerts["groups"]
            for rule in group["rules"]
        }
        rule_names = set(rules_by_name)
        self.assertIn("SpipAnalysisWorkerDown", rule_names)
        self.assertIn("SpipAnalysisWorkerDrainStuck", rule_names)
        self.assertIn(
            'state="draining"',
            rules_by_name["SpipAnalysisWorkerDown"]["expr"],
        )
        self.assertIn(
            'state="stopping"',
            rules_by_name["SpipAnalysisWorkerDown"]["expr"],
        )
        self.assertIn("SpipAnalysisWorkerLeaderLeaseLost", rule_names)
        self.assertIn(
            "spip_analysis_multi_instance_ready",
            rules_by_name["SpipAnalysisReplicaMisconfiguration"]["expr"],
        )
        self.assertIn("SpipAnalysisJobRecoveryLoop", rule_names)
        self.assertIn("SpipAnalysisParserFailureRate", rule_names)
        self.assertIn("SpipAnalysisCostAnomaly", rule_names)
        self.assertIn(
            "spip_analysis_cost_budget_usd_per_hour",
            rules_by_name["SpipAnalysisCostAnomaly"]["expr"],
        )
        self.assertIn("SpipAnalysisOcrBudgetExhaustion", rule_names)
        self.assertIn("SpipAnalysisOcrTimeoutSpike", rule_names)
        self.assertIn("SpipAnalysisControlledUploadReservationStale", rule_names)
        self.assertIn("SpipAnalysisControlledUploadAmbiguityUnresolved", rule_names)
        self.assertIn("SpipStorageEncryptionAttestationInvalid", rule_names)
        self.assertIn("SpipStorageEncryptionAttestationExpiring", rule_names)
        self.assertEqual(alertmanager["route"]["receiver"], "local-observability")
        self.assertEqual(alertmanager["receivers"], [{"name": "local-observability"}])


if __name__ == "__main__":
    unittest.main()
