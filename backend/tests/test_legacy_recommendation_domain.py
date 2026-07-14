from __future__ import annotations

import ast
from pathlib import Path
import unittest

from app import legacy_recommendation_domain as domain
from app import smart_upload


MATURE_EVIDENCE_TEXT = (
    "Keputusan pimpinan telah ditetapkan. Undangan sosialisasi dan daftar hadir. "
    "Register risiko telah disusun dan dilaksanakan. Laporan hasil evaluasi berkala "
    "semester I menunjukkan temuan. Hasil evaluasi ditindaklanjuti dengan revisi "
    "proses berdasarkan evaluasi."
)

TEMPLATE_TEXT = (
    "Petunjuk pengisian formulir matriks risiko. Narasi merupakan contoh. "
    "Nama unit pemilik risiko ........"
)


def candidate(grade: str = "A", confidence: float = 0.9) -> dict:
    return {
        "kk_id": "KK3.1",
        "kode": "1.1",
        "detail_kode": "1.1.1",
        "grade": grade,
        "confidence": confidence,
        "subunsur_name": "Penilaian risiko",
        "uraian": "Evaluasi dan tindak lanjut risiko",
    }


class LegacyRecommendationDomainTests(unittest.TestCase):
    def test_smart_upload_reexports_domain_contract(self) -> None:
        public_names = (
            "classify_evidence_context",
            "detect_template_document",
            "apply_reasoning_gate",
            "apply_evidence_analysis_gate",
            "apply_batch_package_gate",
            "score_batch_package",
            "score_candidate_reasoning",
            "candidate_status_for_score",
            "build_document_profile",
            "compact_document_profile",
            "extract_external_evidence_links",
            "safe_confidence",
            "reasoning_candidate_sort_key",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(getattr(smart_upload, name), getattr(domain, name))

    def test_blank_template_remains_fail_closed_even_with_high_local_confidence(self) -> None:
        classification = smart_upload.classify_evidence_context(
            "template.xlsx", TEMPLATE_TEXT, {}
        )
        self.assertTrue(classification["template_guard"]["is_template"])
        self.assertTrue(classification["template_guard"]["is_blank_or_instructional"])
        self.assertEqual(classification["safe_grade_ceiling"], "E")

        gated = smart_upload.apply_reasoning_gate([candidate("A", 0.95)], classification)
        result = gated["candidates"][0]
        self.assertFalse(result["primary_allowed"])
        self.assertTrue(result["reasoning_scorecard"]["primary_blocked"])
        self.assertLess(result["reasoning_score"], domain.RELEVANCE_SUPPORTING_THRESHOLD)
        self.assertTrue(
            any("Target Grade A melebihi grade aman E" in warning for warning in result["gate_warnings"])
        )

    def test_complete_maturity_chain_can_reach_grade_a_without_overgrade(self) -> None:
        classification = smart_upload.classify_evidence_context(
            "tindak_lanjut_2026.pdf", MATURE_EVIDENCE_TEXT, {}
        )
        self.assertEqual(
            classification["chain"],
            {
                "kebijakan": True,
                "sosialisasi": True,
                "implementasi": True,
                "evaluasi": True,
                "perbaikan": True,
            },
        )
        self.assertEqual(classification["safe_grade_ceiling"], "A")
        self.assertEqual(classification["evidence_type"], "perbaikan")

        result = smart_upload.apply_reasoning_gate([candidate("A")], classification)["candidates"][0]
        self.assertTrue(result["primary_allowed"])
        self.assertFalse(result["reasoning_scorecard"]["primary_blocked"])
        self.assertGreater(result["reasoning_score"], domain.RELEVANCE_PRIMARY_THRESHOLD)

    def test_xlsx_reference_locations_contribute_without_becoming_unbounded_content(self) -> None:
        extraction = {
            "method": "xlsx",
            "evidence_rows": [
                {
                    "sheet": "Evaluasi",
                    "row": 12,
                    "text": "Laporan hasil evaluasi semester I",
                    "stage_hints": ["Evaluasi Berkala"],
                }
            ],
            "evidence_links": [
                {
                    "sheet": "Evaluasi",
                    "cell": "F12",
                    "label": "Laporan tindak lanjut hasil evaluasi",
                    "url": "https://example.org/bukti",
                    "context": "hasil evaluasi ditindaklanjuti",
                    "stage_hints": ["Perbaikan"],
                }
            ],
        }
        classification = smart_upload.classify_evidence_context(
            "matriks.xlsx",
            "Keputusan ditetapkan dan register risiko telah dilaksanakan.",
            extraction,
        )
        self.assertEqual(classification["external_evidence_link_count"], 1)
        self.assertGreater(classification["evidence_reference_char_count"], 0)
        self.assertIn("evaluasi", classification["reference_stage_hits"]["evaluasi"])
        self.assertIn("hasil evaluasi ditindaklanjuti", classification["link_stage_hits"]["perbaikan"])

    def test_batch_gate_is_deterministic_and_does_not_invent_missing_chain(self) -> None:
        result = smart_upload.score_batch_package(
            [
                {
                    "candidates": [
                        {
                            **candidate("C"),
                            "reasoning_score": 90,
                            "primary_allowed": True,
                        }
                    ],
                    "reasoning_gate": {
                        "classification": {
                            "chain": {
                                "kebijakan": True,
                                "sosialisasi": False,
                                "implementasi": True,
                                "evaluasi": False,
                                "perbaikan": False,
                            }
                        }
                    },
                }
            ]
        )
        self.assertEqual(result["safe_grade"], "C")
        self.assertEqual(result["score"], 72.5)
        self.assertEqual(result["status"], "Kandidat Pendukung")
        self.assertTrue(any("evaluasi berkala" in item for item in result["missing_chain"]))
        self.assertTrue(any("tindak lanjut" in item for item in result["missing_chain"]))

    def test_evidence_analysis_demotes_ai_primary_that_fails_deterministic_gate(self) -> None:
        classification = smart_upload.classify_evidence_context(
            "template.xlsx", TEMPLATE_TEXT, {}
        )
        gated_candidates = smart_upload.apply_reasoning_gate(
            [candidate("A", 0.95)], classification
        )["candidates"]
        analysis = {
            "summary": "terlalu singkat",
            "placements": {
                "primary": [{"index": 0, "role": "utama", "reason": "AI memilih"}],
                "supporting": [],
                "weak": [],
            },
        }
        result = smart_upload.apply_evidence_analysis_gate(
            analysis, gated_candidates, classification
        )
        self.assertEqual(result["placements"]["primary"], [])
        self.assertEqual(result["placements"]["supporting"][0]["role"], "perlu reviu")
        self.assertIn("form, template", result["summary"])
        self.assertTrue(
            any("diturunkan" in warning for warning in result["reasoning_gate"]["warnings"])
        )

    def test_domain_module_has_no_database_webdav_or_provider_dependency(self) -> None:
        domain_path = Path(domain.__file__)
        tree = ast.parse(domain_path.read_text(encoding="utf-8"))
        imported_modules: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        forbidden = {
            "app.config",
            "app.database",
            "app.legacy_ai_transport",
            "app.webdav_client",
            "urllib.request",
        }
        self.assertTrue(forbidden.isdisjoint(imported_modules))

        smart_tree = ast.parse(Path(smart_upload.__file__).read_text(encoding="utf-8"))
        moved_definitions = {
            "classify_evidence_context",
            "detect_template_document",
            "apply_reasoning_gate",
            "score_candidate_reasoning",
            "apply_evidence_analysis_gate",
            "score_batch_package",
        }
        defined = {
            node.name
            for node in smart_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(moved_definitions.isdisjoint(defined))


if __name__ == "__main__":
    unittest.main()
