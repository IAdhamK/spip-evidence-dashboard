from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.contracts import EngineResult, EngineStatus
from app.analysis.repository import AnalysisRepository
from app.database import Database


class OCRResourceMetricsTests(unittest.TestCase):
    def test_repository_aggregates_content_free_ocr_resource_output(self) -> None:
        with TemporaryDirectory() as directory:
            repository = AnalysisRepository(
                Database(str(Path(directory) / "metrics.db"))
            )
            payload = b"synthetic"
            checksum = hashlib.sha256(payload).hexdigest()
            document = repository.upsert_document(
                file_name="synthetic.png",
                content_type="image/png",
                size_bytes=len(payload),
                sha256=checksum,
                payload=payload,
                ttl_hours=1,
            )
            run_id = repository.create_run(
                document_id=document["id"],
                analysis_mode="full_audit",
                pipeline_version=PIPELINE_VERSION,
                parser_version=PARSER_VERSION,
                rule_version=RULE_VERSION,
                prompt_version=PROMPT_VERSION,
                provider=None,
                model=None,
                configuration_hash="ocr-resource-test",
                external_ai_allowed=False,
            )
            repository.save_engine_result(
                run_id,
                EngineResult(
                    engine_name="visual_ocr",
                    engine_version=PIPELINE_VERSION,
                    status=EngineStatus.PARTIAL,
                    input_checksum=checksum,
                    output={
                        "local_ocr_attempt_count": 7,
                        "local_ocr_timeout_count": 2,
                        "local_ocr_budget_exhausted_units": 1,
                        "local_ocr_document_elapsed_ms": 12_345,
                        "durable_checkpoint_batches": 3,
                        "local_ocr_budget_exhaustion_reasons": {
                            "page-1": "unit_time_budget_exhausted"
                        },
                    },
                ),
            )

            metrics = repository.operational_metrics()["ocr_resource"]
            self.assertEqual(metrics["attempt_count"], 7)
            self.assertEqual(metrics["timeout_count"], 2)
            self.assertEqual(metrics["budget_exhausted_unit_count"], 1)
            self.assertEqual(metrics["durable_checkpoint_batch_count"], 3)
            self.assertEqual(metrics["document_elapsed_ms"], 12_345)
            self.assertEqual(
                metrics["budget_exhaustion_reason_counts"],
                {"unit_time_budget_exhausted": 1},
            )


if __name__ == "__main__":
    unittest.main()
