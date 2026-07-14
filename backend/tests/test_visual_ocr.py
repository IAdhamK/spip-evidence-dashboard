from __future__ import annotations

import unittest
from io import BytesIO
from zipfile import ZipFile

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.provider import VisionOCRItem, VisionOCRResponse
from app.analysis.vision import VisualOCREngine


class FakeVisionProvider:
    def analyze_images(self, images):
        return VisionOCRResponse(
            items=[
                VisionOCRItem(
                    unit_key=images[0]["unit_key"],
                    ocr_text="Dokumen evaluasi kinerja triwulan pertama tahun 2026.",
                    observations=["Terdapat stempel pada bagian bawah dokumen."],
                    confidence=0.93,
                )
            ]
        )


class WrongKeyVisionProvider:
    def analyze_images(self, images):
        return VisionOCRResponse(
            items=[
                VisionOCRItem(
                    unit_key="image-tidak-dikenal",
                    ocr_text="Teks yang tidak boleh diterapkan ke unit sumber.",
                    confidence=0.9,
                )
            ]
        )


class VisualOCREngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = DocumentIdentity("scan.png", "image/png", 4, "sha", "image")
        self.units = [
            {
                "unit_key": "image-1",
                "unit_type": "image",
                "ordinal": 1,
                "heading_path": [],
                "source_location": {"image": 1},
                "text": "",
                "status": "ocr_required",
                "warnings": ["Gambar membutuhkan Visual/OCR Engine."],
                "metadata": {},
            }
        ]

    def test_valid_ocr_moves_exact_unit_to_processed(self) -> None:
        units, result = VisualOCREngine(FakeVisionProvider()).run(
            self.identity, b"image", self.units
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(units[0]["status"], "processed")
        self.assertIn("evaluasi kinerja", units[0]["text"])
        self.assertEqual(units[0]["metadata"]["ocr_confidence"], 0.93)
        self.assertEqual(self.units[0]["status"], "ocr_required")

    def test_unknown_unit_key_is_rejected_and_remains_blocked(self) -> None:
        units, result = VisualOCREngine(WrongKeyVisionProvider()).run(
            self.identity, b"image", self.units
        )
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(units[0]["status"], "ocr_required")
        self.assertTrue(any("tidak dikenal" in item for item in result.warnings))

    def test_missing_provider_keeps_ocr_required(self) -> None:
        units, result = VisualOCREngine(None).run(self.identity, b"image", self.units)
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(units[0]["status"], "ocr_required")
        self.assertEqual(result.output["ocr_processed"], 0)

    def test_embedded_office_image_is_routed_by_part_location(self) -> None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("word/media/image1.png", b"fake-png")
        identity = DocumentIdentity("evidence.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 8, "sha-docx", "docx")
        units = [{
            **self.units[0],
            "unit_key": "block-2",
            "unit_type": "embedded_image",
            "source_location": {"part": "word/media/image1.png", "block": 2},
        }]
        updated, result = VisualOCREngine(FakeVisionProvider()).run(
            identity,
            buffer.getvalue(),
            units,
            office_page_expansion_enabled=False,
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(updated[0]["status"], "processed")


if __name__ == "__main__":
    unittest.main()
