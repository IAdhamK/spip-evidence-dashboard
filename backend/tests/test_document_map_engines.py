from __future__ import annotations

from io import BytesIO
import unittest
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.document_map import CoverageEngine, DocumentStructureEngine, NativeParsingEngine


def identity(kind: str, name: str) -> DocumentIdentity:
    return DocumentIdentity(name, None, 0, f"sha-{kind}", kind)


def minimal_xlsx() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Evidence" sheetId="1" r:id="rId1"/></sheets>
              <definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">
              Evidence!$A$1:$B$2</definedName></definedNames></workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <dimension ref="A1:B2"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Evaluasi triwulan</t></is></c>
              <c r="B1"><f>1+1</f><v>2</v></c></row></sheetData>
              <mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>
              <hyperlinks><hyperlink ref="A1" r:id="rIdLink" display="Bukti"/></hyperlinks></worksheet>""",
        )
        archive.writestr(
            "xl/worksheets/_rels/sheet1.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rIdLink" Target="https://example.org/evidence" TargetMode="External"/>
              <Relationship Id="rIdComments" Target="../comments1.xml"/>
              <Relationship Id="rIdDrawing" Target="../drawings/drawing1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/comments1.xml",
            """<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <authors><author>Reviewer</author></authors><commentList>
              <comment ref="B1" authorId="0"><text><t>Formula telah diverifikasi.</t></text></comment>
              </commentList></comments>""",
        )
    return buffer.getvalue()


def minimal_docx_with_hyperlink() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
              <w:hyperlink r:id="rId5"><w:r><w:t>Bukti evaluasi</w:t></w:r></w:hyperlink>
              </w:p></w:body></w:document>""",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId5" Target="https://example.org/evidence" TargetMode="External"/>
              </Relationships>""",
        )
    return buffer.getvalue()


def minimal_docx_with_table() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body><w:tbl>
                <w:tr><w:tc><w:p><w:r><w:t>Risiko</w:t></w:r></w:p></w:tc>
                  <w:tc><w:p><w:r><w:t>Pengendalian</w:t></w:r></w:p></w:tc></w:tr>
                <w:tr><w:tc><w:p><w:r><w:t>R-01</w:t></w:r></w:p></w:tc>
                  <w:tc><w:p><w:r><w:t>Monitoring triwulan</w:t></w:r></w:p></w:tc></w:tr>
              </w:tbl></w:body></w:document>""",
        )
    return buffer.getvalue()


def minimal_pptx_with_picture() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:sldSz cx="9144000" cy="6858000"/></p:presentation>""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Evaluasi SPIP</a:t></a:r></a:p></p:txBody></p:sp>
              <p:pic><p:nvPicPr><p:cNvPr id="2" name="Stempel Dinas" descr="Stempel pengesahan"/>
              <p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/></p:blipFill>
              <p:spPr><a:xfrm><a:off x="914400" y="685800"/><a:ext cx="1828800" cy="1371600"/></a:xfrm></p:spPr></p:pic>
              </p:spTree></p:cSld></p:sld>""",
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
                Target="../media/image1.png"/>
              </Relationships>""",
        )
        archive.writestr("ppt/media/image1.png", b"fake-png")
    return buffer.getvalue()


