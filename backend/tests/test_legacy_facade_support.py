from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

from app.config import Settings
from app import legacy_ai_normalization as ai_normalization
from app import legacy_candidate_ranking as ranking
from app import legacy_upload_support as upload_support
from app import smart_upload


def seed() -> ranking.CandidateSeed:
    return ranking.CandidateSeed(
        kk_id="KK3.1",
        kode="1.1",
        detail_kode="1.1.1",
        grade="C",
        subunsur_name="Penilaian Risiko",
        unsur="Unsur Penilaian Risiko",
        uraian="Evaluasi Risiko",
        kriteria="Risiko telah dievaluasi",
        penjelasan="Bukti implementasi tersedia",
        cara_pengujian=None,
        folder_path="/KK3.1/1.1/1.1.1/C",
        public_url=None,
        corpus="register risiko evaluasi tindak lanjut",
    )


class LegacyFacadeSupportTests(unittest.TestCase):
    def test_smart_upload_reexports_all_support_contracts(self) -> None:
        contracts = {
            ai_normalization: (
                "normalize_batch_analysis",
                "normalize_evidence_analysis",
                "extract_narrative_section",
                "format_narrative_context",
                "enrich_evidence_analysis",
                "merge_ai_result",
            ),
            ranking: (
                "CandidateSeed",
                "collect_batch_candidates",
                "normalize_candidate_limit",
                "tokenize",
                "score_candidate",
                "contextual_candidate_adjustment",
            ),
            upload_support: (
                "normalize_smart_upload_action",
                "compute_file_sha256",
                "build_duplicate_summary",
                "build_candidate_duplicate_check",
                "attach_duplicate_checks",
                "build_analysis_summary",
                "sanitize_upload_filename",
            ),
        }
        for module, names in contracts.items():
            for name in names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(smart_upload, name), getattr(module, name))

    def test_candidate_seed_and_ranking_keep_legacy_semantics(self) -> None:
        item = seed()
        with self.assertRaises(FrozenInstanceError):
            item.grade = "A"

        query = smart_upload.tokenize("register risiko evaluasi")
        score = smart_upload.score_candidate(
            query,
            smart_upload.tokenize(item.corpus),
            item,
            "laporan.pdf",
        )
        self.assertEqual(score, 0.82)
        self.assertEqual(
            smart_upload.reason_labels(
                sorted(query & smart_upload.tokenize(item.corpus)), item
            ),
            [
                "Subunsur cocok: risiko",
                "Parameter cocok: evaluasi, risiko",
                "Kriteria cocok: risiko",
            ],
        )

    def test_batch_candidate_collection_deduplicates_and_tracks_all_files(self) -> None:
        base = {
            "kk_id": "KK3.1",
            "kode": "1.1",
            "detail_kode": "1.1.1",
            "grade": "C",
            "reasoning_score": 80,
        }
        result = smart_upload.collect_batch_candidates(
            [
                {"candidates": [{**base, "confidence": 0.7}]},
                {"candidates": [{**base, "confidence": 0.9}]},
            ],
            10,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["file_indexes"], [0, 1])
        self.assertEqual(result[0]["batch_confidence"], 0.9)

    def test_ai_normalization_clamps_values_and_preserves_complete_summary(self) -> None:
        summary = (
            "Evidence menunjukkan evaluasi berkala telah dilakukan dan hasilnya "
            "dipakai untuk menyiapkan tindak lanjut perbaikan organisasi secara terukur."
        )
        result = smart_upload.normalize_evidence_analysis(
            {
                "summary": summary,
                "placements": {
                    "primary": [{"index": "2", "grade": "b", "confidence": 2}]
                },
            }
        )
        self.assertEqual(result["summary"], summary)
        self.assertEqual(result["placements"]["primary"][0]["index"], 2)
        self.assertEqual(result["placements"]["primary"][0]["grade"], "B")
        self.assertEqual(result["placements"]["primary"][0]["confidence"], 1)

        narrative = (
            "Kesimpulan Evidence: Evidence ini telah diperiksa dari laporan evaluasi "
            "berkala dan memperlihatkan hasil yang dapat ditelusuri ke sumber dokumen. "
            "Grade Aman: B"
        )
        extracted = smart_upload.extract_narrative_section(
            narrative, ("kesimpulan evidence",), 180
        )
        self.assertTrue(extracted.endswith("."))
        self.assertNotIn("Grade Aman", extracted)

    def test_ai_content_and_merge_contracts_remain_deterministic(self) -> None:
        self.assertEqual(
            smart_upload.ai_message_content(
                {"payload": {"choices": [{"message": {"reasoning_content": "fallback"}}]}}
            ),
            "fallback",
        )
        merged = smart_upload.merge_ai_result(
            [
                {
                    "kk_id": "KK3.1",
                    "kode": "1",
                    "detail_kode": "1.1",
                    "grade": "C",
                    "confidence": 0.4,
                    "reasons": [],
                }
            ],
            [{"index": 0, "confidence": 0.9, "reason": "AI cocok"}],
        )
        self.assertEqual(merged[0]["confidence"], 0.9)
        self.assertEqual(merged[0]["source"], "knowledge_base_ai_rerank")
        self.assertEqual(merged[0]["reasons"], ["AI: AI cocok"])

    def test_deepseek_narrative_call_stays_patchable_at_legacy_facade(self) -> None:
        settings = Settings(deepseek_model="deepseek-v4-pro")
        narrative = (
            "Kesimpulan Evidence: Evidence menunjukkan pelaksanaan telah dievaluasi "
            "secara berkala dan lokasi sumber masih harus diverifikasi oleh reviewer. "
            "Grade Aman: B"
        )
        response = {
            "status": "ok",
            "payload": {"choices": [{"message": {"content": narrative}}]},
        }
        with patch("app.smart_upload.call_chat_completion", return_value=response) as mocked:
            result = smart_upload.interpret_ai_narrative(
                settings,
                {
                    "file": {
                        "name": "evaluasi.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 10,
                        "preview_text": "laporan evaluasi berkala",
                    },
                    "preclassification": {},
                    "candidates": [],
                },
            )
        self.assertEqual(result["status"], "ok")
        self.assertIn("Evidence menunjukkan", result["evidence_analysis"]["summary"])
        body = mocked.call_args.args[1]
        self.assertEqual(body["model"], "deepseek-v4-pro")
        self.assertEqual(body["temperature"], 0)

    def test_duplicate_and_filename_support_remain_fail_closed(self) -> None:
        duplicate = smart_upload.build_duplicate_summary(
            [{"id": 2, "file_name": "a.pdf", "size_bytes": 10}], []
        )
        self.assertEqual(duplicate["status"], "exact")
        self.assertTrue(duplicate["blocks_upload"])
        self.assertEqual(duplicate["matches"][0]["review_id"], 2)

        with self.assertRaisesRegex(smart_upload.SmartUploadError, "Jenis aksi"):
            smart_upload.normalize_smart_upload_action("force_upload")
        self.assertEqual(
            smart_upload.sanitize_upload_filename("../Laporan evaluasi?.PDF"),
            "-Laporan evaluasi.PDF",
        )

    def test_facade_and_support_module_boundaries_are_enforced(self) -> None:
        smart_tree = ast.parse(Path(smart_upload.__file__).read_text(encoding="utf-8"))
        top_level_functions = {
            node.name
            for node in smart_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            top_level_functions,
            {"interpret_batch_narrative", "interpret_ai_narrative"},
        )
        top_level_classes = {
            node.name for node in smart_tree.body if isinstance(node, ast.ClassDef)
        }
        self.assertEqual(top_level_classes, {"SmartUploadService"})

        forbidden_by_module = {
            ai_normalization: {
                "app.config",
                "app.database",
                "app.legacy_ai_transport",
                "app.webdav_client",
            },
            ranking: {
                "app.config",
                "app.database",
                "app.legacy_ai_transport",
                "app.webdav_client",
            },
            upload_support: {"app.config", "app.database", "app.webdav_client"},
        }
        for module, forbidden in forbidden_by_module.items():
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            imports: set[str] = set()
            for node in tree.body:
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
            with self.subTest(module=module.__name__):
                self.assertTrue(forbidden.isdisjoint(imports))


if __name__ == "__main__":
    unittest.main()
