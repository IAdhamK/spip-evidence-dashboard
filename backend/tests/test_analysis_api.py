from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.jobs import AnalysisJobManager
from app.analysis.governance import synthetic_probe_png
from app.analysis.local_ocr import LocalOCRItem, LocalOCRResponse
from app.analysis.routes import create_analysis_router
from app.analysis.repository import AnalysisRepository
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.domain.grading import build_rule_catalog, compile_parameter_rules, rule_checksum
from app.analysis import RULE_VERSION
from app.config import Settings
from app.database import Database
from app.smart_upload import SmartUploadError


class AnalysisApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "api.db"))
        self.db.ensure_mapping()
        self.db.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=str(Path(self.temp_dir.name) / "api.db"),
            analysis_pipeline_v2_enabled=True,
            analysis_worker_limit=1,
            analysis_cost_alert_usd_per_hour=2.5,
            analysis_structured_model_enabled=False,
            analysis_model_verifier_enabled=False,
        )
        self.manager = AnalysisJobManager(self.db, self.settings)
        self.settings_patch = patch("app.analysis.routes.get_settings", return_value=self.settings)
        self.settings_patch.start()
        app = FastAPI()
        app.include_router(create_analysis_router(self.db, self.manager))
        self.app = app
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.manager.stop()
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_async_analysis_dedupe_readiness_and_upload_gate(self) -> None:
        files = {"file": ("evidence.txt", b"Kebijakan indikator telah ditetapkan dan dievaluasi berkala tahun 2026.", "text/plain")}
        first = self.client.post(
            "/api/analysis-runs",
            files=files,
            data={"analysis_mode": "full_audit"},
        )
        self.assertEqual(first.status_code, 202)
        first_job = first.json()["job"]

        second = self.client.post(
            "/api/analysis-runs",
            files=files,
            data={"analysis_mode": "full_audit"},
        )
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["job"]["id"], first_job["id"])
        self.assertTrue(second.json()["job"]["deduplicated"])

        deadline = time.monotonic() + 5
        snapshot = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/analysis-runs/jobs/{first_job['id']}")
            self.assertEqual(response.status_code, 200)
            snapshot = response.json()
            if snapshot["job"]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertEqual(snapshot["job"]["status"], "completed")

        prometheus = self.client.get("/api/analysis-runs/metrics/prometheus")
        self.assertEqual(prometheus.status_code, 200)
        self.assertTrue(prometheus.headers["content-type"].startswith("text/plain"))
        self.assertIn("spip_analysis_queue_jobs", prometheus.text)
        self.assertIn("spip_analysis_workers", prometheus.text)

        self.assertIn("spip_analysis_job_recovery_total", prometheus.text)
        self.assertIn("spip_storage_encryption_attestation_valid 0", prometheus.text)
        self.assertIn("spip_compute_routing_decisions", prometheus.text)
        self.assertIn("spip_analysis_cost_budget_usd_per_hour 2.5", prometheus.text)
        self.assertIn("spip_analysis_human_review_override_ratio 0", prometheus.text)

        metrics_response = self.client.get("/api/analysis-runs/metrics")
        self.assertEqual(metrics_response.status_code, 200)
        self.assertEqual(
            metrics_response.json()["alerting"]["derived"]["cost_alert_usd_per_hour"],
            2.5,
        )
        self.assertIn(
            "estimated_cost_usd_last_hour", metrics_response.json()["metrics"]
        )

        config = self.client.get("/api/analysis-runs/config")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(
            config.json()["compute_routing"]["policy_version"],
            "compute-routing-v1",
        )
        self.assertEqual(
            config.json()["compute_routing"]["mapping_model_authority"],
            "demotion_only",
        )
        self.assertEqual(
            config.json()["compute_routing"]["grade_authority"],
            "domain_rule_only",
        )
        self.assertEqual(
            config.json()["checkpointing"]["policy_version"],
            "unit-checkpoint-v2",
        )
        self.assertTrue(
            config.json()["checkpointing"]["visual_ocr_batch_durable"]
        )
        queue_adapter = config.json()["job_manager"]["queue_adapter"]
        self.assertEqual(queue_adapter["name"], "sqlite")
        self.assertTrue(queue_adapter["adapter_known"])
        self.assertEqual(
            queue_adapter["canonical_persistence"]["backend_name"], "sqlite"
        )
        self.assertFalse(queue_adapter["multi_instance_supported"])
        authorization_contract = config.json()["authorization_contract"]
        self.assertEqual(
            authorization_contract["policy_version"],
            "analysis-rbac-v1",
        )
        self.assertEqual(authorization_contract["classified_operation_count"], 62)
        self.assertTrue(authorization_contract["all_mutations_role_secured"])

        readiness = self.client.get("/api/analysis-runs/readiness-dashboard")
        self.assertEqual(readiness.status_code, 200)
        self.assertEqual(
            readiness.json()["checkpointing"]["policy_version"],
            "unit-checkpoint-v2",
        )
        self.assertTrue(
            readiness.json()["checkpointing"]["partial_resume_checksum_bound"]
        )
        self.assertEqual(
            readiness.json()["alerting"]["derived"]["cost_alert_usd_per_hour"],
            2.5,
        )
        deployment = readiness.json()["deployment"]
        self.assertEqual(deployment["prometheus_path"], "/api/analysis-runs/metrics/prometheus")
        self.assertEqual(deployment["worker"]["queue_backend"], "sqlite")
        self.assertFalse(deployment["multi_instance_ready"])
        self.assertEqual(
            deployment["worker"]["queue_adapter"]["mode"], "sqlite_singleton"
        )
        self.assertFalse(deployment["reviewer_identity"]["required"])
        self.assertFalse(deployment["reviewer_identity"]["role_required"])
        self.assertEqual(
            deployment["reviewer_identity"]["authorization_mode"],
            "development_payload_identity",
        )
        self.assertTrue(
            deployment["reviewer_identity"]["direct_backend_access_must_be_blocked"]
        )
        self.assertEqual(
            deployment["authorization_contract"]["policy_version"],
            "analysis-rbac-v1",
        )
        self.assertFalse(deployment["alertmanager"]["default_delivery_enabled"])
        self.assertTrue(deployment["alertmanager"]["secret_file_required"])
        self.assertEqual(
            deployment["payload_storage"]["configured_backend"],
            "database",
        )
        self.assertFalse(deployment["payload_storage"]["filesystem_configured"])
        self.assertFalse(
            deployment["payload_storage"]["platform_encryption_validated"]
        )
        self.assertFalse(
            deployment["payload_storage"]["encryption_attestation"]["effective"]
        )
        self.assertIn(
            "validation_flag_enabled",
            deployment["payload_storage"]["encryption_attestation"]["reasons"],
        )
        self.assertTrue(deployment["payload_storage"]["encrypted_volume_required"])
        office_renderer = readiness.json()["office_renderer"]
        self.assertEqual(office_renderer["supported_formats"], ["docx", "xlsx", "pptx"])
        self.assertEqual(office_renderer["max_pages_per_full_audit"], 24)
        self.assertEqual(
            readiness.json()["office_slide_renderer"],
            office_renderer,
        )
        self.assertEqual(readiness.json()["ocr"]["max_image_pixels"], 16_000_000)
        self.assertEqual(readiness.json()["ocr"]["max_tiles"], 16)
        self.assertEqual(readiness.json()["ocr"]["unit_budget_seconds"], 180)
        self.assertEqual(readiness.json()["ocr"]["document_budget_seconds"], 900)
        self.assertEqual(readiness.json()["ocr"]["max_attempts_per_unit"], 24)
        self.assertEqual(readiness.json()["ocr"]["render_batch_units"], 4)
        run_id = snapshot["job"]["run_id"]
        self.assertEqual(snapshot["result"]["run"]["coverage_status"], "complete")
        self.assertTrue(snapshot["result"]["run"]["primary_blocked"])
        administrative_detail = self.client.get(f"/api/analysis-runs/{run_id}")
        self.assertEqual(administrative_detail.status_code, 200)
        self.assertTrue(administrative_detail.json()["mappings"])
        self.assertTrue(
            administrative_detail.json()["mappings"][0]["parameter_uraian"]
        )
        self.assertIn(
            "cara_pengujian",
            administrative_detail.json()["mappings"][0],
        )
        administrative_mapping = administrative_detail.json()["mappings"][0]
        self.assertTrue(administrative_mapping["kk_title"])
        self.assertTrue(administrative_mapping["unsur"])
        self.assertTrue(administrative_mapping["subunsur_name"])
        self.assertTrue(administrative_mapping["available_grades"])
        self.assertIn(
            administrative_mapping["document_role"],
            {"primary", "supporting", "context"},
        )

        rules = self.client.get("/api/analysis-runs/rule-catalog?limit=2")
        self.assertEqual(rules.status_code, 200)
        self.assertEqual(len(rules.json()["rules"]), 2)
        self.assertEqual(rules.json()["rule_count"], 920)

        parameter_catalog = self.client.get(
            "/api/analysis-runs/parameter-catalog?limit=1000"
        )
        self.assertEqual(parameter_catalog.status_code, 200)
        self.assertEqual(parameter_catalog.json()["parameter_count"], 184)
        self.assertEqual(len(parameter_catalog.json()["items"]), 184)
        self.assertEqual(
            {item["kk_id"] for item in parameter_catalog.json()["items"]},
            {"KK3.1", "KK3.2", "KK3.3", "KK3.4"},
        )
        self.assertTrue(all(
            item["kk_title"]
            and item["subunsur_name"]
            and item["uraian"]
            and item["available_grades"]
            for item in parameter_catalog.json()["items"]
        ))

        readiness = self.client.get("/api/analysis-runs/promotion-readiness")
        self.assertEqual(readiness.status_code, 200)
        self.assertFalse(readiness.json()["shadow"]["ready"])
        self.assertFalse(readiness.json()["storage_ready"])

        # A boolean configuration claim without signed evidence must never open
        # the canary storage gate.
        self.settings.analysis_payload_storage_encryption_validated = True
        flag_only_readiness = self.client.get("/api/analysis-runs/promotion-readiness")
        self.assertEqual(flag_only_readiness.status_code, 200)
        self.assertFalse(flag_only_readiness.json()["storage_ready"])
        self.assertIn(
            "Enkripsi volume payload/database produksi belum divalidasi.",
            flag_only_readiness.json()["canary"]["reasons"],
        )

        dashboard = self.client.get("/api/analysis-runs/readiness-dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["rollout"]["effective_stage"], "development")
        self.assertFalse(dashboard.json()["vision"]["effective"])
        self.assertTrue(dashboard.json()["temporary_mitigations"])
        canary_mitigation = next(
            item for item in dashboard.json()["temporary_mitigations"]
            if item["gate"] == "canary"
        )
        self.assertEqual(canary_mitigation["status"], "blocked")
        upload_mitigation = next(
            item for item in dashboard.json()["temporary_mitigations"]
            if item["gate"] == "controlled_upload_reservation"
        )
        self.assertEqual(upload_mitigation["status"], "ready")
        self.assertEqual(
            dashboard.json()["metrics"]["stale_controlled_upload_reservation_count"],
            0,
        )

        expansion = self.client.post(
            f"/api/analysis-runs/{run_id}/expand-candidates",
            json={"limit": 20},
        )
        self.assertEqual(expansion.status_code, 200, expansion.text)
        self.assertEqual(expansion.json()["candidate_expansion"]["requested_limit"], 20)
        self.assertTrue(expansion.json()["run"]["primary_blocked"])
        expanded_ranks = [
            item["rag_rank"] for item in expansion.json()["mappings"]
        ]
        self.assertEqual(expanded_ranks, list(range(1, len(expanded_ranks) + 1)))

        retry = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertEqual(retry.json()["resumes_from_run_id"], run_id)
        self.assertNotEqual(retry.json()["job"]["id"], first_job["id"])
        retry_job_id = retry.json()["job"]["id"]
        retry_snapshot = None
        retry_deadline = time.monotonic() + 5
        while time.monotonic() < retry_deadline:
            retry_response = self.client.get(f"/api/analysis-runs/jobs/{retry_job_id}")
            self.assertEqual(retry_response.status_code, 200)
            retry_snapshot = retry_response.json()
            if retry_snapshot["job"]["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        self.assertEqual(retry_snapshot["job"]["status"], "completed")
        self.assertEqual(retry_snapshot["result"]["run"]["resumed_from_run_id"], run_id)
        self.assertTrue(
            any(
                event["event_type"] == "unit_checkpoints_reused"
                for event in retry_snapshot["result"]["events"]
            )
        )
        checkpoint_response = self.client.get(
            f"/api/analysis-runs/{retry_snapshot['result']['run']['id']}/checkpoints"
        )
        self.assertEqual(checkpoint_response.status_code, 200)
        self.assertIn("unit_preparation", checkpoint_response.json()["summary"])
        self.assertIn("fact_extraction", checkpoint_response.json()["summary"])

        mapping_id = snapshot["result"]["mappings"][0]["id"]
        upload = self.client.post(
            f"/api/analysis-runs/{run_id}/controlled-upload",
            json={"mapping_candidate_id": mapping_id, "reviewer_id": "api-reviewer"},
        )
        self.assertEqual(upload.status_code, 409)
        self.assertIn("grade rule belum disahkan", upload.json()["detail"])

    def test_run_cancel_endpoint_closes_requeued_run_and_is_idempotent(self) -> None:
        repository = AnalysisRepository(self.db, settings=self.settings)
        payload = b"run cancellation compatibility endpoint"
        job = repository.enqueue_job(
            file_name="cancel-by-run.txt",
            content_type="text/plain",
            payload=payload,
            analysis_mode="full_audit",
        )
        claimed = repository.claim_next_job(lease_minutes=5)
        self.assertEqual(claimed["id"], job["id"])
        document = repository.upsert_document(
            file_name="cancel-by-run.txt",
            content_type="text/plain",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
            ttl_hours=24,
        )
        run_id = repository.create_run(
            document_id=int(document["id"]),
            analysis_mode="full_audit",
            pipeline_version="2.0-test",
            parser_version="parser-test",
            rule_version=RULE_VERSION,
            prompt_version="prompt-test",
            provider=None,
            model=None,
            configuration_hash="cancel-api-test",
        )
        self.assertTrue(repository.attach_job_run(
            job["id"],
            run_id,
            expected_attempt=int(claimed["attempt_count"]),
        ))
        self.assertTrue(repository.requeue_job_after_worker_shutdown(
            job["id"],
            expected_attempt=int(claimed["attempt_count"]),
        ))

        cancelled = self.client.post(f"/api/analysis-runs/{run_id}/cancel")
        self.assertEqual(cancelled.status_code, 202)
        self.assertEqual(cancelled.json()["job"]["status"], "cancelled")
        self.assertEqual(cancelled.json()["run"]["status"], "cancelled")
        self.assertFalse(cancelled.json()["idempotent"])

        repeated = self.client.post(f"/api/analysis-runs/{run_id}/cancel")
        self.assertEqual(repeated.status_code, 202)
        self.assertTrue(repeated.json()["idempotent"])
        self.assertEqual(repeated.json()["run"]["status"], "cancelled")

    def test_corrupt_retained_payload_blocks_retry_without_fallback(self) -> None:
        payload = b"Kebijakan integrity payload telah ditetapkan tahun 2026."
        response = self.client.post(
            "/api/analysis-runs",
            files={"file": ("integrity.txt", payload, "text/plain")},
            data={"analysis_mode": "full_audit", "force": "true"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job"]["id"]
        deadline = time.monotonic() + 5
        snapshot = None
        while time.monotonic() < deadline:
            current = self.client.get(f"/api/analysis-runs/jobs/{job_id}")
            self.assertEqual(current.status_code, 200, current.text)
            snapshot = current.json()
            if snapshot["job"]["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        self.assertEqual(snapshot["job"]["status"], "completed")
        run_id = int(snapshot["job"]["run_id"])
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE documents SET pending_bytes = ?
                WHERE id = (SELECT document_id FROM analysis_runs WHERE id = ?)
                """,
                (b"x" * len(payload), run_id),
            )
        retry = self.client.post(f"/api/analysis-runs/{run_id}/retry")
        self.assertEqual(retry.status_code, 409, retry.text)
        self.assertIn("gagal diverifikasi", retry.json()["detail"])

    def test_visual_review_creates_checksum_bound_derived_run(self) -> None:
        class LocalVisualProvider:
            name = "test_visual_review"

            def analyze_images(self, images):
                return LocalOCRResponse(items=[
                    LocalOCRItem(
                        unit_key=image["unit_key"],
                        text="Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                        confidence=0.96,
                        regions=[{
                            "text": "Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                            "confidence": 0.96,
                            "bbox": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.1},
                            "coordinate_space": "normalized_top_left",
                        }],
                        method="local_visual_review_test_v1",
                        languages=["ind"],
                    )
                    for image in images
                ])

        image_payload = synthetic_probe_png()
        provider = LocalVisualProvider()
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=provider,
        ):
            source = AnalysisOrchestrator(self.db, self.settings).start(
                file_name="restricted.png",
                content_type="image/png",
                payload=image_payload,
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            source_run_id = int(source["run"]["id"])
            self.assertEqual(source["run"]["coverage_status"], "partial")
            self.assertEqual(
                source["document_units"][0]["metadata"]["visual_semantics_status"],
                "pending_review_or_vision",
            )
            checkpoint_summary = AnalysisRepository(self.db).checkpoint_summary(
                source_run_id
            )
            self.assertEqual(
                checkpoint_summary["visual_ocr_batch"]["completed"], 1
            )
            self.assertGreaterEqual(
                checkpoint_summary["visual_ocr_manifest"]["completed"], 1
            )
            self.assertTrue(any(
                event["event_type"] == "visual_ocr_batch_checkpoint"
                for event in source["events"]
            ))

            queue = self.client.get("/api/analysis-runs/visual-review/queue")
            self.assertEqual(queue.status_code, 200, queue.text)
            item = next(
                row for row in queue.json()["items"]
                if int(row["run_id"]) == source_run_id
            )
            self.assertEqual(item["review_state"], "pending")
            unit_key = item["unit_key"]

            preview = self.client.get(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}/preview"
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.content, image_payload)
            self.assertEqual(preview.headers["cache-control"], "private, no-store")

            invalid_region = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}/decision",
                json={
                    "reviewer_id": "domain-visual-reviewer",
                    "review_kind": "visual_semantics",
                    "decision": "confirmed",
                    "unit_text_sha256": item["text_sha256"],
                    "source_image_sha256": item["metadata"]["ocr_source_image_sha256"],
                    "ocr_candidate_text_sha256": None,
                    "semantic_description": "Gambar menampilkan kebijakan dan evaluasi SPIP.",
                    "semantic_regions": [{
                        "region_type": "stamp",
                        "label": "Stempel pengesahan",
                        "bbox": {"x": 0.9, "y": 0.8, "width": 0.2, "height": 0.1},
                    }],
                    "reason": "Region melewati gambar harus ditolak server.",
                    "expected_latest_decision_id": None,
                    "attested": True,
                },
            )
            self.assertEqual(invalid_region.status_code, 422, invalid_region.text)

            decision = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}/decision",
                json={
                    "reviewer_id": "domain-visual-reviewer",
                    "review_kind": "visual_semantics",
                    "decision": "confirmed",
                    "unit_text_sha256": item["text_sha256"],
                    "source_image_sha256": item["metadata"]["ocr_source_image_sha256"],
                    "ocr_candidate_text_sha256": None,
                    "semantic_description": "Gambar menampilkan kebijakan dan evaluasi SPIP.",
                    "semantic_regions": [{
                        "region_type": "stamp",
                        "label": "Stempel pengesahan",
                        "bbox": {"x": 0.7, "y": 0.75, "width": 0.2, "height": 0.15},
                    }],
                    "reason": "Gambar asli dan teks OCR telah diperiksa langsung.",
                    "expected_latest_decision_id": None,
                    "attested": True,
                },
            )
            self.assertEqual(decision.status_code, 201, decision.text)
            decision_body = decision.json()
            checksum = decision_body["visual_review_snapshot"]["checksum"]
            self.assertEqual(len(checksum), 64)

            stale = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}/decision",
                json={
                    "reviewer_id": "domain-visual-reviewer",
                    "review_kind": "visual_semantics",
                    "decision": "confirmed",
                    "unit_text_sha256": item["text_sha256"],
                    "source_image_sha256": item["metadata"]["ocr_source_image_sha256"],
                    "ocr_candidate_text_sha256": None,
                    "semantic_description": "Gambar menampilkan kebijakan dan evaluasi SPIP.",
                    "reason": "Request stale tidak boleh menimpa keputusan aktif.",
                    "expected_latest_decision_id": None,
                    "attested": True,
                },
            )
            self.assertEqual(stale.status_code, 409, stale.text)

            apply_response = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/apply",
                json={
                    "reviewer_id": "domain-visual-reviewer",
                    "visual_review_checksum": checksum,
                    "reason": "Terapkan keputusan visual pada run turunan yang dapat diaudit.",
                    "attested": True,
                },
            )
            self.assertEqual(apply_response.status_code, 202, apply_response.text)
            job_id = apply_response.json()["job"]["id"]
            deadline = time.monotonic() + 5
            derived = None
            while time.monotonic() < deadline:
                response = self.client.get(f"/api/analysis-runs/jobs/{job_id}")
                self.assertEqual(response.status_code, 200, response.text)
                derived = response.json()
                if derived["job"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.02)

        self.assertEqual(derived["job"]["status"], "completed")
        derived_result = derived["result"]
        self.assertEqual(derived_result["run"]["resumed_from_run_id"], source_run_id)
        self.assertEqual(derived_result["run"]["visual_review_checksum"], checksum)
        self.assertEqual(derived_result["run"]["coverage_status"], "complete")
        self.assertEqual(
            derived_result["document_units"][0]["metadata"]["visual_semantics_status"],
            "human_verified",
        )
        self.assertTrue(derived_result["facts"])
        self.assertEqual(
            derived_result["facts"][0]["extraction_method"],
            "human_visual_review_sentence_v1",
        )
        self.assertIn(
            "visual_review",
            derived_result["facts"][0]["sources"][0]["source_location"],
        )
        semantic_regions = derived_result["facts"][0]["sources"][0]["source_location"]["semantic_regions"]
        self.assertEqual(semantic_regions[0]["semantic_hint"], "stamp")
        self.assertEqual(semantic_regions[0]["detection_method"], "human_visual_region_v1")
        source_after = AnalysisRepository(self.db).get_document_unit(
            source_run_id,
            unit_key,
        )
        self.assertEqual(source_after["status"], "partial")

    def test_low_confidence_ocr_can_be_rescued_without_lowering_threshold(self) -> None:
        class LowConfidenceProvider:
            name = "test_ocr_rescue"

            def analyze_images(self, images):
                return LocalOCRResponse(items=[
                    LocalOCRItem(
                        unit_key=image["unit_key"],
                        text="Kebijakan SPIP ditetapkan tahun 2026.",
                        confidence=0.40,
                        regions=[],
                        method="local_low_confidence_test_v1",
                        languages=["ind"],
                    )
                    for image in images
                ])

        image_payload = synthetic_probe_png() + b"ocr-rescue-unique"
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=LowConfidenceProvider(),
        ):
            source = AnalysisOrchestrator(self.db, self.settings).start(
                file_name="ocr-rescue.png",
                content_type="image/png",
                payload=image_payload,
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            source_run_id = int(source["run"]["id"])
            source_unit = source["document_units"][0]
            self.assertEqual(source_unit["status"], "ocr_required")
            self.assertEqual(source["run"]["ocr_required_units"], 1)
            self.assertEqual(
                source_unit["metadata"]["ocr_review_candidate_confidence"],
                0.40,
            )

            queue = self.client.get("/api/analysis-runs/visual-review/queue")
            self.assertEqual(queue.status_code, 200, queue.text)
            item = next(
                row for row in queue.json()["items"]
                if int(row["run_id"]) == source_run_id
            )
            self.assertEqual(item["review_kind"], "ocr_rescue")
            self.assertEqual(queue.json()["kind_counts"]["ocr_rescue"], 1)
            unit_key = item["unit_key"]

            detail_response = self.client.get(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}"
            )
            self.assertEqual(detail_response.status_code, 200, detail_response.text)
            detail = detail_response.json()
            self.assertEqual(detail["review_kind"], "ocr_rescue")
            self.assertEqual(len(detail["review_binding"]["unit_text_sha256"]), 64)
            self.assertEqual(
                detail["review_binding"]["unit_text_sha256"],
                hashlib.sha256(b"").hexdigest(),
            )

            reviewed_text = "Kebijakan SPIP telah ditetapkan pada tahun 2026."
            decision = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/{unit_key}/decision",
                json={
                    "reviewer_id": "ocr-human-reviewer",
                    "review_kind": "ocr_rescue",
                    "decision": "corrected",
                    **detail["review_binding"],
                    "reviewed_text": reviewed_text,
                    "semantic_description": "Gambar memuat penetapan kebijakan SPIP tahun 2026.",
                    "reason": "Kandidat OCR dibandingkan dan ditranskripsi dari gambar sumber.",
                    "expected_latest_decision_id": None,
                    "attested": True,
                },
            )
            self.assertEqual(decision.status_code, 201, decision.text)
            checksum = decision.json()["visual_review_snapshot"]["checksum"]
            apply_response = self.client.post(
                f"/api/analysis-runs/visual-review/{source_run_id}/apply",
                json={
                    "reviewer_id": "ocr-human-reviewer",
                    "visual_review_checksum": checksum,
                    "reason": "Terapkan transkripsi manusia melalui run turunan.",
                    "attested": True,
                },
            )
            self.assertEqual(apply_response.status_code, 202, apply_response.text)
            job_id = apply_response.json()["job"]["id"]
            deadline = time.monotonic() + 5
            derived = None
            while time.monotonic() < deadline:
                response = self.client.get(f"/api/analysis-runs/jobs/{job_id}")
                self.assertEqual(response.status_code, 200, response.text)
                derived = response.json()
                if derived["job"]["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.02)

        self.assertEqual(derived["job"]["status"], "completed")
        result = derived["result"]
        self.assertEqual(result["run"]["coverage_status"], "complete")
        self.assertEqual(result["run"]["ocr_required_units"], 0)
        derived_unit = AnalysisRepository(self.db).get_document_unit(
            int(result["run"]["id"]),
            unit_key,
        )
        self.assertEqual(derived_unit["text"], reviewed_text)
        self.assertEqual(
            result["facts"][0]["extraction_method"],
            "human_ocr_rescue_transcription_v1",
        )
        self.assertIn(
            "ocr_rescue",
            result["facts"][0]["sources"][0]["source_location"],
        )
        source_after = AnalysisRepository(self.db).get_document_unit(
            source_run_id,
            unit_key,
        )
        self.assertEqual(source_after["status"], "ocr_required")

    def test_no_text_ocr_is_available_for_manual_transcription_but_not_confirmation(self) -> None:
        class NoTextProvider:
            name = "test_no_text"

            def analyze_images(self, images):
                return LocalOCRResponse(
                    items=[],
                    warnings=[f"Tidak ada teks pada {image['unit_key']}." for image in images],
                )

        image_payload = synthetic_probe_png() + b"manual-transcription-unique"
        with patch(
            "app.analysis.orchestrator.configured_local_ocr_provider",
            return_value=NoTextProvider(),
        ):
            source = AnalysisOrchestrator(self.db, self.settings).start(
                file_name="manual-transcription.png",
                content_type="image/png",
                payload=image_payload,
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
        source_run_id = int(source["run"]["id"])
        source_unit = source["document_units"][0]
        self.assertEqual(source_unit["status"], "ocr_required")
        self.assertTrue(source_unit["metadata"]["ocr_manual_review_required"])

        queue = self.client.get("/api/analysis-runs/visual-review/queue")
        item = next(
            row for row in queue.json()["items"]
            if int(row["run_id"]) == source_run_id
        )
        self.assertEqual(item["review_kind"], "ocr_rescue")
        detail_response = self.client.get(
            f"/api/analysis-runs/visual-review/{source_run_id}/{item['unit_key']}"
        )
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail = detail_response.json()
        self.assertIsNone(detail["review_binding"]["ocr_candidate_text_sha256"])
        common = {
            "reviewer_id": "manual-ocr-reviewer",
            "review_kind": "ocr_rescue",
            **detail["review_binding"],
            "semantic_description": "Gambar diperiksa untuk transkripsi manual.",
            "reason": "OCR tidak menghasilkan teks dan gambar diperiksa langsung.",
            "expected_latest_decision_id": None,
            "attested": True,
        }
        confirmed = self.client.post(
            f"/api/analysis-runs/visual-review/{source_run_id}/{item['unit_key']}/decision",
            json={**common, "decision": "confirmed"},
        )
        self.assertEqual(confirmed.status_code, 422, confirmed.text)
        corrected = self.client.post(
            f"/api/analysis-runs/visual-review/{source_run_id}/{item['unit_key']}/decision",
            json={
                **common,
                "decision": "corrected",
                "reviewed_text": "Register risiko telah ditetapkan tahun 2026.",
            },
        )
        self.assertEqual(corrected.status_code, 201, corrected.text)

    def test_all_parameter_grade_rules_compile_to_governed_contract(self) -> None:
        repository = AnalysisRepository(self.db)
        catalog = build_rule_catalog(repository.parameter_index(), [])
        self.assertEqual(len(catalog), 920)
        required_fields = {
            "required_stages",
            "required_source_types",
            "period_policy",
            "organization_policy",
            "prerequisite_grade",
            "disqualifiers",
            "effective_date",
            "criterion",
            "source",
        }
        for item in catalog:
            rule = item["rule_definition"]
            self.assertTrue(required_fields <= set(rule), item)
            self.assertEqual(item["rule_checksum"], rule_checksum(rule))
            self.assertEqual(rule["period_policy"], "required_single")
            self.assertEqual(rule["organization_policy"], "required_single")
            self.assertIn("template_only", rule["disqualifiers"])
            self.assertIn("plan_without_result", rule["disqualifiers"])
            self.assertTrue(rule["required_source_types"])

    def test_guided_review_creates_expert_candidate_without_unlocking_upload(self) -> None:
        result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="guided-review.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan indikator kinerja Ditjen PDP telah ditetapkan tahun 2026. "
                b"Evaluasi indikator telah dilaksanakan secara berkala tahun 2026."
            ),
            analysis_mode="full_audit",
        )
        run_id = result["run"]["id"]
        queue = self.client.get("/api/analysis-runs/guided-review/queue")
        self.assertEqual(queue.status_code, 200, queue.text)
        item = next(item for item in queue.json()["items"] if item["id"] == run_id)
        self.assertEqual(item["review_state"], "pending")

        detail = self.client.get(f"/api/analysis-runs/guided-review/{run_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        mapping = payload["mappings"][0]
        self.assertTrue(mapping["parameter_uraian"])
        source_fact_id = mapping["supporting_fact_ids"][0]
        preview = self.client.get(f"/api/analysis-runs/guided-review/{run_id}/document")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn(b"Kebijakan indikator", preview.content)
        self.assertEqual(preview.headers["cache-control"], "private, no-store")

        missing_role = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "domain-reviewer",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": mapping["id"],
                "selected_source_fact_ids": [source_fact_id],
                "expected_mapping": {},
                "expected_template_status": "substantive",
                "reason": "Sumber diperiksa tetapi peran evidence belum dipilih.",
            },
        )
        self.assertEqual(missing_role.status_code, 422, missing_role.text)

        missing_template_label = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "domain-reviewer",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": mapping["id"],
                "selected_source_fact_ids": [source_fact_id],
                "expected_mapping": {},
                "expected_evidence_role": "primary",
                "reason": "Peran evidence diperiksa tetapi status template belum dipilih.",
            },
        )
        self.assertEqual(missing_template_label.status_code, 422, missing_template_label.text)

        saved = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "domain-reviewer",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": mapping["id"],
                "selected_source_fact_ids": [source_fact_id],
                "expected_mapping": {},
                "expected_evidence_role": "primary",
                "expected_template_status": "substantive",
                "reason": "Saran parameter dan lokasi sumber sudah diperiksa.",
            },
        )
        self.assertEqual(saved.status_code, 201, saved.text)
        body = saved.json()
        self.assertEqual(body["label"]["dataset_status"], "expert_candidate")
        self.assertEqual(body["label"]["outcome"], "confirmed")
        self.assertEqual(body["label"]["expected_mappings"][0]["evidence_role"], "primary")
        self.assertEqual(body["label"]["expected_template_status"], "substantive")
        self.assertTrue(body["label"]["expected_source_locations"])
        self.assertTrue(body["primary_upload_unchanged"])
        self.assertTrue(body["run"]["primary_blocked"])

        completed = self.client.get(
            "/api/analysis-runs/guided-review/queue?review_status=completed"
        )
        self.assertEqual(completed.status_code, 200)
        self.assertTrue(any(item["id"] == run_id for item in completed.json()["items"]))
        exported = self.client.get("/api/analysis-runs/guided-review/export")
        self.assertEqual(exported.status_code, 200, exported.text)
        exported_items = [json.loads(line) for line in exported.text.splitlines() if line.strip()]
        exported_item = next(item for item in exported_items if item["sha256"] == result["run"]["sha256"])
        self.assertEqual(exported_item["dataset_status"], "expert_candidate")
        self.assertTrue(exported_item["expected_mappings"])
        self.assertTrue(exported_item["expected_source_locations"])

        with self.assertRaisesRegex(ValueError, "Status template expert review"):
            AnalysisRepository(self.db).save_expert_review_label(
                run_id,
                {
                    "reviewer_id": "domain-reviewer",
                    "outcome": "not_evidence",
                    "reason": "Nilai status template internal harus tetap tervalidasi.",
                    "expected_template_status": "arbitrary",
                },
            )

        revised = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "domain-reviewer",
                "outcome": "unsure",
                "selected_source_fact_ids": [],
                "expected_mapping": {},
                "reason": "Perlu pemeriksaan domain owner sebelum dipastikan.",
            },
        )
        self.assertEqual(revised.status_code, 201, revised.text)
        history = self.client.get(f"/api/analysis-runs/guided-review/{run_id}").json()["label_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(sum(bool(item["is_active"]) for item in history), 1)
        self.assertEqual(history[-1]["dataset_status"], "pilot_unlabelled")

        self.settings.analysis_require_reviewer_identity = True
        unauthenticated = self.client.get("/api/analysis-runs/guided-review/queue")
        self.assertEqual(unauthenticated.status_code, 401)
        authenticated = self.client.get(
            "/api/analysis-runs/guided-review/queue",
            headers={"X-Reviewer-Identity": "domain-reviewer"},
        )
        self.assertEqual(authenticated.status_code, 200, authenticated.text)

    def test_trusted_role_rbac_separates_review_governance_and_release_scopes(self) -> None:
        self.settings.analysis_require_reviewer_identity = True
        self.settings.analysis_require_reviewer_role = True

        def headers(identity: str, roles: str) -> dict[str, str]:
            return {
                "X-Reviewer-Identity": identity,
                "X-Reviewer-Roles": roles,
            }

        evidence = headers("evidence@example.go.id", "evidence_reviewer")
        domain = headers("domain@example.go.id", "domain_owner")
        evaluation = headers("evaluation@example.go.id", "evaluation_owner")
        vision = headers("vision@example.go.id", "vision_owner")
        release = headers("release@example.go.id", "release_owner")
        operations = headers("operations@example.go.id", "operations_owner")
        admin = headers("admin@example.go.id", "analysis_admin")

        self.assertEqual(
            self.client.get("/api/analysis-runs/guided-review/queue").status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/analysis-runs/guided-review/queue",
                headers={"X-Reviewer-Identity": "evidence@example.go.id"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/guided-review/queue", headers=domain).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/guided-review/queue", headers=evidence).status_code,
            200,
        )

        self.assertEqual(
            self.client.get("/api/analysis-runs/governance/rules", headers=evidence).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/governance/rules", headers=domain).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/analysis-runs/governance/expert-dataset", headers=evaluation
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/governance/vision", headers=vision).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/evaluation-reports", headers=release).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/evaluation-reports", headers=evaluation).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/release-evidence", headers=evaluation).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/release-evidence", headers=release).status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/analysis-runs/release-evidence", headers=admin).status_code,
            200,
        )

        created = self.client.post(
            "/api/analysis-runs",
            headers=evidence,
            files={"file": ("rbac.txt", b"Kebijakan tahun 2026.", "text/plain")},
            data={"analysis_mode": "full_audit"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        job_id = created.json()["job"]["id"]
        self.assertEqual(
            self.client.get(f"/api/analysis-runs/jobs/{job_id}", headers=domain).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(f"/api/analysis-runs/jobs/{job_id}", headers=operations).status_code,
            200,
        )
        config = self.client.get("/api/analysis-runs/config")
        self.assertEqual(config.status_code, 200)
        self.assertEqual(
            config.json()["reviewer_identity"]["authorization_mode"],
            "trusted_role_rbac",
        )
        self.assertTrue(config.json()["reviewer_identity"]["role_required"])

    def test_domain_owner_can_promote_candidate_to_gold_without_hash_input(self) -> None:
        result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="gold-review.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan indikator kinerja telah ditetapkan tahun 2026 dan "
                b"hasil evaluasinya didokumentasikan."
            ),
            analysis_mode="full_audit",
        )
        run_id = result["run"]["id"]
        detail = self.client.get(f"/api/analysis-runs/guided-review/{run_id}").json()
        mapping = detail["mappings"][0]
        candidate = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "reviewer-pertama",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": mapping["id"],
                "selected_source_fact_ids": [mapping["supporting_fact_ids"][0]],
                "expected_mapping": {},
                "expected_evidence_role": "primary",
                "expected_template_status": "substantive",
                "reason": "Mapping dan lokasi sumber sudah diperiksa pada dokumen.",
            },
        )
        self.assertEqual(candidate.status_code, 201, candidate.text)

        queue = self.client.get("/api/analysis-runs/governance/expert-dataset")
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual(queue.json()["summary"]["counts"]["expert_candidate"], 1)
        self.assertIsNone(queue.json()["summary"]["dataset_sha256"])

        self_review = self.client.post(
            f"/api/analysis-runs/governance/expert-dataset/{run_id}/decision",
            json={
                "reviewer_id": "reviewer-pertama",
                "decision": "approve",
                "reason": "Saya memeriksa ulang kandidat label ini.",
                "attested": True,
            },
        )
        self.assertEqual(self_review.status_code, 409, self_review.text)

        approved = self.client.post(
            f"/api/analysis-runs/governance/expert-dataset/{run_id}/decision",
            json={
                "reviewer_id": "domain-owner-kedua",
                "decision": "approve",
                "reason": "Mapping, grade, dan lokasi sumber sesuai dokumen resmi.",
                "attested": True,
            },
        )
        self.assertEqual(approved.status_code, 201, approved.text)
        body = approved.json()
        self.assertEqual(body["label"]["dataset_status"], "expert_gold")
        self.assertEqual(body["label"]["dataset_partition"], "evaluation")
        self.assertEqual(body["summary"]["expert_gold_case_count"], 1)
        self.assertEqual(body["summary"]["learning_gold_case_count"], 0)
        self.assertRegex(body["summary"]["dataset_sha256"], r"^[a-f0-9]{64}$")
        self.assertTrue(body["two_person_review_enforced"])

        refreshed = self.client.get("/api/analysis-runs/governance/expert-dataset").json()
        self.assertEqual(len(refreshed["candidates"]), 0)
        self.assertEqual(len(refreshed["gold"]), 1)
        self.assertFalse(refreshed["retrieval_feedback"]["active"])
        self.assertTrue(refreshed["retrieval_feedback"]["dataset_matches"])
        self.assertEqual(refreshed["retrieval_feedback"]["term_count"], 0)
        history = self.client.get(f"/api/analysis-runs/guided-review/{run_id}").json()["label_history"]
        self.assertEqual([item["dataset_status"] for item in history], ["expert_candidate", "expert_gold"])
        self.assertEqual(sum(bool(item["is_active"]) for item in history), 1)

        learning_result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="learning-review.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan indikator kinerja telah ditetapkan dan hasil evaluasinya "
                b"dibahas melalui forum rembug tahun 2026."
            ),
            analysis_mode="full_audit",
        )
        learning_run_id = learning_result["run"]["id"]
        learning_detail = self.client.get(
            f"/api/analysis-runs/guided-review/{learning_run_id}"
        ).json()
        learning_mapping = learning_detail["mappings"][0]
        learning_candidate = self.client.post(
            f"/api/analysis-runs/guided-review/{learning_run_id}",
            json={
                "reviewer_id": "reviewer-pertama",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": learning_mapping["id"],
                "selected_source_fact_ids": [learning_mapping["supporting_fact_ids"][0]],
                "expected_mapping": {},
                "expected_evidence_role": "supporting",
                "expected_template_status": "substantive",
                "reason": "Kasus learning diperiksa terpisah dari holdout evaluasi.",
            },
        )
        self.assertEqual(learning_candidate.status_code, 201, learning_candidate.text)
        learning_approved = self.client.post(
            f"/api/analysis-runs/governance/expert-dataset/{learning_run_id}/decision",
            json={
                "reviewer_id": "domain-owner-kedua",
                "decision": "approve",
                "dataset_partition": "learning",
                "reason": "Kasus dialokasikan khusus untuk vocabulary learning.",
                "attested": True,
            },
        )
        self.assertEqual(learning_approved.status_code, 201, learning_approved.text)
        learning_body = learning_approved.json()
        self.assertEqual(learning_body["label"]["dataset_partition"], "learning")
        self.assertEqual(learning_body["summary"]["evaluation_gold_case_count"], 1)
        self.assertEqual(learning_body["summary"]["learning_gold_case_count"], 1)
        self.assertEqual(learning_body["summary"]["dataset_sha256"], body["summary"]["dataset_sha256"])
        self.assertRegex(learning_body["summary"]["learning_dataset_sha256"], r"^[a-f0-9]{64}$")
        self.assertTrue(learning_body["retrieval_feedback"]["is_active"])

    def test_batch_zip_intake_is_safe_local_deduplicated_and_legacy_isolated(self) -> None:
        archive_buffer = BytesIO()
        duplicate_payload = (
            b"Kebijakan indikator kinerja telah ditetapkan dan dievaluasi berkala tahun 2026."
        )
        with ZipFile(archive_buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr("01-invalid.pdf", b"bukan PDF")
            archive.writestr("docs/02-evidence.txt", duplicate_payload)
            archive.writestr("copies/02-evidence-copy.txt", duplicate_payload)
            archive.writestr(
                "docs/03-evidence.txt",
                b"Pelaksanaan evaluasi risiko organisasi telah dilakukan pada periode tahun 2026.",
            )
            archive.writestr("notes/unsupported.bin", b"binary")
        archive_payload = archive_buffer.getvalue()

        with self.db.connect() as conn:
            legacy_before = conn.execute("SELECT COUNT(*) AS total FROM smart_upload_reviews").fetchone()["total"]

        response = self.client.post(
            "/api/analysis-runs/batch-intakes",
            files={"file": ("corpus.zip", archive_payload, "application/zip")},
            data={
                "analysis_mode": "full_audit",
                "review_limit": "2",
                "local_only": "true",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        created = response.json()
        batch_id = created["batch"]["id"]
        self.assertFalse(created["deduplicated"])
        self.assertFalse(created["batch"]["external_ai_allowed"])
        self.assertEqual(created["batch"]["enqueued_count"], 2)
        self.assertEqual(created["batch"]["rejected_count"], 1)
        self.assertEqual(created["batch"]["duplicate_count"], 1)
        self.assertTrue(any(item["member_status"] == "unsupported" for item in created["batch"]["members"]))

        deadline = time.monotonic() + 8
        snapshot = None
        while time.monotonic() < deadline:
            batch_response = self.client.get(f"/api/analysis-runs/batch-intakes/{batch_id}")
            self.assertEqual(batch_response.status_code, 200, batch_response.text)
            snapshot = batch_response.json()["batch"]
            if snapshot["status"] in {"completed", "completed_with_errors"}:
                break
            time.sleep(0.03)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["progress"]["completed"], 2)
        self.assertEqual(snapshot["progress"]["percentage"], 100.0)
        self.assertEqual(snapshot["status"], "completed_with_errors")

        with self.db.connect() as conn:
            batch_runs = conn.execute(
                """
                SELECT analysis_runs.external_ai_allowed, analysis_runs.provider, analysis_runs.model
                FROM analysis_batch_members
                JOIN analysis_jobs ON analysis_jobs.id = analysis_batch_members.job_id
                JOIN analysis_runs ON analysis_runs.id = analysis_jobs.run_id
                WHERE analysis_batch_members.batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            legacy_after = conn.execute("SELECT COUNT(*) AS total FROM smart_upload_reviews").fetchone()["total"]
        self.assertEqual(len(batch_runs), 2)
        self.assertTrue(all(not bool(row["external_ai_allowed"]) for row in batch_runs))
        self.assertTrue(all(row["provider"] == "local_only" for row in batch_runs))
        self.assertTrue(all(row["model"] is None for row in batch_runs))
        self.assertEqual(legacy_after, legacy_before)

        repeated = self.client.post(
            "/api/analysis-runs/batch-intakes",
            files={"file": ("corpus.zip", archive_payload, "application/zip")},
            data={"analysis_mode": "full_audit", "review_limit": "2", "local_only": "true"},
        )
        self.assertEqual(repeated.status_code, 202, repeated.text)
        self.assertTrue(repeated.json()["deduplicated"])
        self.assertEqual(repeated.json()["batch"]["id"], batch_id)
        recent = self.client.get("/api/analysis-runs/batch-intakes/recent?limit=1")
        self.assertEqual(recent.status_code, 200, recent.text)
        self.assertEqual(recent.json()["batches"][0]["id"], batch_id)

    def test_batch_zip_intake_rejects_archive_path_traversal_before_enqueue(self) -> None:
        archive_buffer = BytesIO()
        with ZipFile(archive_buffer, "w", ZIP_DEFLATED) as archive:
            archive.writestr("../escape.txt", b"tidak boleh diproses")
            archive.writestr("safe.txt", b"dokumen aman tetapi satu archive harus ditolak")
        with self.db.connect() as conn:
            jobs_before = conn.execute("SELECT COUNT(*) AS total FROM analysis_jobs").fetchone()["total"]
        response = self.client.post(
            "/api/analysis-runs/batch-intakes",
            files={"file": ("unsafe.zip", archive_buffer.getvalue(), "application/zip")},
            data={"review_limit": "2"},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("Path tidak aman", response.json()["detail"])
        with self.db.connect() as conn:
            jobs_after = conn.execute("SELECT COUNT(*) AS total FROM analysis_jobs").fetchone()["total"]
            rejected = conn.execute(
                "SELECT COUNT(*) AS total FROM analysis_batch_intakes WHERE status = 'rejected'"
            ).fetchone()["total"]
        self.assertEqual(jobs_after, jobs_before)
        self.assertEqual(rejected, 1)

    def test_guided_governance_preserves_rule_history_and_vision_fail_closed(self) -> None:
        rules_response = self.client.get("/api/analysis-runs/governance/rules?limit=200")
        self.assertEqual(rules_response.status_code, 200, rules_response.text)
        rules_payload = rules_response.json()
        self.assertEqual(rules_payload["parameter_count"], 184)
        self.assertEqual(rules_payload["rule_count"], 920)
        parameter = rules_payload["items"][0]
        decisions = [
            {
                "kk_id": rule["kk_id"],
                "kode": rule["kode"],
                "detail_kode": rule["detail_kode"],
                "grade": rule["grade"],
                "rule_checksum": rule["rule_checksum"],
                "status": "approved",
            }
            for rule in parameter["rules"]
        ]
        unattested = self.client.post(
            "/api/analysis-runs/governance/rules/decisions",
            json={
                "reviewer_id": "domain-owner",
                "reason": "Seluruh kontrak grade sudah diperiksa terhadap matriks resmi.",
                "attested": False,
                "decisions": decisions,
            },
        )
        self.assertEqual(unattested.status_code, 409, unattested.text)
        stale_decisions = [dict(item) for item in decisions]
        stale_decisions[0]["rule_checksum"] = "0" * 64
        stale = self.client.post(
            "/api/analysis-runs/governance/rules/decisions",
            json={
                "reviewer_id": "domain-owner",
                "reason": "Seluruh kontrak grade sudah diperiksa terhadap matriks resmi.",
                "attested": True,
                "decisions": stale_decisions,
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            self.client.get("/api/analysis-runs/governance/rules/history").json()["count"],
            0,
        )

        approved = self.client.post(
            "/api/analysis-runs/governance/rules/decisions",
            json={
                "reviewer_id": "domain-owner",
                "reason": "Seluruh kontrak grade sudah diperiksa terhadap matriks resmi.",
                "attested": True,
                "decisions": decisions,
            },
        )
        self.assertEqual(approved.status_code, 201, approved.text)
        self.assertEqual(approved.json()["saved_count"], 5)
        self.assertEqual(approved.json()["approved_rule_count"], 5)

        rejected_decision = dict(decisions[0], status="rejected")
        rejected = self.client.post(
            "/api/analysis-runs/governance/rules/decisions",
            json={
                "reviewer_id": "domain-owner",
                "reason": "Grade ini memerlukan perbaikan prerequisite sebelum disahkan.",
                "attested": True,
                "decisions": [rejected_decision],
            },
        )
        self.assertEqual(rejected.status_code, 201, rejected.text)
        history = self.client.get(
            "/api/analysis-runs/governance/rules/history",
            params={
                "kk_id": parameter["kk_id"],
                "kode": parameter["kode"],
                "detail_kode": parameter["detail_kode"],
            },
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["count"], 6)
        refreshed = self.client.get("/api/analysis-runs/governance/rules?limit=200").json()
        refreshed_parameter = next(
            item for item in refreshed["items"]
            if item["detail_kode"] == parameter["detail_kode"]
            and item["kk_id"] == parameter["kk_id"]
        )
        self.assertEqual(refreshed_parameter["review_state"], "rejected")

        probe_report = {
            "provider": self.settings.ai_provider,
            "model": self.settings.deepseek_model,
            "api_surface": "chat_completions_vision",
            "status": "passed",
            "report_sha256": "a" * 64,
            "expected_tokens": ["SPIP", "2026"],
            "observed_text": "SPIP 2026",
            "warnings": [],
            "error_message": None,
        }
        with patch("app.analysis.routes.run_synthetic_vision_probe", return_value=probe_report):
            probe = self.client.post(
                "/api/analysis-runs/governance/vision/probe",
                json={"reviewer_id": "technical-reviewer"},
            )
        self.assertEqual(probe.status_code, 201, probe.text)
        self.assertTrue(probe.json()["synthetic_only"])
        self.assertFalse(probe.json()["user_document_sent"])

        consent_too_early = self.client.post(
            "/api/analysis-runs/governance/vision/decisions",
            json={
                "reviewer_id": "data-owner",
                "scope": "external_data_processing",
                "status": "approved",
                "sensitivity_scope": "restricted",
                "expires_in_days": 30,
                "reason": "Pemrosesan restricted disetujui untuk pilot terkontrol.",
                "attested": True,
            },
        )
        self.assertEqual(consent_too_early.status_code, 409, consent_too_early.text)

        capability = self.client.post(
            "/api/analysis-runs/governance/vision/decisions",
            json={
                "reviewer_id": "technical-reviewer",
                "scope": "capability_validation",
                "status": "approved",
                "sensitivity_scope": "restricted",
                "evidence_sha256": "a" * 64,
                "expires_in_days": 30,
                "reason": "Uji synthetic membaca token dan schema dengan tepat.",
                "attested": True,
            },
        )
        self.assertEqual(capability.status_code, 201, capability.text)
        self.assertTrue(capability.json()["governance"]["checks"]["capability_approved"])
        consent = self.client.post(
            "/api/analysis-runs/governance/vision/decisions",
            json={
                "reviewer_id": "data-owner",
                "scope": "external_data_processing",
                "status": "approved",
                "sensitivity_scope": "restricted",
                "expires_in_days": 30,
                "reason": "Pemrosesan restricted disetujui untuk pilot terkontrol.",
                "attested": True,
            },
        )
        self.assertEqual(consent.status_code, 201, consent.text)
        self.assertTrue(
            consent.json()["governance"]["checks"]["restricted_data_consent_approved"]
        )
        self.assertFalse(consent.json()["governance"]["effective"])

        revoked = self.client.post(
            "/api/analysis-runs/governance/vision/decisions",
            json={
                "reviewer_id": "data-owner",
                "scope": "external_data_processing",
                "status": "revoked",
                "sensitivity_scope": "restricted",
                "expires_in_days": 30,
                "reason": "Consent dicabut sampai kebijakan pemrosesan diperbarui.",
                "attested": True,
            },
        )
        self.assertEqual(revoked.status_code, 201, revoked.text)
        self.assertFalse(
            revoked.json()["governance"]["checks"]["restricted_data_consent_approved"]
        )
        vision_history = self.client.get("/api/analysis-runs/governance/vision").json()
        self.assertEqual(len(vision_history["decision_history"]), 3)

        self.settings.analysis_require_reviewer_identity = True
        unauthenticated = self.client.get("/api/analysis-runs/governance/rules")
        self.assertEqual(unauthenticated.status_code, 401)
        authenticated = self.client.get(
            "/api/analysis-runs/governance/rules",
            headers={"X-Reviewer-Identity": "domain-owner"},
        )
        self.assertEqual(authenticated.status_code, 200, authenticated.text)

    def test_approved_evidence_reaches_controlled_upload_boundary(self) -> None:
        result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="approved-evidence.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan indikator kinerja Ditjen PDP telah ditetapkan tahun 2026. "
                b"Pelaksanaan indikator Ditjen PDP telah dilaksanakan tahun 2026. "
                b"Evaluasi indikator Ditjen PDP dilakukan secara berkala tahun 2026."
            ),
            analysis_mode="full_audit",
        )
        run_id = result["run"]["id"]
        mapping = result["mappings"][0]
        repository = AnalysisRepository(self.db)
        parameter = next(
            item for item in repository.parameter_index()
            if item["kk_id"] == mapping["kk_id"]
            and item["kode"] == mapping["kode"]
            and item["detail_kode"] == mapping["detail_kode"]
        )
        for grade, rule in compile_parameter_rules(parameter["grades"]).items():
            repository.save_rule_approval(
                {
                    "kk_id": mapping["kk_id"],
                    "kode": mapping["kode"],
                    "detail_kode": mapping["detail_kode"],
                    "grade": grade,
                    "rule_version": RULE_VERSION,
                    "rule_checksum": rule_checksum(rule),
                    "status": "approved",
                    "reviewer_id": "domain-owner",
                    "reason": "Approved for integration fixture.",
                    "rule_definition": rule,
                }
            )
        refreshed = AnalysisOrchestrator(self.db, self.settings).reverify(run_id)
        selected_assessment = next(
            item for item in refreshed["grade_assessments"]
            if item["mapping_candidate_id"] == mapping["id"]
        )
        selected_checks = [
            item for item in refreshed["verification_results"]
            if item["mapping_candidate_id"] == mapping["id"]
        ]
        self.assertTrue(selected_assessment["primary_allowed"], selected_assessment["rule_trace"])
        self.assertTrue(selected_checks)
        self.assertTrue(all(item["status"] == "verified" for item in selected_checks))

        approval = self.client.post(
            f"/api/analysis-runs/{run_id}/review-decisions",
            json={
                "reviewer_id": "human-reviewer",
                "decision": "approve",
                "mapping_candidate_id": mapping["id"],
                "final_mapping": {},
                "reason": "Evidence and source trace reviewed.",
            },
        )
        self.assertEqual(approval.status_code, 201, approval.text)

        grade = selected_assessment["candidate_grade"]
        slots = [
            item for item in self.db.evidence_slots(mapping["kk_id"], mapping["kode"])
            if item["detail_kode"] == mapping["detail_kode"] and item["grade"] == grade
        ]
        self.assertTrue(slots)
        AnalysisOrchestrator(self.db, self.settings).reverify(run_id)
        stale_approval_upload = self.client.post(
            f"/api/analysis-runs/{run_id}/controlled-upload",
            json={
                "mapping_candidate_id": mapping["id"],
                "reviewer_id": "human-reviewer",
                "category_name": slots[0]["category_name"],
            },
        )
        self.assertEqual(stale_approval_upload.status_code, 409)
        self.assertIn("run belum berada pada status approved", stale_approval_upload.json()["detail"])
        renewed_approval = self.client.post(
            f"/api/analysis-runs/{run_id}/review-decisions",
            json={
                "reviewer_id": "human-reviewer",
                "decision": "approve",
                "mapping_candidate_id": mapping["id"],
                "final_mapping": {},
                "reason": "Approval renewed after deterministic re-verification.",
            },
        )
        self.assertEqual(renewed_approval.status_code, 201, renewed_approval.text)
        self.settings.smart_upload_allow_real_upload = True
        self.settings.lumbung_share_token = "integration-fixture-token"
        upload_payload = {
            "mapping_candidate_id": mapping["id"],
            "reviewer_id": "human-reviewer",
            "category_name": slots[0]["category_name"],
        }
        upload_started = threading.Event()
        allow_upload_to_finish = threading.Event()
        bridge_calls: list[int] = []

        def blocking_confirm_upload(_service, review_id: int, _candidate_index: int) -> dict:
            bridge_calls.append(review_id)
            upload_started.set()
            if not allow_upload_to_finish.wait(timeout=5):
                raise AssertionError("Concurrent controlled-upload fixture timed out.")
            return {
                "status": "uploaded_primary",
                "message": "Mock WebDAV boundary accepted controlled upload.",
                "candidate": {"remote_path": "/mock/approved-evidence.txt"},
            }

        first_client = TestClient(self.app)
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload",
            new=blocking_confirm_upload,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    first_client.post,
                    f"/api/analysis-runs/{run_id}/controlled-upload",
                    json=upload_payload,
                )
                try:
                    self.assertTrue(upload_started.wait(timeout=5))
                    concurrent = self.client.post(
                        f"/api/analysis-runs/{run_id}/approve-upload",
                        json=upload_payload,
                    )
                    self.assertEqual(concurrent.status_code, 409, concurrent.text)
                    self.assertIn("sedang diproses", concurrent.json()["detail"])
                    live_metrics = repository.operational_metrics()
                    self.assertEqual(
                        live_metrics["controlled_uploads_by_status"]["uploading"],
                        1,
                    )
                    self.assertEqual(
                        live_metrics["stale_controlled_upload_reservation_count"],
                        0,
                    )
                    with self.db.connect() as conn:
                        conn.execute(
                            """
                            UPDATE controlled_upload_actions
                            SET created_at = datetime('now', '-11 minutes')
                            WHERE run_id = ? AND mapping_candidate_id = ?
                            """,
                            (run_id, mapping["id"]),
                        )
                    self.assertEqual(
                        repository.operational_metrics()[
                            "stale_controlled_upload_reservation_count"
                        ],
                        1,
                    )
                    stale_dashboard = self.client.get(
                        "/api/analysis-runs/readiness-dashboard"
                    )
                    self.assertEqual(stale_dashboard.status_code, 200)
                    stale_upload_mitigation = next(
                        item
                        for item in stale_dashboard.json()["temporary_mitigations"]
                        if item["gate"] == "controlled_upload_reservation"
                    )
                    self.assertEqual(stale_upload_mitigation["status"], "blocked")
                    self.assertIn(
                        "controlled_upload_reservation_stale",
                        {
                            item["code"]
                            for item in stale_dashboard.json()["alerting"]["alerts"]
                        },
                    )
                finally:
                    allow_upload_to_finish.set()
                upload = future.result(timeout=5)
            idempotent = self.client.post(
                f"/api/analysis-runs/{run_id}/approve-upload",
                json=upload_payload,
            )
        first_client.close()
        self.assertEqual(upload.status_code, 201, upload.text)
        self.assertFalse(upload.json()["idempotent"])
        self.assertEqual(idempotent.status_code, 201, idempotent.text)
        self.assertTrue(idempotent.json()["idempotent"])
        self.assertEqual(idempotent.json()["action_id"], upload.json()["action_id"])
        self.assertEqual(len(bridge_calls), 1)
        actions = repository.list_controlled_upload_actions(run_id)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[-1]["status"], "uploaded_primary")
        self.assertEqual(repository.get_run(run_id)["status"], "uploaded")

        ambiguous_result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="ambiguous-upload-evidence.txt",
            content_type="text/plain",
            payload=(
                b"Kebijakan indikator kinerja Ditjen PDP telah ditetapkan tahun 2026. "
                b"Pelaksanaan indikator Ditjen PDP telah dilaksanakan tahun 2026. "
                b"Evaluasi indikator Ditjen PDP dilakukan secara berkala tahun 2026."
            ),
            analysis_mode="full_audit",
        )
        ambiguous_run_id = ambiguous_result["run"]["id"]
        ambiguous_mapping = ambiguous_result["mappings"][0]
        ambiguous_parameter = next(
            item for item in repository.parameter_index()
            if item["kk_id"] == ambiguous_mapping["kk_id"]
            and item["kode"] == ambiguous_mapping["kode"]
            and item["detail_kode"] == ambiguous_mapping["detail_kode"]
        )
        for ambiguous_grade, ambiguous_rule in compile_parameter_rules(
            ambiguous_parameter["grades"]
        ).items():
            repository.save_rule_approval(
                {
                    "kk_id": ambiguous_mapping["kk_id"],
                    "kode": ambiguous_mapping["kode"],
                    "detail_kode": ambiguous_mapping["detail_kode"],
                    "grade": ambiguous_grade,
                    "rule_version": RULE_VERSION,
                    "rule_checksum": rule_checksum(ambiguous_rule),
                    "status": "approved",
                    "reviewer_id": "domain-owner",
                    "reason": "Approved for ambiguous upload fixture.",
                    "rule_definition": ambiguous_rule,
                }
            )
        ambiguous_refreshed = AnalysisOrchestrator(self.db, self.settings).reverify(
            ambiguous_run_id
        )
        ambiguous_assessment = next(
            item for item in ambiguous_refreshed["grade_assessments"]
            if item["mapping_candidate_id"] == ambiguous_mapping["id"]
        )
        self.assertTrue(ambiguous_assessment["primary_allowed"])
        ambiguous_approval = self.client.post(
            f"/api/analysis-runs/{ambiguous_run_id}/review-decisions",
            json={
                "reviewer_id": "human-reviewer",
                "decision": "approve",
                "mapping_candidate_id": ambiguous_mapping["id"],
                "final_mapping": {},
                "reason": "Evidence reviewed for ambiguous upload fixture.",
            },
        )
        self.assertEqual(ambiguous_approval.status_code, 201, ambiguous_approval.text)
        ambiguous_slots = [
            item for item in self.db.evidence_slots(
                ambiguous_mapping["kk_id"], ambiguous_mapping["kode"]
            )
            if item["detail_kode"] == ambiguous_mapping["detail_kode"]
            and item["grade"] == ambiguous_assessment["candidate_grade"]
        ]
        ambiguous_payload = {
            "mapping_candidate_id": ambiguous_mapping["id"],
            "reviewer_id": "human-reviewer",
            "category_name": ambiguous_slots[0]["category_name"],
        }
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload",
            side_effect=SmartUploadError("Simulated uncertain WebDAV response."),
        ):
            ambiguous = self.client.post(
                f"/api/analysis-runs/{ambiguous_run_id}/approve-upload",
                json=ambiguous_payload,
            )
        self.assertEqual(ambiguous.status_code, 409, ambiguous.text)
        ambiguous_actions = repository.list_controlled_upload_actions(ambiguous_run_id)
        self.assertEqual(len(ambiguous_actions), 1)
        self.assertEqual(ambiguous_actions[0]["status"], "blocked_ambiguous")
        self.assertIsNotNone(ambiguous_actions[0]["legacy_review_id"])
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload"
        ) as retry_bridge:
            ambiguous_retry = self.client.post(
                f"/api/analysis-runs/{ambiguous_run_id}/controlled-upload",
                json=ambiguous_payload,
            )
        self.assertEqual(ambiguous_retry.status_code, 409, ambiguous_retry.text)
        self.assertIn("ambigu", ambiguous_retry.json()["detail"].lower())
        retry_bridge.assert_not_called()

        ambiguous_action_id = int(ambiguous_actions[0]["id"])
        initial_snapshot = self.client.get(
            f"/api/analysis-runs/{ambiguous_run_id}"
        )
        self.assertEqual(initial_snapshot.status_code, 200)
        initial_reconciliation = initial_snapshot.json()[
            "controlled_upload_reconciliations"
        ][0]
        self.assertFalse(initial_reconciliation["effective"])
        self.assertEqual(initial_reconciliation["reviewer_count"], 0)
        first_reconciliation = self.client.post(
            f"/api/analysis-runs/{ambiguous_run_id}/controlled-upload-actions/{ambiguous_action_id}/reconciliation",
            json={
                "reviewer_id": "operations-one",
                "outcome": "confirmed_not_uploaded",
                "reason": "Folder tujuan dan legacy review sudah diperiksa langsung.",
                "attested": True,
                "expected_latest_event_id": None,
            },
        )
        self.assertEqual(first_reconciliation.status_code, 201, first_reconciliation.text)
        first_summary = first_reconciliation.json()["reconciliation"]
        self.assertFalse(first_summary["effective"])
        self.assertEqual(first_summary["reviewer_count"], 1)
        self.assertEqual(
            repository.operational_metrics()[
                "unresolved_controlled_upload_ambiguity_count"
            ],
            1,
        )
        stale_reconciliation = self.client.post(
            f"/api/analysis-runs/{ambiguous_run_id}/controlled-upload-actions/{ambiguous_action_id}/reconciliation",
            json={
                "reviewer_id": "operations-two",
                "outcome": "confirmed_not_uploaded",
                "reason": "Pemeriksaan independen menemukan file tidak ada di tujuan.",
                "attested": True,
                "expected_latest_event_id": None,
            },
        )
        self.assertEqual(stale_reconciliation.status_code, 409)
        self.assertIn("muat ulang", stale_reconciliation.json()["detail"].lower())
        second_reconciliation = self.client.post(
            f"/api/analysis-runs/{ambiguous_run_id}/controlled-upload-actions/{ambiguous_action_id}/reconciliation",
            json={
                "reviewer_id": "operations-two",
                "outcome": "confirmed_not_uploaded",
                "reason": "Pemeriksaan independen menemukan file tidak ada di tujuan.",
                "attested": True,
                "expected_latest_event_id": first_summary["latest_event_id"],
            },
        )
        self.assertEqual(second_reconciliation.status_code, 201, second_reconciliation.text)
        resolved_summary = second_reconciliation.json()["reconciliation"]
        self.assertTrue(resolved_summary["effective"])
        self.assertEqual(resolved_summary["outcome"], "confirmed_not_uploaded")
        self.assertEqual(resolved_summary["reviewer_count"], 2)
        ambiguity_metrics = repository.operational_metrics()
        self.assertEqual(
            ambiguity_metrics["resolved_controlled_upload_ambiguity_count"],
            1,
        )
        self.assertEqual(
            ambiguity_metrics["unresolved_controlled_upload_ambiguity_count"],
            0,
        )
        self.assertEqual(repository.get_run(ambiguous_run_id)["status"], "approved")
        self.assertEqual(
            repository.get_controlled_upload_action(ambiguous_action_id)["status"],
            "blocked_ambiguous",
        )
        final_override = self.client.post(
            f"/api/analysis-runs/{ambiguous_run_id}/controlled-upload-actions/{ambiguous_action_id}/reconciliation",
            json={
                "reviewer_id": "operations-three",
                "outcome": "confirmed_uploaded",
                "reason": "Attempt to change a final two-person decision.",
                "attested": True,
                "expected_latest_event_id": resolved_summary["latest_event_id"],
            },
        )
        self.assertEqual(final_override.status_code, 409)
        self.assertIn("sudah final", final_override.json()["detail"].lower())
        with self.db.connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                conn.execute(
                    """
                    UPDATE controlled_upload_reconciliation_events
                    SET reason = 'tampered'
                    WHERE id = ?
                    """,
                    (resolved_summary["latest_event_id"],),
                )
        with patch(
            "app.analysis.legacy_bridge.SmartUploadService.confirm_upload"
        ) as resolved_retry_bridge:
            resolved_retry = self.client.post(
                f"/api/analysis-runs/{ambiguous_run_id}/approve-upload",
                json=ambiguous_payload,
            )
        self.assertEqual(resolved_retry.status_code, 409)
        resolved_retry_bridge.assert_not_called()

    def test_release_evidence_ledger_requires_checksums_and_is_append_only(self) -> None:
        rejected = self.client.post(
            "/api/analysis-runs/release-evidence",
            json={
                "release_cycle_id": "cycle-canary-1",
                "release_version": "2026.1",
                "stage": "canary",
                "decision": "passed",
                "stable_cycle": True,
                "rollback_rehearsed": True,
                "critical_incident_count": 0,
                "reviewer_id": "product-owner",
                "reason": "Attempt without signed evaluation evidence.",
                "attested": True,
            },
        )
        self.assertEqual(rejected.status_code, 409)

        result = AnalysisOrchestrator(self.db, self.settings).start(
            file_name="release-evidence.txt",
            content_type="text/plain",
            payload=b"Kebijakan indikator tahun 2026 telah ditetapkan dan dievaluasi berkala.",
            analysis_mode="full_audit",
        )
        run_id = result["run"]["id"]
        detail = self.client.get(f"/api/analysis-runs/guided-review/{run_id}").json()
        mapping = detail["mappings"][0]
        candidate = self.client.post(
            f"/api/analysis-runs/guided-review/{run_id}",
            json={
                "reviewer_id": "release-label-reviewer",
                "outcome": "confirmed",
                "selected_mapping_candidate_id": mapping["id"],
                "selected_source_fact_ids": [mapping["supporting_fact_ids"][0]],
                "expected_mapping": {},
                "expected_evidence_role": "supporting",
                "expected_template_status": "substantive",
                "reason": "Mapping dan lokasi sumber diperiksa untuk release ledger.",
            },
        )
        self.assertEqual(candidate.status_code, 201, candidate.text)
        gold = self.client.post(
            f"/api/analysis-runs/governance/expert-dataset/{run_id}/decision",
            json={
                "reviewer_id": "release-domain-owner",
                "decision": "approve",
                "reason": "Kandidat release ledger sesuai dengan dokumen sumber.",
                "attested": True,
            },
        )
        self.assertEqual(gold.status_code, 201, gold.text)
        evaluation = self.client.post(
            "/api/analysis-runs/evaluation-reports/from-expert-gold",
            json={
                "dataset_name": "expert-gold-release-ledger",
                "reviewer_id": "evaluation-owner",
                "notes": "Server-derived report for append-only ledger test.",
                "attested": True,
            },
        )
        self.assertEqual(evaluation.status_code, 201, evaluation.text)
        evaluation_body = evaluation.json()
        report_id = evaluation_body["report"]["id"]
        self.assertEqual(
            evaluation_body["report"]["generation_method"],
            "server_derived_v2_partitioned",
        )
        self.assertTrue(evaluation_body["report"]["release_authority"])
        self.assertEqual(evaluation_body["report"]["case_count"], 1)
        self.assertRegex(evaluation_body["report"]["dataset_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("Kebijakan indikator", evaluation.text)
        manual = self.client.post(
            "/api/analysis-runs/evaluation-reports",
            json={
                "dataset_name": "manual-perfect-metrics",
                "dataset_status": "expert_gold",
                "case_count": evaluation_body["report"]["case_count"],
                "retrieval_recall_at_5": 1.0,
                "source_accuracy": 1.0,
                "overgrade_rate": 0.0,
                "grade_label_coverage": 1.0,
                "grade_assessment_coverage": 1.0,
                "report_sha256": "f" * 64,
                "dataset_sha256": evaluation_body["report"]["dataset_sha256"],
                "reviewer_id": "manual-evaluator",
                "notes": "Manual report is informational even when metrics look perfect.",
            },
        )
        self.assertEqual(manual.status_code, 201, manual.text)
        self.assertFalse(manual.json()["report"]["release_authority"])
        manual_bypass = self.client.post(
            "/api/analysis-runs/release-evidence",
            json={
                "release_cycle_id": "shadow-manual-bypass",
                "release_version": "2026.1",
                "stage": "shadow",
                "decision": "passed",
                "evaluation_report_id": manual.json()["report"]["id"],
                "reviewer_id": "product-owner",
                "reason": "Manual report must never authorize a passed release event.",
                "attested": True,
            },
        )
        self.assertEqual(manual_bypass.status_code, 409, manual_bypass.text)
        self.assertIn("manual/legacy", manual_bypass.json()["detail"])
        duplicate = self.client.post(
            "/api/analysis-runs/evaluation-reports/from-expert-gold",
            json={
                "dataset_name": "expert-gold-release-ledger",
                "reviewer_id": "different-evaluator",
                "notes": "Attempt to replace immutable report metadata.",
                "attested": True,
            },
        )
        self.assertEqual(duplicate.status_code, 201, duplicate.text)
        self.assertEqual(duplicate.json()["report"]["id"], report_id)
        self.assertEqual(duplicate.json()["report"]["reviewer_id"], "evaluation-owner")

        premature_shadow = self.client.post(
            "/api/analysis-runs/release-evidence",
            json={
                "release_cycle_id": "shadow-cycle-1",
                "release_version": "2026.1",
                "stage": "shadow",
                "decision": "passed",
                "evaluation_report_id": report_id,
                "reviewer_id": "product-owner",
                "reason": "Shadow comparison recorded with server-derived checksums.",
                "evidence": {"ticket": "REL-SHADOW-1"},
                "attested": True,
            },
        )
        self.assertEqual(premature_shadow.status_code, 409, premature_shadow.text)

        ready_promotion = {
            "shadow": {"ready": True, "reasons": []},
            "canary": {"ready": False, "reasons": ["Rule gate fixture belum lengkap."]},
            "general_release": {"ready": False, "reasons": []},
            "approved_rule_count": 0,
            "total_rule_count": 920,
            "high_security_findings": 0,
        }
        with patch(
            "app.analysis.routes.EvaluationLearningEngine.promotion_readiness",
            return_value=ready_promotion,
        ):
            missing_shadow_ledger = self.client.post(
                "/api/analysis-runs/release-evidence",
                json={
                    "release_cycle_id": "shadow-cycle-missing-ledger",
                    "release_version": "2026.1",
                    "stage": "shadow",
                    "decision": "passed",
                    "evaluation_report_id": report_id,
                    "reviewer_id": "product-owner",
                    "reason": "Promotion metrics alone must not bypass shadow ledger.",
                    "evidence": {"ticket": "REL-SHADOW-MISSING"},
                    "attested": True,
                },
            )
        self.assertEqual(missing_shadow_ledger.status_code, 409)
        self.assertIn("50 shadow comparison", missing_shadow_ledger.json()["detail"])

        with patch(
            "app.analysis.routes.EvaluationLearningEngine.promotion_readiness",
            return_value=ready_promotion,
        ), patch(
            "app.analysis.routes.ShadowComparisonService.report",
            return_value={
                "completed_count": 50,
                "review_target_reached": True,
                "report_sha256": "c" * 64,
                "top_1_match_rate": 0.9,
                "exact_set_match_rate": 0.8,
            },
        ):
            passed_shadow = self.client.post(
                "/api/analysis-runs/release-evidence",
                json={
                    "release_cycle_id": "shadow-cycle-1",
                    "release_version": "2026.1",
                    "stage": "shadow",
                    "decision": "passed",
                    "evaluation_report_id": report_id,
                    "reviewer_id": "product-owner",
                    "reason": "Shadow comparison recorded with server-derived checksums.",
                    "evidence": {"ticket": "REL-SHADOW-1"},
                    "attested": True,
                },
            )
        self.assertEqual(passed_shadow.status_code, 201, passed_shadow.text)
        passed_event = passed_shadow.json()["event"]
        self.assertEqual(passed_event["dataset_sha256"], evaluation_body["report"]["dataset_sha256"])
        self.assertEqual(passed_event["comparison_report_sha256"], "c" * 64)
        self.assertTrue(passed_event["evidence"]["gate_snapshot"]["validated"])
        self.assertEqual(
            passed_event["evidence"]["gate_snapshot"]["shadow_comparison_count"],
            50,
        )

        invalid_stable = self.client.post(
            "/api/analysis-runs/release-evidence",
            json={
                "release_cycle_id": "canary-cycle-too-early",
                "release_version": "2026.1",
                "stage": "canary",
                "decision": "passed",
                "evaluation_report_id": report_id,
                "stable_cycle": True,
                "rollback_rehearsed": True,
                "critical_incident_count": 0,
                "reviewer_id": "product-owner",
                "reason": "Must fail because expert and rule gates are incomplete.",
                "attested": True,
            },
        )
        self.assertEqual(invalid_stable.status_code, 409, invalid_stable.text)

        planned = self.client.post(
            "/api/analysis-runs/release-evidence",
            json={
                "release_cycle_id": "canary-plan-1",
                "release_version": "2026.2",
                "stage": "canary",
                "decision": "planned",
                "reviewer_id": "product-owner",
                "reason": "Canary plan recorded before quality gates are complete.",
                "attested": True,
            },
        )
        self.assertEqual(planned.status_code, 201, planned.text)
        planned_id = planned.json()["event"]["id"]

        ledger = self.client.get("/api/analysis-runs/release-evidence")
        self.assertEqual(ledger.status_code, 200)
        ledger_body = ledger.json()
        self.assertTrue(ledger_body["automatic_checksum_derivation"])
        self.assertEqual(ledger_body["summary"]["stable_release_cycle_count"], 0)
        self.assertFalse(ledger_body["summary"]["legacy_deprecation_eligible"])
        self.assertEqual(len(ledger_body["evaluation_reports"]), 2)
        self.assertEqual(
            sum(bool(item["release_authority"]) for item in ledger_body["evaluation_reports"]),
            1,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE analysis_release_events SET reason = 'mutated' WHERE id = ?",
                    (planned_id,),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE evaluation_reports SET reviewer_id = 'mutated' WHERE id = ?",
                    (report_id,),
                )


if __name__ == "__main__":
    unittest.main()