def xlsx_with_shape_and_chart() -> bytes:
    buffer = BytesIO(minimal_xlsx())
    with ZipFile(buffer, "a") as archive:
        archive.writestr(
            "xl/drawings/drawing1.xml",
            """<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <xdr:oneCellAnchor><xdr:from><xdr:col>2</xdr:col><xdr:row>3</xdr:row></xdr:from>
              <xdr:ext cx="2000000" cy="800000"/><xdr:sp><xdr:nvSpPr><xdr:cNvPr id="2" name="Alur"/></xdr:nvSpPr>
              <xdr:spPr><a:prstGeom prst="rect"/></xdr:spPr><xdr:txBody><a:p><a:r><a:t>Alur mitigasi risiko</a:t></a:r></a:p>
              </xdr:txBody></xdr:sp><xdr:clientData/></xdr:oneCellAnchor>
              <xdr:twoCellAnchor><xdr:from><xdr:col>4</xdr:col><xdr:row>1</xdr:row></xdr:from>
              <xdr:to><xdr:col>9</xdr:col><xdr:row>14</xdr:row></xdr:to>
              <xdr:graphicFrame><xdr:nvGraphicFramePr><xdr:cNvPr id="3" name="Tren Risiko"/></xdr:nvGraphicFramePr>
              <a:graphic><a:graphicData><c:chart r:id="rId1"/></a:graphicData></a:graphic>
              </xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>""",
        )
        archive.writestr(
            "xl/drawings/_rels/drawing1.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="../charts/chart1.xml"/>
              </Relationships>""",
        )
        archive.writestr(
            "xl/charts/chart1.xml",
            """<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <c:chart><c:title><a:t>Tren risiko</a:t></c:title><c:plotArea><c:barChart><c:ser>
              <c:tx><c:v>Risiko residual</c:v></c:tx><c:cat><c:strRef><c:f>Evidence!$A$1</c:f>
              <c:strCache><c:pt idx="0"><c:v>Triwulan I</c:v></c:pt></c:strCache></c:strRef></c:cat>
              <c:val><c:numRef><c:f>Evidence!$B$1</c:f><c:numCache><c:pt idx="0"><c:v>2</c:v></c:pt></c:numCache>
              </c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>""",
        )
    return buffer.getvalue()


def xlsx_with_unanchored_chart() -> bytes:
    buffer = BytesIO(minimal_xlsx())
    with ZipFile(buffer, "a") as archive:
        archive.writestr(
            "xl/drawings/drawing1.xml",
            """<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"/>""",
        )
        archive.writestr(
            "xl/drawings/_rels/drawing1.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="../charts/chart1.xml"/>
              </Relationships>""",
        )
        archive.writestr(
            "xl/charts/chart1.xml",
            """<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
              <c:chart><c:plotArea><c:barChart><c:ser><c:val><c:numRef>
              <c:f>Evidence!$B$1</c:f><c:numCache><c:pt idx="0"><c:v>2</c:v></c:pt></c:numCache>
              </c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>""",
        )
    return buffer.getvalue()


def sparse_visual_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    image = DecodedStreamObject()
    image.set_data(b"\xff")
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
            NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)}),
        }
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Lampiran) Tj ET")
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class DocumentMapEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = NativeParsingEngine()
        self.structure = DocumentStructureEngine()
        self.coverage = CoverageEngine()

    def test_text_full_audit_has_complete_coverage(self) -> None:
        doc = identity("text", "evidence.txt")
        units, inventory, parse_result = self.parser.run(doc, b"baris satu\nbaris dua", "full_audit")
        document_map, structure_result = self.structure.run(doc, units, inventory)
        ledger, coverage_result = self.coverage.run(doc, units)
        self.assertEqual(parse_result.status, EngineStatus.COMPLETED)
        self.assertEqual(structure_result.status, EngineStatus.COMPLETED)
        self.assertEqual(coverage_result.status, EngineStatus.COMPLETED)
        self.assertEqual(ledger["coverage_percentage"], 100.0)
        self.assertEqual(document_map["unit_type_counts"]["text_line"], 2)

    def test_screening_records_unprocessed_units(self) -> None:
        doc = identity("text", "large.txt")
        payload = "\n".join(f"baris {index}" for index in range(250)).encode()
        units, _, _ = self.parser.run(doc, payload, "screening")
        ledger, result = self.coverage.run(doc, units)
        self.assertEqual(len(units), 250)
        self.assertEqual(ledger["processed_units"], 200)
        self.assertEqual(ledger["pending_units"], 50)
        self.assertEqual(result.status, EngineStatus.PARTIAL)

    def test_text_detects_legacy_encoding_and_preserves_exact_line(self) -> None:
        doc = identity("text", "legacy.csv")
        units, inventory, result = self.parser.run(
            doc, "Nama;Keterangan\nDesa;Evaluasi berkala – selesai".encode("cp1252"), "full_audit"
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(inventory["encoding"], "cp1252")
        self.assertEqual(units[1]["source_location"]["line_start"], 2)
        self.assertIn("Evaluasi berkala", units[1]["text"])

    def test_xlsx_preserves_sheet_and_cell_location_text(self) -> None:
        doc = identity("xlsx", "evidence.xlsx")
        units, inventory, result = self.parser.run(doc, minimal_xlsx(), "full_audit")
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(inventory["total_sheets"], 1)
        self.assertEqual(inventory["print_area_count"], 1)
        self.assertEqual(inventory["sheets"][0]["print_area"], "Evidence!$A$1:$B$2")
        self.assertEqual(units[0]["source_location"]["sheet"], "Evidence")
        self.assertEqual(units[0]["metadata"]["dimension_ref"], "A1:B2")
        self.assertIn("A1=Evaluasi triwulan", units[0]["text"])
        self.assertIn("formula: 1+1", units[0]["text"])
        self.assertIn("comment: Formula telah diverifikasi", units[0]["text"])
        self.assertEqual(units[0]["metadata"]["merged_ranges"], ["A1:B1"])
        self.assertEqual(units[0]["metadata"]["hyperlinks"][0]["target"], "https://example.org/evidence")
        self.assertEqual(units[0]["metadata"]["comment_cells"], ["B1"])

    def test_xlsx_structures_shape_anchor_and_chart_series_semantics(self) -> None:
        doc = identity("xlsx", "visual.xlsx")
        units, inventory, result = self.parser.run(doc, xlsx_with_shape_and_chart(), "full_audit")
        document_map, structure_result = self.structure.run(doc, units, inventory)
        by_type = {unit["unit_type"]: unit for unit in units}
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(structure_result.status, EngineStatus.COMPLETED)
        self.assertEqual(inventory["shape_drawing_count"], 1)
        self.assertEqual(inventory["chart_count"], 1)
        self.assertIn("Alur mitigasi risiko", by_type["drawing_shape"]["text"])
        self.assertIn("Tren risiko", by_type["chart"]["text"])
        self.assertEqual(by_type["drawing_shape"]["status"], "processed")
        self.assertEqual(by_type["drawing_shape"]["source_location"]["from_cell"], "C4")
        shape_region = by_type["drawing_shape"]["metadata"]["semantic_regions"][0]
        self.assertEqual(shape_region["region_type"], "drawing_shape")
        self.assertEqual(shape_region["semantic_hint"], "diagram")
        self.assertEqual(shape_region["bbox"]["from_cell"], "C4")
        self.assertEqual(by_type["chart"]["status"], "processed")
        self.assertEqual(by_type["chart"]["source_location"]["from_cell"], "E2")
        self.assertEqual(by_type["chart"]["source_location"]["to_cell"], "J15")
        chart_region = by_type["chart"]["metadata"]["semantic_regions"][0]
        self.assertEqual(chart_region["region_type"], "chart")
        self.assertEqual(chart_region["coordinate_space"], "spreadsheet_cells")
        semantics = by_type["chart"]["metadata"]["chart_semantics"]
        self.assertEqual(semantics["chart_type"], "barChart")
        self.assertEqual(semantics["series"][0]["name"], "Risiko residual")
        self.assertEqual(semantics["series"][0]["categories"], ["Triwulan I"])
        self.assertEqual(semantics["series"][0]["values"], ["2"])
        self.assertEqual(document_map["semantic_region_count"], 2)
        self.assertEqual(
            {item["region_type"] for item in document_map["semantic_regions"]},
            {"drawing_shape", "chart"},
        )

    def test_xlsx_chart_without_drawing_anchor_remains_partial(self) -> None:
        units, _, result = self.parser.run(
            identity("xlsx", "unanchored.xlsx"), xlsx_with_unanchored_chart(), "full_audit"
        )
        chart = next(unit for unit in units if unit["unit_type"] == "chart")
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(chart["status"], "partial")
        self.assertTrue(chart["metadata"]["requires_visual_verification"])
        self.assertIsNone(chart["metadata"]["visual_semantics_method"])

    def test_docx_preserves_heading_style_and_hyperlink_target(self) -> None:
        doc = identity("docx", "evidence.docx")
        units, inventory, result = self.parser.run(doc, minimal_docx_with_hyperlink(), "full_audit")
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(inventory["total_blocks"], 1)
        self.assertEqual(units[0]["unit_type"], "heading")
        self.assertEqual(units[0]["metadata"]["style"], "Heading1")
        self.assertEqual(units[0]["metadata"]["hyperlinks"], ["https://example.org/evidence"])

    def test_docx_unitizes_table_rows_with_table_and_row_source_location(self) -> None:
        units, inventory, result = self.parser.run(
            identity("docx", "table.docx"), minimal_docx_with_table(), "full_audit"
        )
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(inventory["total_blocks"], 2)
        self.assertEqual(units[0]["unit_type"], "table_row")
        self.assertEqual(units[1]["source_location"]["table"], 1)
        self.assertEqual(units[1]["source_location"]["row"], 2)
        self.assertIn("C2=Monitoring triwulan", units[1]["text"])

    def test_docx_embedded_image_is_an_explicit_ocr_unit(self) -> None:
        buffer = BytesIO(minimal_docx_with_hyperlink())
        with ZipFile(buffer, "a") as archive:
            archive.writestr("word/media/image1.png", b"fake-png")
        units, inventory, result = self.parser.run(
            identity("docx", "visual.docx"), buffer.getvalue(), "full_audit"
        )
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(inventory["total_blocks"], 2)
        self.assertEqual(units[1]["unit_type"], "embedded_image")
        self.assertEqual(units[1]["status"], "ocr_required")
        self.assertEqual(units[1]["source_location"]["part"], "word/media/image1.png")

    def test_image_requires_visual_engine_and_blocks_complete_coverage(self) -> None:
        doc = identity("image", "evidence.png")
        units, _, result = self.parser.run(doc, b"\x89PNG\r\n\x1a\nrest", "full_audit")
        ledger, coverage_result = self.coverage.run(doc, units)
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(ledger["ocr_required_units"], 1)
        self.assertEqual(coverage_result.status, EngineStatus.PARTIAL)
        self.assertTrue(ledger["primary_blocked"])

    def test_image_inventory_records_png_dimensions_without_decoding_library(self) -> None:
        payload = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")
        units, inventory, _ = self.parser.run(identity("image", "photo.png"), payload, "full_audit")
        self.assertEqual((inventory["width"], inventory["height"]), (640, 480))
        self.assertEqual((units[0]["metadata"]["width"], units[0]["metadata"]["height"]), (640, 480))

    def test_pptx_visual_slide_gets_full_slide_unit_and_precise_locator(self) -> None:
        units, inventory, result = self.parser.run(
            identity("pptx", "visual.pptx"), minimal_pptx_with_picture(), "full_audit"
        )
        by_key = {unit["unit_key"]: unit for unit in units}
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(inventory["total_slides"], 1)
        self.assertEqual(inventory["full_slide_visual_count"], 1)
        self.assertEqual(by_key["slide-1"]["status"], "processed")
        visual = by_key["slide-visual-1"]
        self.assertEqual(visual["unit_type"], "slide_visual")
        self.assertEqual(visual["status"], "ocr_required")
        self.assertEqual(visual["source_location"]["slide"], 1)
        self.assertEqual(visual["source_location"]["render"], "full_slide")
        self.assertEqual(visual["metadata"]["visual_inventory"]["pictures"], 1)
        self.assertEqual(visual["metadata"]["visual_inventory"]["semantic_region_count"], 1)
        picture_region = visual["metadata"]["semantic_regions"][0]
        self.assertEqual(picture_region["region_type"], "picture")
        self.assertEqual(picture_region["semantic_hint"], "stamp")
        self.assertEqual(picture_region["coordinate_space"], "normalized_top_left")
        self.assertEqual(
            picture_region["bbox"],
            {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        )
        self.assertTrue(visual["metadata"]["requires_visual_verification"])

    def test_sparse_pdf_text_with_image_resource_still_requires_ocr(self) -> None:
        units, _, result = self.parser.run(
            identity("pdf", "sparse.pdf"), sparse_visual_pdf(), "full_audit"
        )
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(units[0]["status"], "ocr_required")
        self.assertGreater(units[0]["metadata"]["image_xobject_count"], 0)
        self.assertTrue(units[0]["metadata"]["sparse_visual_page"])


if __name__ == "__main__":
    unittest.main()
