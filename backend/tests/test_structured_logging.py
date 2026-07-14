from __future__ import annotations

import json
import logging
import unittest

from app.analysis.structured_logging import (
    LOGGER_NAME,
    LOG_SCHEMA_VERSION,
    build_analysis_log_record,
    emit_analysis_log,
)


class StructuredAnalysisLoggingTests(unittest.TestCase):
    def test_record_is_content_free_and_machine_readable(self) -> None:
        record = build_analysis_log_record(
            "job_completed",
            run_id=42,
            job_id="12345678-abcd",
            stage="orchestration",
            status="review_required",
            reason_code="terminal_run",
            attempt=2,
            counters={"duration_ms": 12.5, "processed_units": 7},
        )
        self.assertEqual(record["schema_version"], LOG_SCHEMA_VERSION)
        self.assertEqual(record["run_id"], 42)
        self.assertEqual(record["job_id"], "12345678-abcd")
        self.assertEqual(record["counters"], {"duration_ms": 12.5, "processed_units": 7})
        serialized = json.dumps(record)
        self.assertNotIn("file_name", serialized)
        self.assertNotIn("source_location", serialized)
        self.assertNotIn("document_text", serialized)

    def test_unsafe_identifiers_and_non_finite_counters_are_omitted(self) -> None:
        record = build_analysis_log_record(
            "job_failed",
            job_id="dokumen rahasia.pdf",
            stage="contains spaces",
            status="failed",
            reason_code="payload_missing",
            counters={"bad key": 1, "not_finite": float("inf"), "failed_units": 1},
        )
        self.assertNotIn("job_id", record)
        self.assertNotIn("stage", record)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["reason_code"], "payload_missing")
        self.assertEqual(record["counters"], {"failed_units": 1})

    def test_unknown_event_is_rejected_and_emitter_outputs_json(self) -> None:
        with self.assertRaises(ValueError):
            build_analysis_log_record("document_contents")
        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            emit_analysis_log(
                "run_attached",
                run_id=9,
                job_id="abcdef12-3456",
                status="running",
            )
        payload = json.loads(captured.output[0].split(":", 2)[-1])
        self.assertEqual(payload["event"], "run_attached")
        self.assertEqual(payload["run_id"], 9)


if __name__ == "__main__":
    unittest.main()
