from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from app.analysis.governance import synthetic_probe_png
from app.analysis.local_ocr import LocalOCRItem, LocalOCRResponse
from scripts.audit_document_corpus import analyse_documents, build_review_queue, inspect_archive, summarize


class FakeCorpusLocalOCR:
    name = "fake_local"

    def analyze_images(self, images):
        return LocalOCRResponse(items=[
            LocalOCRItem(
                unit_key=image["unit_key"],
                text="Dokumen evaluasi SPIP tahun 2026 telah selesai.",
                confidence=0.95,
                regions=[{
                    "text": "Dokumen evaluasi SPIP tahun 2026 telah selesai.",
                    "confidence": 0.95,
                    "bbox": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.1},
                    "coordinate_space": "normalized_top_left",
                }],
                languages=["ind"],
            )
            for image in images
        ], metrics={"attempt_count": len(images), "timeout_count": 0})


class CorpusAuditTests(unittest.TestCase):
    def _archive(self, members: dict[str, bytes]) -> tuple[TemporaryDirectory, Path]:
        directory = TemporaryDirectory()
        path = Path(directory.name) / "corpus.zip"
        with ZipFile(path, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        return directory, path

    def _inspect(self, path: Path):
        return inspect_archive(
            path,
            max_entries=100,
            max_total_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
            max_ratio=200,
        )

    def test_safe_archive_is_inventoryable_before_extraction(self) -> None:
        directory, path = self._archive({"dokumen/report.pdf": b"%PDF-1.4\n"})
        self.addCleanup(directory.cleanup)
        report, checked = self._inspect(path)
        self.assertTrue(report["safe_to_extract"])
        self.assertEqual(report["extension_counts"], {".pdf": 1})
        self.assertEqual(checked[0][1], "dokumen/report.pdf")

    def test_path_traversal_and_casefold_collision_block_extraction(self) -> None:
        directory, path = self._archive(
            {"../escape.pdf": b"%PDF-1.4\n", "Folder/A.pdf": b"a", "folder/a.pdf": b"b"}
        )
        self.addCleanup(directory.cleanup)
        report, _ = self._inspect(path)
        self.assertFalse(report["safe_to_extract"])
        self.assertTrue(any("Path tidak aman" in error for error in report["errors"]))
        self.assertTrue(any("case-insensitive" in error for error in report["errors"]))

    def test_review_queue_is_diverse_and_deduplicated_by_checksum(self) -> None:
        results = []
        counts = {"xlsx": 24, "pdf": 22, "docx": 6, "image": 12}
        serial = 0
        for kind, count in counts.items():
            for index in range(count):
                serial += 1
                results.append(
                    {
                        "document_id": f"doc-{serial}",
                        "file_name": f"{kind}/{index}.{kind}",
                        "sha256": f"{serial:064x}",
                        "file_kind": kind,
                        "size_bytes": serial,
                        "parser_status": "partial",
                        "coverage_status": "partial",
                        "coverage": {"ocr_required_units": 1, "partial_units": 0},
                        "inventory": {},
                        "unit_metrics": {},
                    }
                )
        results.append({**results[0], "document_id": "duplicate", "file_name": "copy.xlsx"})
        queue = build_review_queue(results)
        self.assertEqual(len(queue), 50)
        self.assertEqual(len({item["sha256"] for item in queue}), 50)
        self.assertEqual(
            {kind: sum(item["file_kind"] == kind for item in queue) for kind in counts},
            {"xlsx": 20, "pdf": 18, "docx": 6, "image": 6},
        )

    def test_local_ocr_reprocessing_updates_coverage_and_summary(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        payload = synthetic_probe_png()
        path = root / "scan.png"
        path.write_bytes(payload)
        entry = ZipInfo("scan.png")
        entry.file_size = len(payload)
        with patch(
            "scripts.audit_document_corpus.configured_local_ocr_provider",
            return_value=FakeCorpusLocalOCR(),
        ):
            results, _ = analyse_documents(root, [(entry, "scan.png")], local_ocr=True)
        self.assertEqual(results[0]["coverage_status"], "partial")
        self.assertEqual(results[0]["local_ocr"]["processed_units"], 1)
        self.assertEqual(results[0]["local_ocr"]["region_count"], 1)
        self.assertEqual(results[0]["local_ocr"]["visual_semantics_pending"], 1)
        summary = summarize({"safe_to_extract": True}, results)
        self.assertEqual(summary["local_ocr_processed_unit_count"], 1)
        self.assertEqual(summary["local_ocr_remaining_unit_count"], 0)
        self.assertEqual(summary["local_ocr_visual_semantics_pending_count"], 1)
        self.assertEqual(summary["local_ocr_attempt_count"], 1)
        self.assertEqual(summary["local_ocr_timeout_count"], 0)

        legacy_ocr = dict(results[0]["local_ocr"])
        legacy_ocr.pop("tiled_processed_units", None)
        legacy_ocr["warnings"] = [
            "Tesseract page-1 memakai 4 tile karena raster besar.",
            "Tesseract page-2 memakai 2 tile karena raster besar.",
        ]
        legacy_summary = summarize(
            {"safe_to_extract": True},
            [{**results[0], "local_ocr": legacy_ocr}],
        )
        self.assertEqual(legacy_summary["local_ocr_tiled_processed_unit_count"], 2)

    def test_office_reprocessing_runs_visual_engine_without_native_ocr_gap(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        path = root / "evidence.xlsx"
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                  <sheets><sheet name="Evaluasi" sheetId="1" r:id="rId1"/></sheets>
                  </workbook>""",
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
                  </Relationships>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData><row r="1"><c r="A1" t="inlineStr"><is>
                  <t>Evaluasi SPIP selesai</t></is></c></row></sheetData></worksheet>""",
            )
        entry = ZipInfo("evidence.xlsx")
        entry.file_size = path.stat().st_size
        observed: dict = {}

        class FakeOfficeVisualEngine:
            def __init__(self, _provider, _local_provider):
                pass

            def run(self, _identity, _payload, units, **kwargs):
                observed.update(kwargs)
                expanded = [*units, {
                    "unit_key": "office-visual-page-1",
                    "unit_type": "office_visual_page",
                    "ordinal": len(units) + 1,
                    "heading_path": [],
                    "source_location": {"rendered_page": 1, "sheet": "Evaluasi"},
                    "text": "Evaluasi SPIP selesai",
                    "status": "partial",
                    "warnings": ["Makna visual menunggu reviewer."],
                    "metadata": {"requires_visual_verification": True},
                }]
                return expanded, SimpleNamespace(
                    status=SimpleNamespace(value="partial"),
                    warnings=[],
                    output={
                        "local_ocr_processed": 1,
                        "ocr_region_count": 1,
                        "visual_semantics_pending": 1,
                        "ocr_review_candidates": 0,
                        "pdf_retry_required": 0,
                        "pdf_retry_processed": 0,
                        "pdf_retry_deferred": 0,
                        "office_total_pages": 1,
                        "office_pages_scheduled": 1,
                        "office_pages_deferred": 0,
                        "office_adaptive_low_dpi_pages": 1,
                    },
                )

        with (
            patch(
                "scripts.audit_document_corpus.configured_local_ocr_provider",
                return_value=FakeCorpusLocalOCR(),
            ),
            patch(
                "scripts.audit_document_corpus.VisualOCREngine",
                FakeOfficeVisualEngine,
            ),
        ):
            results, _ = analyse_documents(
                root,
                [(entry, "evidence.xlsx")],
                local_ocr=True,
                office_render_max_pages=7,
            )

        self.assertTrue(observed["office_page_expansion_enabled"])
        self.assertEqual(observed["office_render_max_pages"], 7)
        self.assertEqual(results[0]["local_ocr"]["office_total_pages"], 1)
        self.assertEqual(
            results[0]["unit_metrics"]["unit_type_counts"]["office_visual_page"],
            1,
        )
        summary = summarize({"safe_to_extract": True}, results)
        self.assertEqual(summary["office_render_document_count"], 1)
        self.assertEqual(summary["office_rendered_page_count"], 1)
        self.assertEqual(summary["office_scheduled_page_count"], 1)
        self.assertEqual(summary["office_adaptive_low_dpi_page_count"], 1)
        self.assertEqual(summary["office_render_failure_count"], 0)

    def test_reprocessing_checkpoint_reuses_exact_profile_without_duplicate_append(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        payload = synthetic_probe_png()
        path = root / "scan.png"
        path.write_bytes(payload)
        entry = ZipInfo("scan.png")
        entry.file_size = len(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        baseline = {
            "document_id": "old-id",
            "file_name": "scan.png",
            "sha256": checksum,
            "size_bytes": len(payload),
            "content_type": "image/png",
            "file_kind": "image",
            "intake_status": "completed",
            "security_findings": [],
            "parser_status": "completed",
            "coverage_status": "completed",
            "coverage": {"coverage_status": "complete", "ocr_required_units": 0},
            "unit_status_counts": {"processed": 1},
            "unit_metrics": {},
            "inventory": {},
            "extracted_char_count": 10,
            "template_ledger": {},
            "sensitive_signal_counts": {},
            "failure_reasons": [],
            "local_ocr": None,
        }
        checkpoint = root / "checkpoint.jsonl"
        first, _ = analyse_documents(
            root,
            [(entry, "scan.png")],
            baseline_records={(checksum, "scan.png"): baseline},
            reprocess_kinds={"xlsx"},
            checkpoint_path=checkpoint,
        )
        self.assertEqual(first[0]["reprocessing"]["source"], "baseline")
        self.assertTrue(first[0]["audit_profile"])
        checkpoint_record = json.loads(checkpoint.read_text(encoding="utf-8"))

        second, _ = analyse_documents(
            root,
            [(entry, "scan.png")],
            checkpoint_records={(checksum, "scan.png"): checkpoint_record},
            checkpoint_path=checkpoint,
        )
        self.assertEqual(second[0]["reprocessing"]["source"], "checkpoint")
        self.assertEqual(len(checkpoint.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
