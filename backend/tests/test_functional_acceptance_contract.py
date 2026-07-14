from __future__ import annotations

import unittest

from scripts.run_functional_acceptance import (
    REQUIRED_ENGINE_NAMES,
    _corpus_evidence,
    _summarize_run,
)


class FunctionalAcceptanceContractTests(unittest.TestCase):
    def test_partial_operational_run_passes_only_when_it_is_fail_closed(self) -> None:
        result = {
            "run": {
                "status": "review_required",
                "coverage_status": "partial",
                "primary_blocked": True,
                "total_units": 2,
                "processed_units": 1,
                "failed_units": 0,
                "ocr_required_units": 1,
            },
            "engines": [
                {"engine_name": engine_name}
                for engine_name in sorted(REQUIRED_ENGINE_NAMES)
            ],
            "facts": [
                {
                    "sources": [
                        {
                            "source_quote_verified": True,
                            "source_location": {"page": 1},
                        }
                    ]
                }
            ],
            "mappings": [{}],
            "grade_assessments": [{}],
            "verification_results": [{}],
        }

        summary = _summarize_run("operational-pdf-1", "pdf", result)

        self.assertTrue(summary["passed"])
        self.assertTrue(summary["incomplete_fail_closed"])
        result["run"]["primary_blocked"] = False
        self.assertFalse(
            _summarize_run("operational-pdf-1", "pdf", result)["passed"]
        )

    def test_corpus_contract_keeps_all_fifty_cases_unlabelled(self) -> None:
        summary = {
            "document_count": 110,
            "file_kind_counts": {"pdf": 68, "docx": 6, "xlsx": 24, "image": 12},
            "local_only": True,
            "external_ai_used": False,
            "office_render_failure_count": 0,
            "local_ocr_processed_unit_count": 482,
            "local_ocr_remaining_unit_count": 18,
            "local_ocr_review_candidate_count": 13,
            "local_ocr_visual_semantics_pending_count": 193,
        }
        queue = [
            {
                "file_kind": ("pdf", "docx", "xlsx", "image")[index % 4],
                "expected_mappings": [],
                "expected_source_locations": [],
            }
            for index in range(50)
        ]

        evidence = _corpus_evidence(summary, queue)

        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["review_queue_case_count"], 50)
        self.assertEqual(evidence["unlabelled_case_count"], 50)
