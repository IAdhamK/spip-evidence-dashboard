from __future__ import annotations

import hashlib
import json
import sqlite3
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.analysis.repository import AnalysisRepository
from app.analysis.contracts import DocumentIdentity
from app.analysis.facts import FactExtractionEngine
from app.analysis.visual_review import VisualPreviewError, extract_visual_preview
from app.database import Database


class VisualReviewWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "visual-review.db"))
        self.db.ensure_mapping()
        self.repository = AnalysisRepository(self.db)
        self.image_payload = b"\x89PNG\r\n\x1a\n" + b"visual-source"
        self.image_sha256 = hashlib.sha256(self.image_payload).hexdigest()
        document = self.repository.upsert_document(
            file_name="evidence.png",
            content_type="image/png",
            size_bytes=len(self.image_payload),
            sha256=hashlib.sha256(self.image_payload).hexdigest(),
            payload=self.image_payload,
            ttl_hours=72,
        )
        self.run_id = self.repository.create_run(
            document_id=int(document["id"]),
            analysis_mode="full_audit",
            pipeline_version="2.0.0",
            parser_version="2.0.0",
            rule_version="2026.2-draft",
            prompt_version="2026.2",
            provider="local_only",
            model=None,
            configuration_hash="a" * 64,
            external_ai_allowed=False,
        )
        self.ocr_text = "Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026."
        self.semantic_regions = [{
            "region_type": "picture",
            "semantic_hint": "stamp",
            "label": "Stempel pengesahan",
            "bbox": {"x": 0.7, "y": 0.75, "width": 0.2, "height": 0.15},
            "coordinate_space": "normalized_top_left",
            "detection_method": "structured_ooxml_pptx_v1",
            "requires_human_confirmation": True,
        }]
        self.repository.save_document_units(self.run_id, [{
            "unit_key": "image-1",
            "unit_type": "image",
            "ordinal": 1,
            "heading_path": [],
            "source_location": {"image": 1},
            "text": self.ocr_text,
            "status": "partial",
            "warnings": ["Teks OCR lokal tersedia, tetapi makna visual gambar belum diverifikasi."],
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_method": "local_test_v1",
                "ocr_confidence": 0.95,
                "visual_semantics_status": "pending_review_or_vision",
                "semantic_regions": self.semantic_regions,
            },
        }])
        self.unit = self.repository.get_document_unit(self.run_id, "image-1")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _decision(self, *, decision: str = "confirmed", reviewed_text: str = "") -> dict:
        return self.repository.save_visual_review_decision({
            "run_id": self.run_id,
            "unit_id": int(self.unit["id"]),
            "unit_key": "image-1",
            "review_kind": "visual_semantics",
            "decision": decision,
            "unit_text_sha256": self.unit["text_sha256"],
            "source_image_sha256": self.image_sha256,
            "reviewed_text": reviewed_text or self.ocr_text,
            "reviewed_text_sha256": hashlib.sha256(
                (reviewed_text or self.ocr_text).encode("utf-8")
            ).hexdigest(),
            "semantic_description": "Dokumen visual menunjukkan evaluasi SPIP.",
            "source_location": self.unit["source_location"],
            "evidence": {
                "ocr_method": "local_test_v1",
                "semantic_regions_sha256": hashlib.sha256(
                    json.dumps(
                        self.semantic_regions,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "reviewed_semantic_regions": [{
                    "region_type": "signature",
                    "semantic_hint": "signature",
                    "label": "Tanda tangan pejabat",
                    "bbox": {"x": 0.55, "y": 0.7, "width": 0.25, "height": 0.12},
                    "coordinate_space": "normalized_top_left",
                    "detection_method": "human_visual_region_v1",
                    "requires_human_confirmation": False,
                }],
            },
            "reviewer_id": "domain-reviewer",
            "reason": "Gambar dan teks sumber telah diperiksa langsung.",
        })

    def test_append_only_decisions_have_stable_snapshot_and_overlay(self) -> None:
        first = self._decision(decision="unsure")
        second = self._decision(decision="confirmed")
        self.assertEqual(second["supersedes_decision_id"], first["id"])
        snapshot = self.repository.visual_review_snapshot(self.run_id)
        self.assertEqual(snapshot["decision_count"], 1)
        self.assertEqual(snapshot["actionable_count"], 1)
        self.assertEqual(len(snapshot["checksum"]), 64)
        self.assertEqual(snapshot["decisions"][0]["review_kind"], "visual_semantics")

        raw_unit = {
            key: value
            for key, value in self.unit.items()
            if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
        }
        applied, summary = self.repository.apply_visual_review_decisions(
            self.run_id,
            [raw_unit],
        )
        self.assertEqual(summary["applied_count"], 1)
        self.assertEqual(applied[0]["status"], "processed")
        self.assertEqual(
            applied[0]["metadata"]["visual_semantics_status"],
            "human_verified",
        )
        self.assertEqual(
            applied[0]["metadata"]["visual_review"]["decision_id"],
            second["id"],
        )
        self.assertEqual(
            applied[0]["metadata"]["visual_semantic_description"],
            "Dokumen visual menunjukkan evaluasi SPIP.",
        )
        self.assertEqual(applied[0]["metadata"]["semantic_region_count"], 2)
        self.assertEqual(
            applied[0]["metadata"]["semantic_regions"][1]["detection_method"],
            "human_visual_region_v1",
        )
        facts, _ = FactExtractionEngine().run(
            DocumentIdentity("evidence.png", "image/png", len(self.image_payload), "b" * 64, "image"),
            applied,
        )
        self.assertEqual(
            facts[0]["source"]["source_location"]["semantic_regions"][0]["semantic_hint"],
            "stamp",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE visual_review_decisions SET reason = 'mutated' WHERE id = ?",
                    (second["id"],),
                )

    def test_overlay_rejects_stale_text_checksum(self) -> None:
        self._decision(decision="confirmed")
        raw_unit = {
            key: value
            for key, value in self.unit.items()
            if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
        }
        raw_unit["text"] = "Teks OCR berubah setelah keputusan dibuat."
        applied, summary = self.repository.apply_visual_review_decisions(
            self.run_id,
            [raw_unit],
        )
        self.assertEqual(summary["applied_count"], 0)
        self.assertEqual(summary["stale_count"], 1)
        self.assertEqual(applied[0]["status"], "partial")

    def test_overlay_rejects_stale_semantic_region_snapshot(self) -> None:
        self._decision(decision="confirmed")
        raw_unit = {
            key: value
            for key, value in self.unit.items()
            if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
        }
        raw_unit["metadata"] = dict(raw_unit["metadata"])
        raw_unit["metadata"]["semantic_regions"] = [{
            **self.semantic_regions[0],
            "bbox": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.15},
        }]
        applied, summary = self.repository.apply_visual_review_decisions(
            self.run_id,
            [raw_unit],
        )
        self.assertEqual(summary["applied_count"], 0)
        self.assertEqual(summary["stale_count"], 1)
        self.assertEqual(applied[0]["status"], "partial")

    def test_embedded_preview_is_checksum_bound_and_rejects_mismatch(self) -> None:
        archive_buffer = BytesIO()
        with ZipFile(archive_buffer, "w") as archive:
            archive.writestr("xl/media/image1.png", self.image_payload)
        run = {
            "file_name": "evidence.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        unit = {
            "source_location": {"part": "xl/media/image1.png"},
            "metadata": {"ocr_source_image_sha256": self.image_sha256},
        }
        preview, media_type, name = extract_visual_preview(
            run,
            unit,
            archive_buffer.getvalue(),
        )
        self.assertEqual(preview, self.image_payload)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(name, "image1.png")
        unit["metadata"]["ocr_source_image_sha256"] = "0" * 64
        with self.assertRaises(VisualPreviewError):
            extract_visual_preview(run, unit, archive_buffer.getvalue())

    def test_ocr_rescue_transcription_closes_required_unit_with_provenance(self) -> None:
        candidate_text = "Kebijakan SPIP ditetapkan pada tahun 2026."
        candidate_sha256 = hashlib.sha256(candidate_text.encode("utf-8")).hexdigest()
        self.repository.save_document_units(self.run_id, [{
            "unit_key": "page-7",
            "unit_type": "pdf_page",
            "ordinal": 2,
            "heading_path": [],
            "source_location": {"page": 7},
            "text": "",
            "status": "ocr_required",
            "warnings": ["Gambar membutuhkan OCR/vision."],
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_review_candidate_text": candidate_text,
                "ocr_review_candidate_text_sha256": candidate_sha256,
                "ocr_review_candidate_confidence": 0.41,
            },
        }])
        unit = self.repository.get_document_unit(self.run_id, "page-7")
        reviewed_text = "Kebijakan SPIP telah ditetapkan pada tahun 2026."
        decision = self.repository.save_visual_review_decision({
            "run_id": self.run_id,
            "unit_id": int(unit["id"]),
            "unit_key": "page-7",
            "review_kind": "ocr_rescue",
            "decision": "corrected",
            "unit_text_sha256": hashlib.sha256(b"").hexdigest(),
            "source_image_sha256": self.image_sha256,
            "reviewed_text": reviewed_text,
            "reviewed_text_sha256": hashlib.sha256(reviewed_text.encode()).hexdigest(),
            "semantic_description": "Halaman menetapkan kebijakan SPIP tahun 2026.",
            "source_location": unit["source_location"],
            "evidence": {
                "ocr_review_candidate_text_sha256": candidate_sha256,
                "ocr_review_candidate_confidence": 0.41,
            },
            "reviewer_id": "ocr-reviewer",
            "reason": "Transkripsi dibandingkan langsung dengan halaman sumber.",
        })
        raw_unit = {
            key: value
            for key, value in unit.items()
            if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
        }
        applied, summary = self.repository.apply_visual_review_decisions(
            self.run_id,
            [raw_unit],
        )
        self.assertEqual(summary["applied_count"], 1)
        self.assertEqual(applied[0]["status"], "processed")
        self.assertEqual(applied[0]["text"], reviewed_text)
        self.assertEqual(applied[0]["metadata"]["ocr_provider"], "human_review")
        self.assertEqual(
            applied[0]["metadata"]["ocr_rescue"]["decision_id"],
            decision["id"],
        )
        self.assertEqual(
            applied[0]["metadata"]["visual_semantics_status"],
            "human_verified",
        )

    def test_manual_ocr_rescue_without_candidate_is_queued_and_transcribed(self) -> None:
        self.repository.save_document_units(self.run_id, [{
            "unit_key": "page-8",
            "unit_type": "pdf_page",
            "ordinal": 3,
            "heading_path": [],
            "source_location": {"page": 8},
            "text": "",
            "status": "ocr_required",
            "warnings": ["Tesseract tidak menemukan teks."],
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_manual_review_required": True,
                "ocr_render_dpi": 288,
            },
        }])
        self.repository.update_run(
            self.run_id,
            status="review_required",
            coverage_status="partial",
            primary_blocked=True,
        )
        unit = self.repository.get_document_unit(self.run_id, "page-8")
        queued = next(
            item for item in self.repository.list_visual_review_items()
            if item["unit_key"] == "page-8"
        )
        self.assertEqual(queued["review_kind"], "ocr_rescue")
        self.assertEqual(queued["ocr_text"], "")

        reviewed_text = "Register risiko telah disahkan pada tahun 2026."
        self.repository.save_visual_review_decision({
            "run_id": self.run_id,
            "unit_id": int(unit["id"]),
            "unit_key": "page-8",
            "review_kind": "ocr_rescue",
            "decision": "corrected",
            "unit_text_sha256": hashlib.sha256(b"").hexdigest(),
            "source_image_sha256": self.image_sha256,
            "reviewed_text": reviewed_text,
            "reviewed_text_sha256": hashlib.sha256(reviewed_text.encode()).hexdigest(),
            "semantic_description": "Halaman menunjukkan register risiko tahun 2026.",
            "source_location": unit["source_location"],
            "evidence": {"ocr_review_candidate_text_sha256": None},
            "reviewer_id": "manual-ocr-reviewer",
            "reason": "Teks ditranskripsikan langsung dari halaman sumber.",
        })
        raw_unit = {
            key: value
            for key, value in unit.items()
            if key not in {"id", "run_id", "created_at", "text_sha256", "char_count"}
        }
        applied, summary = self.repository.apply_visual_review_decisions(
            self.run_id,
            [raw_unit],
        )
        self.assertEqual(summary["applied_count"], 1)
        self.assertEqual(applied[0]["status"], "processed")
        self.assertEqual(applied[0]["text"], reviewed_text)
        self.assertTrue(applied[0]["metadata"]["human_transcribed_ocr"])

    def test_pdf_preview_renders_exact_checksum_bound_page(self) -> None:
        run = {"file_name": "evidence.pdf", "content_type": "application/pdf"}
        unit = {
            "source_location": {"page": 7},
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_render_dpi": 288,
            },
        }

        def fake_render(arguments, **_kwargs):
            self.assertIn("7", arguments)
            self.assertIn("288", arguments)
            Path(f"{arguments[-1]}.png").write_bytes(self.image_payload)

        with patch("app.analysis.visual_review.shutil.which", return_value="pdftoppm"), patch(
            "app.analysis.visual_review.subprocess.run",
            side_effect=fake_render,
        ):
            preview, media_type, name = extract_visual_preview(run, unit, b"%PDF-test")
        self.assertEqual(preview, self.image_payload)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(name, "page-7.png")

    def test_pptx_preview_renders_exact_checksum_bound_slide(self) -> None:
        run = {
            "file_name": "evidence.pptx",
            "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
        unit = {
            "unit_type": "slide_visual",
            "source_location": {"slide": 4, "render": "full_slide"},
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_render_dpi": 144,
            },
        }
        with patch(
            "app.analysis.visual_review.render_pptx_slide",
            return_value=self.image_payload,
        ) as render:
            preview, media_type, name = extract_visual_preview(run, unit, b"pptx")
        self.assertEqual(preview, self.image_payload)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(name, "slide-4.png")
        self.assertEqual(render.call_args.args[1], 4)
        self.assertEqual(render.call_args.kwargs["dpi"], 144)

    def test_docx_preview_renders_exact_checksum_bound_visual_page(self) -> None:
        run = {
            "file_name": "evidence.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        unit = {
            "unit_type": "office_visual_page",
            "source_location": {"rendered_page": 6, "rendered_from": "docx"},
            "metadata": {
                "ocr_source_image_sha256": self.image_sha256,
                "ocr_render_dpi": 288,
            },
        }
        with patch(
            "app.analysis.visual_review.render_office_page",
            return_value=self.image_payload,
        ) as render:
            preview, media_type, name = extract_visual_preview(run, unit, b"docx")
        self.assertEqual(preview, self.image_payload)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(name, "docx-page-6.png")
        self.assertEqual(render.call_args.args[1:3], ("docx", 6))
        self.assertEqual(render.call_args.kwargs["dpi"], 288)


if __name__ == "__main__":
    unittest.main()
