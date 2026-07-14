from __future__ import annotations

import hashlib
import subprocess
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.facts import FactExtractionEngine
from app.analysis.governance import synthetic_probe_png
from app.analysis.local_ocr import (
    LocalOCRItem,
    LocalOCRResponse,
    TesseractLocalOCRProvider,
    _parse_psm_modes,
    _parse_tesseract_tsv,
)
from app.analysis.office_render import OfficeRenderError
from app.analysis.provider import VisionOCRItem, VisionOCRResponse
from app.analysis.vision import (
    VisualCancellationRequested,
    VisualCheckpointError,
    VisualOCREngine,
)


class HighConfidenceLocalOCR:
    name = "test_local"

    def analyze_images(self, images):
        return LocalOCRResponse(items=[
            LocalOCRItem(
                unit_key=images[0]["unit_key"],
                text="Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                confidence=0.96,
                regions=[{
                    "text": "Kebijakan SPIP telah ditetapkan dan dievaluasi tahun 2026.",
                    "confidence": 0.96,
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.1},
                    "coordinate_space": "normalized_top_left",
                }],
                method="local_test_v1",
                languages=["ind"],
            )
        ])


class AllHighConfidenceLocalOCR:
    name = "test_local_all"

    def analyze_images(self, images):
        return LocalOCRResponse(items=[
            LocalOCRItem(
                unit_key=image["unit_key"],
                text=f"Halaman visual {image['unit_key']} telah dievaluasi tahun 2026.",
                confidence=0.96,
                method="local_test_all_v1",
                languages=["ind"],
            )
            for image in images
        ])


class LowConfidenceLocalOCR:
    name = "test_local_low"

    def analyze_images(self, images):
        return LocalOCRResponse(items=[
            LocalOCRItem(
                unit_key=images[0]["unit_key"],
                text="teks tidak pasti",
                confidence=0.2,
                method="local_test_v1",
            )
        ])


class TiledHighConfidenceLocalOCR(HighConfidenceLocalOCR):
    def analyze_images(self, images):
        response = super().analyze_images(images)
        for item in response.items:
            item.method = "local_tesseract_tiled_v1"
        return response


class BudgetAwareLocalOCR(AllHighConfidenceLocalOCR):
    max_image_pixels = 10_000
    max_tiles = 1

    def __init__(self):
        self.observed_images: list[dict] = []

    def analyze_images(self, images):
        self.observed_images.extend(images)
        return super().analyze_images(images)


class EmptyLocalOCR:
    name = "test_local_empty"

    def analyze_images(self, images):
        return LocalOCRResponse(items=[], warnings=["Local OCR timeout pada image-1."])


class AdaptivePDFLocalOCR:
    name = "test_adaptive_pdf"

    def analyze_images(self, images):
        items = []
        for image in images:
            dpi = int(image.get("render_dpi") or 0)
            confidence = 0.92 if dpi == 288 else 0.25
            items.append(LocalOCRItem(
                unit_key=image["unit_key"],
                text="Register risiko telah dievaluasi secara berkala tahun 2026.",
                confidence=confidence,
                method=f"local_test_{dpi}_dpi",
                languages=["ind"],
            ))
        return LocalOCRResponse(items=items)


class ExternalFallbackVision:
    def analyze_images(self, images):
        return VisionOCRResponse(items=[
            VisionOCRItem(
                unit_key=images[0]["unit_key"],
                ocr_text="Kebijakan SPIP telah ditetapkan tahun 2026.",
                confidence=0.91,
            )
        ])


class LocalOCREngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = DocumentIdentity(
            "scan.png", "image/png", 100, "image-sha", "image"
        )
        self.payload = synthetic_probe_png()
        self.units = [{
            "unit_key": "image-1",
            "unit_type": "image",
            "ordinal": 1,
            "heading_path": [],
            "source_location": {"image": 1},
            "text": "",
            "status": "ocr_required",
            "warnings": ["Gambar membutuhkan OCR/vision."],
            "metadata": {},
        }]

    def test_local_ocr_is_primary_and_preserves_bounding_regions(self) -> None:
        units, result = VisualOCREngine(None, HighConfidenceLocalOCR()).run(
            self.identity,
            self.payload,
            self.units,
            local_min_confidence=0.45,
        )
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(result.output["local_ocr_processed"], 1)
        self.assertEqual(result.output["external_vision_processed"], 0)
        self.assertEqual(result.output["ocr_region_count"], 1)
        self.assertEqual(result.output["visual_semantics_pending"], 1)
        self.assertEqual(units[0]["status"], "partial")
        self.assertEqual(units[0]["metadata"]["ocr_provider"], "local")
        self.assertEqual(units[0]["metadata"]["ocr_method"], "local_test_v1")
        self.assertEqual(
            units[0]["metadata"]["ocr_regions"][0]["coordinate_space"],
            "normalized_top_left",
        )
        self.assertEqual(len(units[0]["metadata"]["ocr_source_image_sha256"]), 64)

        units[0]["id"] = 1
        facts, fact_result = FactExtractionEngine().run(self.identity, units)
        self.assertEqual(facts, [])
        self.assertEqual(fact_result.output["visual_semantics_blocked_count"], 1)
        self.assertTrue(any("makna visual" in warning for warning in fact_result.warnings))

    def test_visual_engine_counts_tiled_local_ocr(self) -> None:
        _units, result = VisualOCREngine(None, TiledHighConfidenceLocalOCR()).run(
            self.identity,
            self.payload,
            self.units,
        )
        self.assertEqual(result.output["local_ocr_processed"], 1)
        self.assertEqual(result.output["local_ocr_tiled_processed"], 1)

    def test_pdf_low_confidence_is_rerendered_at_higher_dpi(self) -> None:
        identity = DocumentIdentity(
            "register.pdf", "application/pdf", 100, "pdf-sha", "pdf"
        )
        units = [{
            **self.units[0],
            "unit_key": "page-7",
            "unit_type": "pdf_page",
            "source_location": {"page": 7},
        }]
        engine = VisualOCREngine(None, AdaptivePDFLocalOCR())
        base_image = {
            "unit_key": "page-7",
            "mime_type": "image/png",
            "payload": b"base-render",
            "render_dpi": 144,
        }
        retry_image = {
            "unit_key": "page-7",
            "mime_type": "image/png",
            "payload": b"retry-render",
            "render_dpi": 288,
        }
        with patch.object(
            engine,
            "_prepare_images",
            side_effect=[[base_image], [retry_image]],
        ) as prepare:
            updated, result = engine.run(
                identity,
                b"pdf",
                units,
                local_min_confidence=0.45,
                pdf_dpi=144,
                pdf_retry_dpi=288,
            )
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(result.output["pdf_retry_required"], 1)
        self.assertEqual(result.output["pdf_retry_processed"], 1)
        self.assertEqual(updated[0]["status"], "processed")
        self.assertEqual(updated[0]["metadata"]["ocr_render_dpi"], 288)
        self.assertEqual(updated[0]["metadata"]["ocr_method"], "local_test_288_dpi")
        self.assertFalse(any("ditolak karena confidence" in item for item in result.warnings))

    def test_pdf_retry_budget_defers_excess_units_fail_closed(self) -> None:
        identity = DocumentIdentity(
            "register.pdf", "application/pdf", 100, "pdf-sha", "pdf"
        )
        units = [
            {
                **self.units[0],
                "unit_key": f"page-{page}",
                "unit_type": "pdf_page",
                "source_location": {"page": page},
            }
            for page in (1, 2)
        ]
        base_images = [
            {
                "unit_key": f"page-{page}",
                "mime_type": "image/png",
                "payload": f"base-{page}".encode(),
                "render_dpi": 144,
            }
            for page in (1, 2)
        ]
        retry_image = {
            "unit_key": "page-1",
            "mime_type": "image/png",
            "payload": b"retry-1",
            "render_dpi": 288,
        }
        engine = VisualOCREngine(None, AdaptivePDFLocalOCR())
        with patch.object(
            engine,
            "_prepare_images",
            side_effect=[base_images, [retry_image]],
        ):
            updated, result = engine.run(
                identity,
                b"pdf",
                units,
                pdf_retry_max_units=1,
            )
        self.assertEqual(result.output["pdf_retry_required"], 1)
        self.assertEqual(result.output["pdf_retry_deferred"], 1)
        self.assertEqual(updated[0]["status"], "processed")
        self.assertEqual(updated[1]["status"], "ocr_required")
        self.assertEqual(result.output["ocr_review_candidates"], 1)
        self.assertEqual(
            updated[1]["metadata"]["ocr_review_candidate_text_sha256"],
            hashlib.sha256(
                updated[1]["metadata"]["ocr_review_candidate_text"].encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(updated[1]["metadata"]["ocr_render_dpi"], 144)
        self.assertTrue(any("ditunda" in item for item in result.warnings))

    def test_low_confidence_local_ocr_uses_external_fallback(self) -> None:
        units, result = VisualOCREngine(
            ExternalFallbackVision(), LowConfidenceLocalOCR()
        ).run(
            self.identity,
            self.payload,
            self.units,
            local_min_confidence=0.45,
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(result.output["local_ocr_processed"], 0)
        self.assertEqual(result.output["external_vision_processed"], 1)
        self.assertEqual(units[0]["metadata"]["ocr_provider"], "external_vision")
        self.assertTrue(any("confidence" in warning for warning in result.warnings))

    def test_local_timeout_without_approved_external_provider_remains_partial(self) -> None:
        units, result = VisualOCREngine(None, EmptyLocalOCR()).run(
            self.identity,
            self.payload,
            self.units,
        )
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(units[0]["status"], "ocr_required")
        self.assertEqual(result.output["ocr_processed"], 0)
        self.assertTrue(units[0]["metadata"]["ocr_manual_review_required"])
        self.assertEqual(
            units[0]["metadata"]["ocr_source_image_sha256"],
            hashlib.sha256(self.payload).hexdigest(),
        )
        self.assertTrue(any("timeout" in warning.lower() for warning in result.warnings))

    def test_pptx_full_slide_render_routes_to_local_ocr_and_visual_review(self) -> None:
        pptx = BytesIO()
        with ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide2.xml", "<slide/>")
        identity = DocumentIdentity(
            "evidence.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            len(pptx.getvalue()),
            "pptx-sha",
            "pptx",
        )
        units = [{
            **self.units[0],
            "unit_key": "slide-visual-2",
            "unit_type": "slide_visual",
            "source_location": {"slide": 2, "part": "ppt/slides/slide2.xml", "render": "full_slide"},
        }]
        with patch("app.analysis.vision.render_pptx_slide", return_value=self.payload) as render:
            updated, result = VisualOCREngine(None, HighConfidenceLocalOCR()).run(
                identity,
                pptx.getvalue(),
                units,
                pdf_dpi=144,
                office_rendering_enabled=True,
            )
        render.assert_called_once()
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(result.output["local_ocr_processed"], 1)
        self.assertEqual(result.output["visual_semantics_pending"], 1)
        self.assertEqual(updated[0]["status"], "partial")
        self.assertEqual(
            updated[0]["metadata"]["ocr_render_method"],
            "libreoffice_impress_to_pdf_to_png_v1",
        )
        self.assertEqual(updated[0]["metadata"]["visual_semantics_status"], "pending_review_or_vision")

    def test_disabled_pptx_full_slide_renderer_remains_ocr_required(self) -> None:
        pptx = BytesIO()
        with ZipFile(pptx, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<slide/>")
        identity = DocumentIdentity("visual.pptx", None, len(pptx.getvalue()), "pptx-sha", "pptx")
        units = [{
            **self.units[0],
            "unit_key": "slide-visual-1",
            "unit_type": "slide_visual",
            "source_location": {"slide": 1, "part": "ppt/slides/slide1.xml", "render": "full_slide"},
        }]
        with patch("app.analysis.vision.render_pptx_slide") as render:
            updated, result = VisualOCREngine(None, HighConfidenceLocalOCR()).run(
                identity,
                pptx.getvalue(),
                units,
                office_rendering_enabled=False,
            )
        render.assert_not_called()
        self.assertEqual(updated[0]["status"], "ocr_required")
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertTrue(any("dinonaktifkan" in warning for warning in result.warnings))

    def test_docx_full_document_expands_pages_and_routes_each_to_visual_review(self) -> None:
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("word/document.xml", "<document/>")
        identity = DocumentIdentity("evidence.docx", None, len(payload.getvalue()), "docx-sha", "docx")
        native_units = [{
            "unit_key": "block-1",
            "unit_type": "paragraph",
            "ordinal": 1,
            "heading_path": [],
            "source_location": {"part": "word/document.xml", "block": 1},
            "text": "Evaluasi SPIP tahun 2026.",
            "status": "processed",
            "warnings": [],
            "metadata": {},
        }]
        with (
            patch("app.analysis.vision.convert_office_to_pdf", return_value=b"office-pdf") as convert,
            patch("app.analysis.vision.office_pdf_page_count", return_value=2),
            patch("app.analysis.vision.render_pdf_page", return_value=self.payload) as render,
        ):
            updated, result = VisualOCREngine(None, AllHighConfidenceLocalOCR()).run(
                identity,
                payload.getvalue(),
                native_units,
                office_render_max_pages=4,
            )
        convert.assert_called_once()
        self.assertEqual(render.call_count, 2)
        pages = [unit for unit in updated if unit["unit_type"] == "office_visual_page"]
        self.assertEqual(len(pages), 2)
        self.assertEqual([unit["source_location"]["rendered_page"] for unit in pages], [1, 2])
        self.assertTrue(all(unit["status"] == "partial" for unit in pages))
        self.assertTrue(all(
            unit["metadata"]["visual_semantics_status"] == "pending_review_or_vision"
            for unit in pages
        ))
        self.assertEqual(result.output["office_total_pages"], 2)
        self.assertEqual(result.output["office_pages_deferred"], 0)

    def test_xlsx_full_document_page_budget_records_deferred_pages_fail_closed(self) -> None:
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
        identity = DocumentIdentity("large.xlsx", None, len(payload.getvalue()), "xlsx-sha", "xlsx")
        native_units = [
            {
                "unit_key": f"sheet-{index}",
                "unit_type": "sheet",
                "ordinal": index,
                "heading_path": [name],
                "source_location": {
                    "sheet": name,
                    "sheet_index": index,
                    "hidden": False,
                },
                "text": name,
                "status": "processed",
                "warnings": [],
                "metadata": {},
            }
            for index, name in enumerate(("Evaluasi", "Tindak Lanjut", "Monitoring"), start=1)
        ]
        with (
            patch("app.analysis.vision.convert_office_to_pdf", return_value=b"office-pdf"),
            patch("app.analysis.vision.office_pdf_page_count", return_value=3),
            patch("app.analysis.vision.render_pdf_page", return_value=self.payload) as render,
        ):
            updated, result = VisualOCREngine(None, AllHighConfidenceLocalOCR()).run(
                identity,
                payload.getvalue(),
                native_units,
                office_render_max_pages=1,
            )
        pages = [unit for unit in updated if unit["unit_type"] == "office_visual_page"]
        self.assertEqual([unit["status"] for unit in pages], ["partial", "pending", "pending"])
        self.assertEqual(
            [unit["source_location"]["sheet"] for unit in pages],
            ["Evaluasi", "Tindak Lanjut", "Monitoring"],
        )
        self.assertEqual(render.call_count, 1)
        self.assertEqual(result.output["office_pages_scheduled"], 1)
        self.assertEqual(result.output["office_pages_deferred"], 2)
        self.assertTrue(any("page budget" in warning for warning in result.warnings))

    def test_xlsx_full_document_excludes_hidden_sheet_pdf_pages(self) -> None:
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
        identity = DocumentIdentity("hidden.xlsx", None, len(payload.getvalue()), "xlsx-sha", "xlsx")
        native_units = [
            {
                "unit_key": f"sheet-{index}",
                "unit_type": "sheet",
                "ordinal": index,
                "heading_path": [name],
                "source_location": {
                    "sheet": name,
                    "sheet_index": index,
                    "hidden": hidden,
                },
                "text": name,
                "status": "processed",
                "warnings": [],
                "metadata": {},
            }
            for index, (name, hidden) in enumerate(
                (("Evaluasi", False), ("Referensi", True), ("Tindak Lanjut", False)),
                start=1,
            )
        ]
        with (
            patch("app.analysis.vision.convert_office_to_pdf", return_value=b"office-pdf"),
            patch("app.analysis.vision.office_pdf_page_count", return_value=3),
            patch("app.analysis.vision.render_pdf_page", return_value=self.payload) as render,
        ):
            updated, result = VisualOCREngine(None, AllHighConfidenceLocalOCR()).run(
                identity,
                payload.getvalue(),
                native_units,
                office_render_max_pages=3,
            )
        pages = [unit for unit in updated if unit["unit_type"] == "office_visual_page"]
        self.assertEqual(
            [unit["source_location"]["rendered_page"] for unit in pages],
            [1, 3],
        )
        self.assertEqual(
            [unit["source_location"]["sheet"] for unit in pages],
            ["Evaluasi", "Tindak Lanjut"],
        )
        self.assertEqual(render.call_count, 2)
        self.assertEqual(result.output["office_pdf_total_pages"], 3)
        self.assertEqual(result.output["office_total_pages"], 2)
        self.assertEqual(result.output["office_hidden_pages_excluded"], 1)
        self.assertEqual(result.output["office_pages_deferred"], 0)

    def test_xlsx_oversized_page_is_rerendered_lower_dpi_with_same_locator(self) -> None:
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("xl/workbook.xml", "<workbook/>")
        identity = DocumentIdentity(
            "wide.xlsx", None, len(payload.getvalue()), "xlsx-sha", "xlsx"
        )
        native_units = [{
            "unit_key": "sheet-1",
            "unit_type": "sheet",
            "ordinal": 1,
            "heading_path": ["Evaluasi"],
            "source_location": {
                "sheet": "Evaluasi",
                "sheet_index": 1,
                "hidden": False,
            },
            "text": "Evaluasi",
            "status": "processed",
            "warnings": [],
            "metadata": {},
        }]

        def rendered_page(_pdf, _page, *, dpi, timeout_seconds):
            del timeout_seconds
            image = Image.new(
                "RGB",
                (200, 100) if int(dpi) >= 144 else (100, 50),
                "white",
            )
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

        provider = BudgetAwareLocalOCR()
        with (
            patch("app.analysis.vision.convert_office_to_pdf", return_value=b"office-pdf"),
            patch("app.analysis.vision.office_pdf_page_count", return_value=1),
            patch("app.analysis.vision.render_pdf_page", side_effect=rendered_page) as render,
        ):
            updated, result = VisualOCREngine(None, provider).run(
                identity,
                payload.getvalue(),
                native_units,
                pdf_dpi=144,
                office_render_max_pages=1,
            )

        page = next(unit for unit in updated if unit["unit_type"] == "office_visual_page")
        observed = provider.observed_images[0]
        self.assertEqual(render.call_count, 2)
        self.assertLess(observed["render_dpi"], 144)
        self.assertEqual(observed["adaptive_render_from_dpi"], 144)
        self.assertEqual(page["source_location"]["rendered_page"], 1)
        self.assertEqual(page["source_location"]["sheet"], "Evaluasi")
        self.assertEqual(page["metadata"]["ocr_render_dpi"], observed["render_dpi"])
        self.assertEqual(page["metadata"]["ocr_adaptive_render_from_dpi"], 144)
        self.assertEqual(result.output["office_adaptive_low_dpi_pages"], 1)
        self.assertTrue(any("dirender adaptif" in warning for warning in result.warnings))

    def test_office_converter_failure_creates_blocking_visual_unit(self) -> None:
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("word/document.xml", "<document/>")
        identity = DocumentIdentity("broken.docx", None, len(payload.getvalue()), "docx-sha", "docx")
        with patch(
            "app.analysis.vision.convert_office_to_pdf",
            side_effect=OfficeRenderError("converter gagal"),
        ):
            updated, result = VisualOCREngine(None, AllHighConfidenceLocalOCR()).run(
                identity,
                payload.getvalue(),
                [],
            )
        blocker = next(unit for unit in updated if unit["unit_type"] == "office_visual_document")
        self.assertEqual(blocker["status"], "ocr_required")
        self.assertIn("converter gagal", blocker["metadata"]["office_render_error"])
        self.assertEqual(result.status, EngineStatus.PARTIAL)

    def test_tesseract_tsv_becomes_normalized_line_regions(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t95.0\tSPIP\n"
            "5\t1\t1\t1\t1\t2\t45\t12\t35\t10\t90.0\t2026\n"
        )
        item = _parse_tesseract_tsv("image-1", tsv, self.payload, ["ind", "eng"])
        self.assertEqual(item.text, "SPIP 2026")
        self.assertGreater(item.confidence, 0.9)
        self.assertEqual(len(item.regions), 1)
        bbox = item.regions[0]["bbox"]
        self.assertTrue(all(0 <= bbox[key] <= 1 for key in bbox))

    def test_tesseract_literal_quote_does_not_swallow_following_tsv_rows(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t40.0\t\"Tele\n"
            "5\t1\t2\t1\t1\t1\t50\t30\t80\t10\t90.0\tEvaluasi\n"
        )
        item = _parse_tesseract_tsv("page-1", tsv, self.payload, ["ind"])
        self.assertEqual(item.text, '"Tele Evaluasi')
        self.assertNotIn("5 1 2", item.text)
        self.assertEqual(len(item.regions), 2)

    def test_tesseract_retries_weak_layout_with_alternative_psm(self) -> None:
        low_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t20.0\tteks\n"
        )
        high_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t96.0\tSPIP\n"
            "5\t1\t1\t1\t1\t2\t45\t12\t35\t10\t94.0\t2026\n"
        )
        results = [
            subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=low_tsv, stderr=""),
            subprocess.CompletedProcess([], 0, stdout=high_tsv, stderr=""),
        ]
        with patch("app.analysis.local_ocr.subprocess.run", side_effect=results) as run:
            provider = TesseractLocalOCRProvider(
                "tesseract",
                "ind+eng",
                10,
                minimum_confidence=0.45,
                psm_modes="6,3,11",
            )
            response = provider.analyze_images([{
                "unit_key": "image-1",
                "mime_type": "image/png",
                "payload": self.payload,
            }])
        self.assertEqual(response.items[0].method, "local_tesseract_psm_3_v1")
        self.assertGreater(response.items[0].confidence, 0.9)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(_parse_psm_modes("6,3,3,99,abc"), [6, 3])

    def test_tesseract_uses_alpha_mask_after_raw_layouts_remain_weak(self) -> None:
        image = Image.new("RGBA", (227, 64), (255, 255, 255, 0))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        low_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t20.0\tteks\n"
        )
        high_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t300\t40\t96.0\tPENGIDENTIFIKASIAN\n"
            "5\t1\t1\t1\t1\t2\t320\t12\t180\t40\t94.0\tRISIKO\n"
        )
        results = [
            subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
            *[
                subprocess.CompletedProcess([], 0, stdout=low_tsv, stderr="")
                for _ in range(3)
            ],
            subprocess.CompletedProcess([], 0, stdout=high_tsv, stderr=""),
        ]
        with patch("app.analysis.local_ocr.subprocess.run", side_effect=results) as run:
            provider = TesseractLocalOCRProvider(
                "tesseract",
                "ind+eng",
                10,
                minimum_confidence=0.45,
                psm_modes="6,3,11",
                preprocessing_enabled=True,
            )
            response = provider.analyze_images([{
                "unit_key": "embedded-image-1",
                "mime_type": "image/png",
                "payload": buffer.getvalue(),
            }])
        self.assertEqual(len(response.items), 1)
        self.assertEqual(
            response.items[0].method,
            "local_tesseract_alpha_mask_psm_6_v2",
        )
        self.assertGreater(response.items[0].confidence, 0.9)
        self.assertEqual(run.call_count, 5)

    def test_tesseract_tiles_large_raster_and_remaps_regions(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        high_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t10\t30\t10\t96.0\tSPIP\n"
        )
        results = [
            subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=high_tsv, stderr=""),
            subprocess.CompletedProcess([], 0, stdout=high_tsv, stderr=""),
        ]
        with patch("app.analysis.local_ocr.subprocess.run", side_effect=results) as run:
            provider = TesseractLocalOCRProvider(
                "tesseract",
                "ind+eng",
                10,
                minimum_confidence=0.45,
                psm_modes="6,3,11",
                max_image_pixels=10_000,
                max_tiles=4,
            )
            response = provider.analyze_images([{
                "unit_key": "large-sheet",
                "mime_type": "image/png",
                "payload": buffer.getvalue(),
            }])
        self.assertEqual(run.call_count, 3)
        self.assertEqual(response.items[0].method, "local_tesseract_tiled_v1")
        self.assertEqual(len(response.items[0].regions), 2)
        self.assertLess(response.items[0].regions[0]["bbox"]["x"], 0.5)
        self.assertGreater(response.items[0].regions[1]["bbox"]["x"], 0.5)
        self.assertTrue(any("memakai 2 tile" in warning for warning in response.warnings))

    def test_tesseract_tile_budget_excess_remains_fail_closed(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        with patch(
            "app.analysis.local_ocr.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
        ) as run:
            provider = TesseractLocalOCRProvider(
                "tesseract",
                "ind+eng",
                10,
                max_image_pixels=10_000,
                max_tiles=1,
            )
            response = provider.analyze_images([{
                "unit_key": "too-large",
                "mime_type": "image/png",
                "payload": buffer.getvalue(),
            }])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(response.items)
        self.assertTrue(any("melebihi budget 1" in warning for warning in response.warnings))

    def test_tesseract_attempt_budget_stops_alternative_psm_fail_closed(self) -> None:
        low_tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t1\t1\t1\t1\t10\t12\t30\t10\t20.0\tteks\n"
        )
        results = [
            subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=low_tsv, stderr=""),
        ]
        with patch("app.analysis.local_ocr.subprocess.run", side_effect=results) as run:
            provider = TesseractLocalOCRProvider(
                "tesseract",
                "ind+eng",
                10,
                psm_modes="6,3,11",
                preprocessing_enabled=False,
                max_attempts_per_unit=1,
            )
            response = provider.analyze_images([{
                "unit_key": "pathological-page",
                "mime_type": "image/png",
                "payload": self.payload,
            }])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(response.metrics["attempt_count"], 1)
        self.assertEqual(response.metrics["budget_exhausted_unit_count"], 1)
        self.assertEqual(
            response.metrics["budget_exhaustion_reasons"]["pathological-page"],
            "unit_attempt_budget_exhausted",
        )
        self.assertEqual(response.items[0].confidence, 0.2)
        self.assertTrue(any("berhenti aman" in warning for warning in response.warnings))

    def test_tesseract_expired_document_deadline_skips_subprocess(self) -> None:
        with patch(
            "app.analysis.local_ocr.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="eng\nind\n", stderr=""),
        ) as run:
            provider = TesseractLocalOCRProvider("tesseract", "ind+eng", 10)
            response = provider.analyze_images([{
                "unit_key": "late-page",
                "mime_type": "image/png",
                "payload": self.payload,
                "_ocr_document_deadline_monotonic": 0.1,
            }])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(response.items)
        self.assertEqual(response.metrics["budget_exhausted_unit_count"], 1)
        self.assertEqual(
            response.metrics["budget_exhaustion_reasons"]["late-page"],
            "document_time_budget_exhausted",
        )

    def test_visual_engine_persists_budget_reason_and_telemetry(self) -> None:
        class ExhaustedLocalOCR:
            name = "budget_test"

            def analyze_images(self, images):
                return LocalOCRResponse(
                    warnings=["OCR dihentikan aman oleh budget."],
                    metrics={
                        "attempt_count": 3,
                        "timeout_count": 1,
                        "budget_exhaustion_reasons": {
                            images[0]["unit_key"]: "unit_time_budget_exhausted"
                        },
                    },
                )

        units, result = VisualOCREngine(None, ExhaustedLocalOCR()).run(
            self.identity,
            self.payload,
            self.units,
            local_document_budget_seconds=60,
        )
        self.assertEqual(units[0]["status"], "ocr_required")
        self.assertEqual(
            units[0]["metadata"]["ocr_budget_reason"],
            "unit_time_budget_exhausted",
        )
        self.assertEqual(result.output["local_ocr_attempt_count"], 3)
        self.assertEqual(result.output["local_ocr_timeout_count"], 1)
        self.assertEqual(result.output["local_ocr_budget_exhausted_units"], 1)

    def test_visual_engine_interleaves_render_and_ocr_in_bounded_batches(self) -> None:
        class RecordingLocalOCR(AllHighConfidenceLocalOCR):
            def __init__(self):
                self.batch_sizes = []

            def analyze_images(self, images):
                self.batch_sizes.append(len(images))
                return super().analyze_images(images)

        identity = DocumentIdentity(
            "scan.pdf", "application/pdf", 3, "pdf-sha", "pdf"
        )
        units = [{
            "unit_key": f"page-{page}",
            "unit_type": "page",
            "ordinal": page,
            "heading_path": [],
            "source_location": {"page": page},
            "text": "",
            "status": "ocr_required",
            "warnings": [],
            "metadata": {},
        } for page in range(1, 7)]
        provider = RecordingLocalOCR()
        engine = VisualOCREngine(None, provider)
        checkpoints = []

        def prepare(_identity, _payload, candidates, dpi, *_args):
            return [{
                "unit_key": unit["unit_key"],
                "mime_type": "image/png",
                "payload": self.payload,
                "render_dpi": dpi,
            } for unit in candidates]

        with patch.object(engine, "_prepare_images", side_effect=prepare):
            updated, result = engine.run(
                identity,
                b"pdf",
                units,
                pdf_dpi=144,
                pdf_retry_dpi=144,
                local_render_batch_units=2,
                checkpoint_callback=lambda snapshot, details: checkpoints.append({
                    "details": details,
                    "statuses": {
                        unit["unit_key"]: unit["status"]
                        for unit in snapshot
                        if unit["unit_key"] in details["unit_keys"]
                    },
                }),
            )
        self.assertEqual(provider.batch_sizes, [2, 2, 2])
        self.assertEqual(len(checkpoints), 3)
        self.assertTrue(all(
            set(checkpoint["statuses"].values()) == {"processed"}
            for checkpoint in checkpoints
        ))
        self.assertTrue(all(unit["status"] == "processed" for unit in updated))
        self.assertEqual(result.output["local_ocr_processed"], 6)
        self.assertEqual(result.output["local_ocr_render_batch_units"], 2)

    def test_visual_checkpoint_failure_is_not_hidden_as_provider_warning(self) -> None:
        def fail_checkpoint(_units, _details):
            raise RuntimeError("database unavailable")

        with self.assertRaisesRegex(VisualCheckpointError, "checkpoint gagal"):
            VisualOCREngine(None, HighConfidenceLocalOCR()).run(
                self.identity,
                self.payload,
                self.units,
                checkpoint_callback=fail_checkpoint,
            )

    def test_visual_cancellation_stops_before_next_batch_after_checkpoint(self) -> None:
        identity = DocumentIdentity(
            "cancel.pdf", "application/pdf", 3, "pdf-sha", "pdf"
        )
        units = [{
            "unit_key": f"page-{page}",
            "unit_type": "page",
            "ordinal": page,
            "heading_path": [],
            "source_location": {"page": page},
            "text": "",
            "status": "ocr_required",
            "warnings": [],
            "metadata": {},
        } for page in range(1, 5)]
        provider = AllHighConfidenceLocalOCR()
        engine = VisualOCREngine(None, provider)
        checkpoints = []
        cancellation_calls = 0

        def prepare(_identity, _payload, candidates, dpi, *_args):
            return [{
                "unit_key": unit["unit_key"],
                "mime_type": "image/png",
                "payload": self.payload,
                "render_dpi": dpi,
            } for unit in candidates]

        def cancellation_check():
            nonlocal cancellation_calls
            cancellation_calls += 1
            return cancellation_calls >= 3

        with (
            patch.object(engine, "_prepare_images", side_effect=prepare),
            self.assertRaises(VisualCancellationRequested),
        ):
            engine.run(
                identity,
                b"pdf",
                units,
                pdf_retry_dpi=144,
                local_render_batch_units=2,
                checkpoint_callback=lambda _snapshot, details: checkpoints.append(
                    list(details["unit_keys"])
                ),
                cancellation_check=cancellation_check,
            )
        self.assertEqual(checkpoints, [["page-1", "page-2"]])


if __name__ == "__main__":
    unittest.main()
