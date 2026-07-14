from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.domain.cross_document import CrossDocumentSynthesisEngine
from app.analysis.repository import AnalysisRepository
from app.database import Database


class CrossDocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "package.db"))
        self.repository = AnalysisRepository(self.db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_run(self, suffix: str, fact_type: str, claim: str, period: str = "2026") -> int:
        payload = claim.encode()
        document = self.repository.upsert_document(
            file_name=f"{suffix}.txt",
            content_type="text/plain",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            payload=payload,
            ttl_hours=72,
        )
        run_id = self.repository.create_run(
            document_id=document["id"],
            analysis_mode="full_audit",
            pipeline_version=PIPELINE_VERSION,
            parser_version=PARSER_VERSION,
            rule_version=RULE_VERSION,
            prompt_version=PROMPT_VERSION,
            provider="test",
            model="test",
            configuration_hash=f"config-{suffix}",
        )
        self.repository.update_run(
            run_id,
            status="approved",
            coverage_status="complete",
            coverage_percentage=100,
            total_units=1,
            processed_units=1,
            primary_blocked=False,
        )
        unit_ids = self.repository.save_document_units(
            run_id,
            [{
                "unit_key": "text-1",
                "unit_type": "text_chunk",
                "ordinal": 1,
                "heading_path": [],
                "source_location": {"line_start": 1, "line_end": 1},
                "text": claim,
                "status": "processed",
                "warnings": [],
                "metadata": {},
            }],
        )
        facts = self.repository.save_extracted_facts(
            run_id,
            [{
                "fact_key": f"fact-{suffix}",
                "claim": claim,
                "fact_type": fact_type,
                "organization": "Ditjen PDP",
                "period": period,
                "confidence": 0.9,
                "source": {
                    "unit_id": unit_ids[0],
                    "source_location": {"line_start": 1},
                    "source_quote": claim,
                },
            }],
        )
        mappings = self.repository.save_mapping_candidates(
            run_id,
            [{
                "kk_id": "KK3.1",
                "kode": "3.1",
                "detail_kode": "3.1.1",
                "retrieval_score": 0.8,
                "mapping_score": 0.85,
                "status": "candidate",
                "supporting_fact_ids": [facts[0]["id"]],
                "reasons": ["fixture"],
                "missing_evidence": [],
            }],
        )
        mapping_id = mappings[0]["id"]
        self.repository.save_verification_result(
            run_id,
            {
                "mapping_candidate_id": mapping_id,
                "verifier_type": "fixture",
                "status": "verified",
                "findings": [],
                "source_coverage_ok": True,
                "grade_rule_ok": True,
                "period_ok": True,
                "organization_ok": True,
            },
        )
        self.repository.save_human_review_decision(
            run_id,
            {
                "mapping_candidate_id": mapping_id,
                "reviewer_id": "fixture-reviewer",
                "decision": "approve",
                "original_mapping": mappings[0],
                "final_mapping": mappings[0],
                "reason": "Fixture approval after verification.",
                "override_warnings": [],
                "pipeline_version": PIPELINE_VERSION,
                "rule_version": RULE_VERSION,
            },
        )
        return run_id

    def test_combines_only_same_parameter_and_scope(self) -> None:
        policy_run = self.make_run("policy", "policy", "Kebijakan manajemen risiko ditetapkan.")
        implementation_run = self.make_run("implementation", "implementation", "Peta risiko telah dilaksanakan.")
        package_id = self.repository.create_package(
            name="Paket MR 2026",
            run_ids=[policy_run, implementation_run],
            organization="Ditjen PDP",
            period="2026",
        )
        package = CrossDocumentSynthesisEngine(self.repository).run(package_id)
        self.assertEqual(package["status"], "review_required")
        self.assertTrue(package["primary_blocked"])
        self.assertEqual(len(package["assessments"]), 1)
        assessment = package["assessments"][0]
        self.assertTrue(assessment["chain"]["policy"])
        self.assertTrue(assessment["chain"]["implementation"])
        self.assertEqual(assessment["safe_grade"], "C")
        self.assertEqual(assessment["supporting_run_ids"], [policy_run, implementation_run])

    def test_marks_period_mismatch_as_contradiction(self) -> None:
        current = self.make_run("current", "policy", "Kebijakan tahun berjalan.", period="2026")
        old = self.make_run("old", "implementation", "Implementasi tahun lama.", period="2025")
        package_id = self.repository.create_package(
            name="Paket periode salah",
            run_ids=[current, old],
            organization="Ditjen PDP",
            period="2026",
        )
        package = CrossDocumentSynthesisEngine(self.repository).run(package_id)
        self.assertEqual(package["assessments"][0]["status"], "contradicted")
        self.assertIsNone(package["assessments"][0]["safe_grade"])
        self.assertTrue(any("Periode" in item for item in package["assessments"][0]["contradictions"]))

    def test_unapproved_member_is_blocked_before_synthesis(self) -> None:
        first = self.make_run("approved", "policy", "Kebijakan disahkan.")
        second = self.make_run("unapproved", "implementation", "Implementasi berjalan.")
        self.repository.update_run(second, status="review_required", primary_blocked=True)
        package_id = self.repository.create_package(
            name="Paket tidak eligible",
            run_ids=[first, second],
            organization="Ditjen PDP",
            period="2026",
        )
        package = CrossDocumentSynthesisEngine(self.repository).run(package_id)
        self.assertEqual(package["status"], "blocked")
        self.assertEqual(package["assessments"], [])


if __name__ == "__main__":
    unittest.main()
