from __future__ import annotations

import ast
from io import BytesIO
from pathlib import Path
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfWriter

from app import legacy_document_extraction as extraction
from app import smart_upload


def zip_payload(parts: dict[str, str]) -> bytes:
    target = BytesIO()
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value.encode("utf-8"))
    return target.getvalue()


def docx_payload() -> bytes:
    return zip_payload(
        {
            "word/document.xml": (
                '<w:document xmlns:w="urn:w"><w:body><w:p><w:r>'
                "<w:t>Laporan evaluasi berkala</w:t>"
                "</w:r></w:p></w:body></w:document>"
            ),
            "word/header1.xml": (
                '<w:hdr xmlns:w="urn:w"><w:p><w:r>'
                "<w:t>Unit Pengendalian</w:t>"
                "</w:r></w:p></w:hdr>"
            ),
        }
    )


def xlsx_payload() -> bytes:
    return zip_payload(
        {
            "xl/workbook.xml": (
                '<workbook xmlns="urn:s" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Evaluasi" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="urn:r"><Relationship Id="rId1" '
                'Target="worksheets/sheet1.xml"/></Relationships>'
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="urn:s"><sheetData>'
                '<row r="1"><c r="A1" t="inlineStr"><is><t>Dokumentasi evidence</t></is></c>'
                '<c r="B1" t="inlineStr"><is><t>Tautan</t></is></c></row>'
                '<row r="2"><c r="A2" t="inlineStr"><is><t>Laporan evaluasi berkala</t></is></c>'
                '<c r="B2" t="inlineStr"><is><t>https://example.org/bukti</t></is></c></row>'
                "</sheetData></worksheet>"
            ),
        }
    )


def pptx_payload() -> bytes:
    return zip_payload(
        {
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:t>'
                "Evaluasi berkala telah dilaksanakan"
                "</a:t></p:cSld></p:sld>"
            ),
            "ppt/notesSlides/notesSlide1.xml": (
                '<p:notes xmlns:p="urn:p" xmlns:a="urn:a"><a:t>'
                "Tindak lanjut hasil evaluasi"
                "</a:t></p:notes>"
            ),
        }
    )


class LegacyDocumentExtractionTests(unittest.TestCase):
    def test_smart_upload_reexports_parser_contract(self) -> None:
        public_names = (
            "extract_preview_text",
            "extract_plain_text",
            "extract_pdf_text",
            "extract_docx_text",
            "extract_xlsx_text",
            "extract_pptx_text",
            "extract_image_metadata",
            "normalize_analysis_mode",
            "selected_pdf_pages",
            "read_xlsx_shared_strings",
            "xlsx_reference_stage_hints",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(getattr(smart_upload, name), getattr(extraction, name))

    def test_plain_text_and_invalid_mode_keep_legacy_behavior(self) -> None:
        result = smart_upload.extract_preview_text(
            "catatan.txt", "text/plain", b"evaluasi\n\n  berkala", False, "invalid"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "plain_text")
        self.assertEqual(result["text"], "evaluasi berkala")
        self.assertEqual(smart_upload.normalize_analysis_mode("invalid")[0], "fast")

    def test_docx_extracts_main_and_header_sections(self) -> None:
        result = smart_upload.extract_preview_text(
            "laporan.docx", None, docx_payload(), False, "full"
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("Laporan evaluasi berkala", result["text"])
        self.assertIn("Unit Pengendalian", result["text"])
        self.assertEqual(len(result["section_summaries"]), 2)

    def test_xlsx_preserves_sheet_evidence_and_url(self) -> None:
        result = smart_upload.extract_preview_text(
            "matriks.xlsx", None, xlsx_payload(), False, "full"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_sheets"], 1)
        self.assertEqual(result["scanned_sheets"], 1)
        self.assertGreaterEqual(result["evidence_row_count"], 1)
        self.assertEqual(result["hyperlink_count"], 1)
        self.assertEqual(result["evidence_links"][0]["url"], "https://example.org/bukti")
        self.assertIn("Evaluasi Berkala", result["evidence_rows"][1]["stage_hints"])

    def test_pptx_extracts_slide_and_presenter_notes(self) -> None:
        result = smart_upload.extract_preview_text(
            "paparan.pptx", None, pptx_payload(), False, "full"
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_slides"], 1)
        self.assertIn("Evaluasi berkala telah dilaksanakan", result["text"])
        self.assertIn("Catatan presenter: Tindak lanjut hasil evaluasi", result["text"])

    def test_pdf_and_malformed_archive_fail_closed_without_crashing_entrypoint(self) -> None:
        pdf = BytesIO()
        writer = PdfWriter()
        for _ in range(6):
            writer.add_blank_page(width=100, height=100)
        writer.write(pdf)
        pdf_result = smart_upload.extract_preview_text("scan.pdf", "application/pdf", pdf.getvalue(), False)
        self.assertEqual(pdf_result["status"], "partial")
        self.assertEqual(pdf_result["total_pages"], 6)
        self.assertEqual(pdf_result["scanned_pages"], 5)

        broken = smart_upload.extract_preview_text("rusak.xlsx", None, b"not-a-zip", False)
        self.assertEqual(broken["status"], "partial")
        self.assertEqual(broken["method"], "xlsx")
        self.assertEqual(broken["text"], "")
        self.assertIn("Ekstraksi gagal", broken["message"])

    def test_smart_upload_no_longer_owns_archive_or_xml_parser_implementation(self) -> None:
        source_path = Path(smart_upload.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        parser_definitions = {
            "extract_preview_text",
            "extract_pdf_text",
            "extract_docx_text",
            "extract_xlsx_text",
            "extract_pptx_text",
            "read_xlsx_shared_strings",
            "extract_xlsx_sheet_structured",
        }
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertTrue(parser_definitions.isdisjoint(defined))

        forbidden_imports = {"io", "posixpath", "xml.etree.ElementTree", "zipfile"}
        imported: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertTrue(forbidden_imports.isdisjoint(imported))


if __name__ == "__main__":
    unittest.main()
