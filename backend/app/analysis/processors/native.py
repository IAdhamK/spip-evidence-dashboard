from __future__ import annotations

from io import BytesIO
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from app.analysis.contracts import DocumentIdentity
from app.analysis.document_family_registry import RISK_MATRIX_HEADER_ALIASES


MAX_UNIT_TEXT_CHARS = 2_000_000
SPARSE_PDF_TEXT_CHARS = 200
SCREENING_LIMITS = {
    "pdf": 5,
    "docx": 50,
    "xlsx": 4,
    "pptx": 6,
    "text": 200,
}


def parse_native_document(
    identity: DocumentIdentity,
    payload: bytes,
    analysis_mode: str,
) -> tuple[list[dict], dict]:
    parser = {
        "pdf": parse_pdf,
        "docx": parse_docx,
        "xlsx": parse_xlsx,
        "pptx": parse_pptx,
        "image": parse_image,
        "text": parse_text,
    }.get(identity.file_kind)
    if not parser:
        return [], {"file_kind": identity.file_kind, "error": "Processor tidak tersedia."}
    return parser(payload, analysis_mode)


def parse_pdf(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(payload))
    total_pages = len(reader.pages)
    limit = _processing_limit(total_pages, "pdf", analysis_mode)
    units: list[dict] = []
    for index in range(total_pages):
        page_number = index + 1
        if index >= limit:
            units.append(_unit(f"page-{page_number}", "page", page_number, "pending", {"page": page_number}))
            continue
        try:
            page = reader.pages[index]
            text = normalize_text(page.extract_text() or "")
            image_xobject_count = _pdf_image_xobject_count(page)
            sparse_visual_page = bool(
                text
                and len(text) < SPARSE_PDF_TEXT_CHARS
                and image_xobject_count
            )
            status = "processed" if text and not sparse_visual_page else "ocr_required"
            if not text:
                warnings = ["Text layer tidak ditemukan; halaman membutuhkan OCR/vision."]
            elif sparse_visual_page:
                warnings = [
                    "Text layer sangat tipis dan halaman mengandung gambar; OCR/vision diperlukan untuk memastikan coverage."
                ]
            else:
                warnings = []
            units.append(
                _unit(
                    f"page-{page_number}",
                    "page",
                    page_number,
                    status,
                    {"page": page_number},
                    text=text,
                    heading_path=_heading_guess(text),
                    warnings=warnings,
                    metadata={
                        "text_density": round(len(text), 1),
                        "native_text_char_count": len(text),
                        "image_xobject_count": image_xobject_count,
                        "sparse_visual_page": sparse_visual_page,
                    },
                )
            )
        except Exception as exc:
            units.append(
                _unit(
                    f"page-{page_number}",
                    "page",
                    page_number,
                    "failed",
                    {"page": page_number},
                    warnings=[f"Ekstraksi halaman gagal: {exc}"],
                )
            )
    return units, {"file_kind": "pdf", "total_pages": total_pages, "selected_pages": limit}


def parse_docx(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    with ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        relationships = _relationship_targets(
            archive.read("word/_rels/document.xml.rels")
            if "word/_rels/document.xml.rels" in names
            else None
        )
        blocks = _docx_main_blocks(archive.read("word/document.xml"), relationships)
        for prefix, label in (
            ("word/header", "header"),
            ("word/footer", "footer"),
            ("word/footnotes", "footnotes"),
            ("word/endnotes", "endnotes"),
            ("word/comments", "comments"),
        ):
            for path in sorted(name for name in names if name.startswith(prefix) and name.endswith(".xml")):
                text = normalize_text(" ".join(_xml_text_nodes(archive.read(path))))
                if text:
                    blocks.append({"type": label, "text": text, "source": path, "heading_path": []})
        for path in sorted(name for name in names if name.startswith("word/media/") and not name.endswith("/")):
            blocks.append(
                {
                    "type": "embedded_image",
                    "text": "",
                    "source": path,
                    "heading_path": [],
                    "status": "ocr_required",
                    "warnings": ["Gambar tertanam DOCX membutuhkan Visual/OCR Engine."],
                    "metadata": {"media_part": path, "size_bytes": archive.getinfo(path).file_size},
                }
            )

    limit = _processing_limit(len(blocks), "docx", analysis_mode)
    units = []
    for index, block in enumerate(blocks, start=1):
        status = (block.get("status") or "processed") if index <= limit else "pending"
        text = block["text"] if status == "processed" else ""
        units.append(
            _unit(
                f"block-{index}",
                block["type"],
                index,
                status,
                {
                    "part": block.get("source", "word/document.xml"),
                    "block": index,
                    **(block.get("source_location") or {}),
                },
                text=text,
                heading_path=block.get("heading_path") or [],
                metadata=block.get("metadata") or {},
                warnings=block.get("warnings") or [],
            )
        )
    return units, {"file_kind": "docx", "total_blocks": len(blocks), "selected_blocks": limit}


def parse_xlsx(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    with ZipFile(BytesIO(payload)) as archive:
        archive_names = set(archive.namelist())
        shared_strings = _xlsx_shared_strings(archive)
        sheets = _xlsx_sheet_inventory(archive)
        defined_names = _xlsx_defined_names(archive)
        print_areas = {
            int(item["local_sheet_id"]): item["value"]
            for item in defined_names
            if item["name"] == "_xlnm.Print_Area" and item.get("local_sheet_id") is not None
        }
        for sheet_index, sheet in enumerate(sheets):
            sheet["print_area"] = print_areas.get(sheet_index)
            try:
                with archive.open(sheet["path"]) as sheet_stream:
                    sheet.update(_xlsx_risk_matrix_features(
                        sheet_stream,
                        shared_strings,
                        row_limit=40,
                    ))
            except Exception as exc:
                sheet["inventory_warning"] = f"Profiling sheet gagal: {exc}"
                sheet["risk_matrix_relevant"] = False
        drawing_context: dict[str, dict] = {}
        limit = _processing_limit(len(sheets), "xlsx", analysis_mode)
        selected_sheet_indexes = set(range(limit))
        if analysis_mode != "full_audit":
            adaptive_candidates = sorted(
                (
                    (index, sheet) for index, sheet in enumerate(sheets)
                    if sheet.get("risk_matrix_relevant") and index not in selected_sheet_indexes
                ),
                key=lambda item: (
                    -int(item[1].get("risk_header_category_count") or 0),
                    -int(item[1].get("substantive_row_count") or 0),
                    item[0],
                ),
            )
            selected_sheet_indexes.update(index for index, _sheet in adaptive_candidates[:4])
        for sheet_index, sheet in enumerate(sheets):
            sheet["adaptive_selected"] = sheet_index in selected_sheet_indexes
        units: list[dict] = []
        for index, sheet in enumerate(sheets, start=1):
            location = {"sheet": sheet["name"], "sheet_index": index, "hidden": sheet["state"] != "visible"}
            if index - 1 not in selected_sheet_indexes:
                units.append(_unit(f"sheet-{index}", "sheet", index, "pending", location, metadata=sheet))
                continue
            try:
                rel_path = _ooxml_relationships_path(sheet["path"])
                relationships = _relationship_targets(
                    archive.read(rel_path) if rel_path in archive_names else None
                )
                for target in relationships.values():
                    resolved = _resolve_ooxml_target(sheet["path"], target)
                    if resolved.startswith("xl/drawings/") and resolved.endswith(".xml"):
                        drawing_context.setdefault(
                            resolved,
                            {"sheet": sheet["name"], "sheet_index": index},
                        )
                comments: dict[str, str] = {}
                for target in relationships.values():
                    resolved = _resolve_ooxml_target(sheet["path"], target)
                    if "comments" in resolved.lower() and resolved in archive_names:
                        comments.update(_xlsx_comments(archive.read(resolved)))
                rows, metadata = _xlsx_sheet_rows(
                    archive.read(sheet["path"]),
                    shared_strings,
                    relationships,
                    comments,
                )
                risk_features = _xlsx_risk_matrix_features(
                    archive.read(sheet["path"]),
                    shared_strings,
                    row_limit=5_000,
                )
                text = "\n".join(rows)
                status, text, warnings = _bounded_text(text)
                units.append(
                    _unit(
                        f"sheet-{index}",
                        "sheet",
                        index,
                        status,
                        location,
                        text=text,
                        heading_path=[sheet["name"]],
                        warnings=warnings,
                        metadata={
                            **sheet,
                            **metadata,
                            **risk_features,
                            "risk_matrix_features": risk_features,
                        },
                    )
                )
            except Exception as exc:
                units.append(
                    _unit(
                        f"sheet-{index}",
                        "sheet",
                        index,
                        "failed",
                        location,
                        warnings=[f"Ekstraksi sheet gagal: {exc}"],
                        metadata=sheet,
                    )
                )
        drawing_paths = sorted(
            name
            for name in archive_names
            if name.startswith("xl/drawings/")
            and "/_rels/" not in name
            and name.endswith(".xml")
        )
        chart_paths: set[str] = set()
        chart_context: dict[str, dict] = {}
        shape_drawing_count = 0
        for drawing_index, path in enumerate(drawing_paths, start=1):
            drawing_xml = archive.read(path)
            drawing_items = _xlsx_drawing_items(drawing_xml)
            rel_path = _ooxml_relationships_path(path)
            relationships = _relationship_targets(
                archive.read(rel_path) if rel_path in archive_names else None
            )
            for relationship_id, target in relationships.items():
                resolved = _resolve_ooxml_target(path, target)
                if resolved.startswith("xl/charts/") and resolved in archive_names:
                    chart_paths.add(resolved)
                    chart_context.setdefault(
                        resolved,
                        {
                            **drawing_context.get(path, {}),
                            **(drawing_items["charts"].get(relationship_id) or {}),
                        },
                    )
            selected = analysis_mode == "full_audit"
            for shape_index, shape in enumerate(drawing_items["shapes"], start=1):
                shape_text = str(shape.get("text") or "")
                if not shape_text:
                    continue
                shape_drawing_count += 1
                anchor = dict(shape.get("anchor") or {})
                structured = bool(anchor.get("from_cell"))
                status = (
                    "processed" if selected and structured
                    else "partial" if selected
                    else "pending"
                )
                units.append(
                    _unit(
                        f"drawing-shape-{drawing_index}-{shape_index}",
                        "drawing_shape",
                        len(units) + 1,
                        status,
                        {
                            "part": path,
                            "drawing": drawing_index,
                            "shape": shape_index,
                            **drawing_context.get(path, {}),
                            **anchor,
                        },
                        text=shape_text if selected else "",
                        warnings=(
                            [] if status == "processed"
                            else ["Teks shape XLSX terbaca, tetapi anchor/relasi visual belum lengkap."]
                            if selected else []
                        ),
                        metadata={
                            "drawing_part": path,
                            "relationship_count": len(relationships),
                            "shape_name": shape.get("name"),
                            "shape_type": shape.get("shape_type"),
                            "anchor": anchor,
                            "visual_semantics_method": (
                                "structured_ooxml_shape_v1" if structured else None
                            ),
                            "semantic_regions": [
                                _xlsx_semantic_region(
                                    "drawing_shape",
                                    anchor,
                                    label=shape_text[:300],
                                    semantic_hint=_explicit_visual_hint(
                                        shape.get("name"), shape_text, shape.get("shape_type")
                                    ),
                                )
                            ] if structured else [],
                            "requires_visual_verification": not structured,
                        },
                    )
                )
        for chart_index, path in enumerate(sorted(chart_paths), start=1):
            selected = analysis_mode == "full_audit"
            chart_semantics = _xlsx_chart_semantics(archive.read(path)) if selected else {}
            chart_text = str(chart_semantics.get("text") or "")
            context = dict(chart_context.get(path, {}))
            structured = bool(
                chart_semantics.get("chart_type")
                and chart_semantics.get("series")
                and context.get("from_cell")
            )
            chart_status = (
                "processed" if selected and structured
                else "partial" if selected
                else "pending"
            )
            units.append(
                _unit(
                    f"chart-{chart_index}",
                    "chart",
                    len(units) + 1,
                    chart_status,
                    {"part": path, "chart": chart_index, **context},
                    text=chart_text,
                    warnings=(
                        [] if chart_status == "processed"
                        else ["Chart XLSX belum mempunyai series/anchor terstruktur yang lengkap."]
                        if selected else []
                    ),
                    metadata={
                        "chart_part": path,
                        "chart_semantics": chart_semantics,
                        "anchor": {
                            key: value for key, value in context.items()
                            if key in {"from_cell", "to_cell", "from_row", "from_column", "to_row", "to_column"}
                        },
                        "visual_semantics_method": (
                            "structured_ooxml_chart_v1" if structured else None
                        ),
                        "semantic_regions": [
                            _xlsx_semantic_region(
                                "chart",
                                context,
                                label=(
                                    chart_semantics.get("title")
                                    or context.get("drawing_name")
                                    or f"Chart {chart_index}"
                                ),
                                semantic_hint="chart",
                            )
                        ] if context.get("from_cell") else [],
                        "requires_visual_verification": not structured,
                    },
                )
            )
        media_paths = sorted(
            name for name in archive_names if name.startswith("xl/media/") and not name.endswith("/")
        )
        for media_index, path in enumerate(media_paths, start=1):
            ordinal = len(units) + 1
            selected = analysis_mode == "full_audit"
            units.append(
                _unit(
                    f"embedded-image-{media_index}",
                    "embedded_image",
                    ordinal,
                    "ocr_required" if selected else "pending",
                    {"part": path, "image": media_index},
                    warnings=["Gambar tertanam XLSX membutuhkan Visual/OCR Engine."] if selected else [],
                    metadata={"media_part": path, "size_bytes": archive.getinfo(path).file_size},
                )
            )
        external_link_paths = sorted(
            name
            for name in archive_names
            if name.startswith("xl/externalLinks/")
            and "/_rels/" not in name
            and name.endswith(".xml")
        )
        table_paths = sorted(
            name for name in archive_names if name.startswith("xl/tables/") and name.endswith(".xml")
        )
    return units, {
        "file_kind": "xlsx", "total_sheets": len(sheets),
        "selected_sheets": len(selected_sheet_indexes),
        "adaptive_screening": analysis_mode != "full_audit",
        "relevant_sheet_count": sum(bool(item.get("risk_matrix_relevant")) for item in sheets),
        "selected_relevant_sheet_count": sum(
            bool(item.get("risk_matrix_relevant")) and bool(item.get("adaptive_selected"))
            for item in sheets
        ),
        "unprocessed_relevant_sheet_count": sum(
            bool(item.get("risk_matrix_relevant")) and not bool(item.get("adaptive_selected"))
            for item in sheets
        ),
        "sheets": sheets,
        "embedded_image_count": len(media_paths),
        "drawing_part_count": len(drawing_paths),
        "shape_drawing_count": shape_drawing_count,
        "chart_count": len(chart_paths),
        "external_link_part_count": len(external_link_paths),
        "table_definition_count": len(table_paths),
        "defined_name_count": len(defined_names),
        "print_area_count": len(print_areas),
    }


def parse_pptx(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    with ZipFile(BytesIO(payload)) as archive:
        archive_names = set(archive.namelist())
        slide_size = _pptx_slide_size(
            archive.read("ppt/presentation.xml")
            if "ppt/presentation.xml" in archive_names else None
        )
        slide_paths = sorted(
            (name for name in archive_names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=_numeric_path_key,
        )
        limit = _processing_limit(len(slide_paths), "pptx", analysis_mode)
        units: list[dict] = []
        visual_slides: list[dict] = []
        for index, path in enumerate(slide_paths, start=1):
            location = {"slide": index, "part": path}
            if index > limit:
                units.append(_unit(f"slide-{index}", "slide", index, "pending", location))
                continue
            slide_xml = archive.read(path)
            text = normalize_text(" ".join(_xml_text_nodes(slide_xml)))
            notes_path = f"ppt/notesSlides/notesSlide{index}.xml"
            notes = normalize_text(" ".join(_xml_text_nodes(archive.read(notes_path)))) if notes_path in archive_names else ""
            combined = normalize_text(f"{text} Catatan presenter: {notes}" if notes else text)
            rel_path = f"ppt/slides/_rels/slide{index}.xml.rels"
            relationships = _relationship_targets(
                archive.read(rel_path) if rel_path in archive_names else None
            )
            hyperlinks = sorted(
                target for target in relationships.values() if target.startswith(("http://", "https://"))
            )
            visual_inventory = _pptx_slide_visual_inventory(
                slide_xml,
                relationships,
                slide= index,
                slide_size=slide_size,
            )
            status, combined, warnings = _bounded_text(combined)
            units.append(
                _unit(
                    f"slide-{index}",
                    "slide",
                    index,
                    status,
                    location,
                    text=combined,
                    heading_path=_heading_guess(text),
                    warnings=warnings,
                    metadata={
                        "notes_char_count": len(notes),
                        "hyperlinks": hyperlinks,
                        "relationship_count": len(relationships),
                        "visual_inventory": visual_inventory,
                    },
                )
                )
            if visual_inventory["requires_full_slide_render"]:
                visual_slides.append({
                    "slide": index,
                    "part": path,
                    "visual_inventory": visual_inventory,
                })
        for item in visual_slides:
            selected = analysis_mode == "full_audit"
            slide_number = int(item["slide"])
            units.append(
                _unit(
                    f"slide-visual-{slide_number}",
                    "slide_visual",
                    len(units) + 1,
                    "ocr_required" if selected else "pending",
                    {
                        "slide": slide_number,
                        "part": item["part"],
                        "render": "full_slide",
                    },
                    warnings=(
                        ["Slide memuat elemen visual; full-slide OCR/verification diperlukan."]
                        if selected else []
                    ),
                    metadata={
                        "visual_inventory": item["visual_inventory"],
                        "semantic_regions": item["visual_inventory"].get("semantic_regions") or [],
                        "requires_visual_verification": True,
                        "render_method": "libreoffice_impress_to_pdf_to_png_v1",
                    },
                )
            )
        media_paths = sorted(
            name for name in archive_names if name.startswith("ppt/media/") and not name.endswith("/")
        )
        for media_index, path in enumerate(media_paths, start=1):
            selected = analysis_mode == "full_audit"
            units.append(
                _unit(
                    f"embedded-image-{media_index}",
                    "embedded_image",
                    len(units) + 1,
                    "ocr_required" if selected else "pending",
                    {"part": path, "image": media_index},
                    warnings=["Gambar tertanam PPTX membutuhkan Visual/OCR Engine."] if selected else [],
                    metadata={"media_part": path, "size_bytes": archive.getinfo(path).file_size},
                )
            )
    return units, {
        "file_kind": "pptx", "total_slides": len(slide_paths), "selected_slides": limit,
        "embedded_image_count": len(media_paths),
        "full_slide_visual_count": len(visual_slides),
    }


def _pptx_slide_visual_inventory(
    slide_xml: bytes,
    relationships: dict[str, str],
    *,
    slide: int | None = None,
    slide_size: dict | None = None,
) -> dict:
    counts = {
        "pictures": 0,
        "graphic_frames": 0,
        "connectors": 0,
        "group_shapes": 0,
    }
    semantic_regions: list[dict] = []
    try:
        root = ET.fromstring(slide_xml)
        for element in root.iter():
            local_name = element.tag.rsplit("}", 1)[-1]
            if local_name == "pic":
                counts["pictures"] += 1
            elif local_name == "graphicFrame":
                counts["graphic_frames"] += 1
            elif local_name == "cxnSp":
                counts["connectors"] += 1
            elif local_name == "grpSp":
                counts["group_shapes"] += 1
            if local_name in {"pic", "graphicFrame", "cxnSp", "grpSp"}:
                region = _pptx_semantic_region(
                    element,
                    relationships,
                    slide=slide,
                    slide_size=slide_size,
                )
                if region:
                    semantic_regions.append(region)
    except ET.ParseError:
        pass
    visual_targets = sorted({
        target
        for target in relationships.values()
        if any(marker in target.lower() for marker in ("/media/", "../media/", "/charts/", "../charts/", "/diagrams/", "../diagrams/"))
    })
    requires_render = bool(sum(counts.values()) or visual_targets)
    return {
        **counts,
        "visual_relationship_count": len(visual_targets),
        "visual_relationship_targets": visual_targets[:100],
        "semantic_region_count": len(semantic_regions),
        "semantic_regions": semantic_regions[:500],
        "requires_full_slide_render": requires_render,
    }


def _pptx_slide_size(xml_data: bytes | None) -> dict | None:
    if not xml_data:
        return None
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return None
    node = next(
        (item for item in root.iter() if _xml_local_name(item.tag) == "sldSz"),
        None,
    )
    if node is None:
        return None
    try:
        width = int(node.attrib.get("cx") or 0)
        height = int(node.attrib.get("cy") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"width_emu": width, "height_emu": height}


def _pptx_semantic_region(
    element: ET.Element,
    relationships: dict[str, str],
    *,
    slide: int | None,
    slide_size: dict | None,
) -> dict | None:
    element_type = _xml_local_name(element.tag)
    region_type = {
        "pic": "picture",
        "graphicFrame": "graphic_frame",
        "cxnSp": "connector",
        "grpSp": "group_shape",
    }.get(element_type)
    if not region_type:
        return None

    relationship_id = next(
        (
            value
            for child in element.iter()
            for key, value in child.attrib.items()
            if _xml_local_name(key) in {"embed", "link", "id"}
            and str(value).startswith("rId")
        ),
        None,
    )
    relationship_target = relationships.get(str(relationship_id)) if relationship_id else None
    lowered_target = str(relationship_target or "").lower()
    descendant_names = {_xml_local_name(child.tag) for child in element.iter()}
    if "chart" in descendant_names or "/charts/" in lowered_target or "../charts/" in lowered_target:
        region_type = "chart"
    elif (
        "relIds" in descendant_names
        or "/diagrams/" in lowered_target
        or "../diagrams/" in lowered_target
    ):
        region_type = "diagram"

    properties = next(
        (child for child in element.iter() if _xml_local_name(child.tag) == "cNvPr"),
        None,
    )
    name = str(properties.attrib.get("name") or "") if properties is not None else ""
    description = str(
        properties.attrib.get("descr")
        or properties.attrib.get("title")
        or ""
    ) if properties is not None else ""
    semantic_hint = _explicit_visual_hint(name, description, region_type)

    transform = next(
        (child for child in element.iter() if _xml_local_name(child.tag) == "xfrm"),
        None,
    )
    transform_children = list(transform) if transform is not None else []
    offset = next(
        (child for child in transform_children if _xml_local_name(child.tag) == "off"),
        None,
    )
    extent = next(
        (child for child in transform_children if _xml_local_name(child.tag) == "ext"),
        None,
    )
    bbox_emu: dict[str, int] | None = None
    if offset is not None and extent is not None:
        try:
            bbox_emu = {
                "x": int(offset.attrib.get("x") or 0),
                "y": int(offset.attrib.get("y") or 0),
                "width": int(extent.attrib.get("cx") or 0),
                "height": int(extent.attrib.get("cy") or 0),
            }
        except (TypeError, ValueError):
            bbox_emu = None
        if bbox_emu and (bbox_emu["width"] <= 0 or bbox_emu["height"] <= 0):
            bbox_emu = None

    region: dict[str, object] = {
        "region_type": region_type,
        "coordinate_space": "slide_emu",
        "detection_method": "structured_ooxml_pptx_v1",
        "locator": {
            "slide": slide,
            "relationship_id": relationship_id,
            "relationship_target": relationship_target,
        },
        "label": (description or name)[:300],
        "semantic_hint": semantic_hint,
        "requires_human_confirmation": True,
    }
    if bbox_emu:
        region["bbox_emu"] = bbox_emu
        width = int((slide_size or {}).get("width_emu") or 0)
        height = int((slide_size or {}).get("height_emu") or 0)
        if width > 0 and height > 0:
            region["bbox"] = {
                "x": round(max(0.0, min(1.0, bbox_emu["x"] / width)), 6),
                "y": round(max(0.0, min(1.0, bbox_emu["y"] / height)), 6),
                "width": round(max(0.0, min(1.0, bbox_emu["width"] / width)), 6),
                "height": round(max(0.0, min(1.0, bbox_emu["height"] / height)), 6),
            }
            region["coordinate_space"] = "normalized_top_left"
    return region


def _xlsx_semantic_region(
    region_type: str,
    anchor: dict,
    *,
    label: object = "",
    semantic_hint: str | None = None,
) -> dict:
    return {
        "region_type": region_type,
        "coordinate_space": "spreadsheet_cells",
        "detection_method": "structured_ooxml_xlsx_v1",
        "bbox": {
            key: anchor.get(key)
            for key in (
                "from_row", "from_column", "from_cell",
                "to_row", "to_column", "to_cell", "extent_emu",
            )
            if anchor.get(key) is not None
        },
        "label": str(label or "")[:300],
        "semantic_hint": semantic_hint,
        "requires_human_confirmation": False,
    }


def _explicit_visual_hint(*values: object) -> str | None:
    text = " ".join(str(value or "").lower() for value in values)
    if any(term in text for term in ("tanda tangan", "signature", "signed by")):
        return "signature"
    if any(term in text for term in ("stempel", "cap dinas", "stamp", "seal")):
        return "stamp"
    if any(term in text for term in ("diagram", "alur", "flowchart", "process flow")):
        return "diagram"
    if "chart" in text or "grafik" in text:
        return "chart"
    return None


def parse_image(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    width, height = _image_dimensions(payload)
    return [
        _unit(
            "image-1",
            "image",
            1,
            "ocr_required",
            {"image": 1},
            warnings=["Gambar membutuhkan Visual/OCR Engine."],
            metadata={"size_bytes": len(payload), "width": width, "height": height},
        )
    ], {"file_kind": "image", "total_images": 1, "width": width, "height": height}


def parse_text(payload: bytes, analysis_mode: str) -> tuple[list[dict], dict]:
    decoded, encoding, replacement_count = _decode_text_payload(payload)
    lines = decoded.splitlines() or [""]
    max_segment_chars = 20_000
    segments = []
    for line_number, line in enumerate(lines, start=1):
        if len(line) <= max_segment_chars:
            segments.append((line_number, 1, max(1, len(line)), line))
            continue
        for offset in range(0, len(line), max_segment_chars):
            value = line[offset:offset + max_segment_chars]
            segments.append((line_number, offset + 1, offset + len(value), value))
    limit = _processing_limit(len(segments), "text", analysis_mode)
    units = []
    for index, (line_number, column_start, column_end, segment) in enumerate(segments, start=1):
        location = {
            "line_start": line_number,
            "line_end": line_number,
            "column_start": column_start,
            "column_end": column_end,
        }
        status = "processed" if index <= limit else "pending"
        units.append(
            _unit(
                f"line-{line_number}-segment-{column_start}",
                "text_line",
                index,
                status,
                location,
                text=normalize_text(segment) if status == "processed" else "",
                metadata={"encoding": encoding},
            )
        )
    warnings = []
    if replacement_count:
        warnings.append(f"{replacement_count} karakter tidak dapat didekode sempurna.")
    return units, {
        "file_kind": "text",
        "encoding": encoding,
        "decode_replacement_count": replacement_count,
        "total_lines": len(lines),
        "total_units": len(segments),
        "selected_units": limit,
        "warnings": warnings,
    }


def _decode_text_payload(payload: bytes) -> tuple[str, str, int]:
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        decoded = payload.decode("utf-16", errors="replace")
        return decoded, "utf-16", decoded.count("\ufffd")
    if payload.startswith(b"\xef\xbb\xbf"):
        decoded = payload.decode("utf-8-sig", errors="replace")
        return decoded, "utf-8-sig", decoded.count("\ufffd")
    try:
        return payload.decode("utf-8"), "utf-8", 0
    except UnicodeDecodeError:
        decoded = payload.decode("cp1252", errors="replace")
        return decoded, "cp1252", decoded.count("\ufffd")


def _unit(
    key: str,
    unit_type: str,
    ordinal: int,
    status: str,
    source_location: dict,
    *,
    text: str = "",
    heading_path: list[str] | None = None,
    warnings: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "unit_key": key,
        "unit_type": unit_type,
        "ordinal": ordinal,
        "heading_path": heading_path or [],
        "source_location": source_location,
        "text": text,
        "status": status,
        "warnings": warnings or [],
        "metadata": metadata or {},
    }


def _processing_limit(total: int, kind: str, mode: str) -> int:
    return total if mode == "full_audit" else min(total, SCREENING_LIMITS[kind])


def _bounded_text(text: str) -> tuple[str, str, list[str]]:
    if len(text) <= MAX_UNIT_TEXT_CHARS:
        return "processed", text, []
    return (
        "partial",
        text[:MAX_UNIT_TEXT_CHARS],
        [f"Teks unit dipotong pada {MAX_UNIT_TEXT_CHARS} karakter; unit harus dipecah sebelum full audit."],
    )


def _heading_guess(text: str) -> list[str]:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first and len(first) <= 180:
        return [first]
    return []


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _pdf_image_xobject_count(page) -> int:
    try:
        resources = page.get("/Resources") or {}
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        return _pdf_resource_image_count(resources, set(), 0)
    except Exception:
        return 0


def _pdf_resource_image_count(resources, seen: set[int], depth: int) -> int:
    if depth > 3 or not hasattr(resources, "get"):
        return 0
    xobjects = resources.get("/XObject") or {}
    xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
    if not hasattr(xobjects, "values"):
        return 0
    count = 0
    for reference in xobjects.values():
        value = reference.get_object() if hasattr(reference, "get_object") else reference
        marker = id(value)
        if marker in seen or not hasattr(value, "get"):
            continue
        seen.add(marker)
        subtype = str(value.get("/Subtype") or "")
        if subtype == "/Image":
            count += 1
        elif subtype == "/Form":
            nested = value.get("/Resources") or resources
            nested = nested.get_object() if hasattr(nested, "get_object") else nested
            count += _pdf_resource_image_count(nested, seen, depth + 1)
    return count


def _xml_text_nodes(xml_data: bytes) -> list[str]:
    root = ET.fromstring(xml_data)
    values = []
    for element in root.iter():
        local = element.tag.rsplit("}", 1)[-1]
        if local in {"t", "delText", "instrText"} and element.text:
            values.append(element.text)
    return values


def _docx_main_blocks(xml_data: bytes, relationships: dict[str, str] | None = None) -> list[dict]:
    root = ET.fromstring(xml_data)
    body = next((item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "body"), root)
    blocks = []
    heading_stack: list[str] = []
    table_index = 0
    for child in list(body):
        local = child.tag.rsplit("}", 1)[-1]
        if local not in {"p", "tbl"}:
            continue
        if local == "tbl":
            table_index += 1
            table_rows = [item for item in list(child) if item.tag.rsplit("}", 1)[-1] == "tr"]
            for row_index, row in enumerate(table_rows, start=1):
                cells = [item for item in list(row) if item.tag.rsplit("}", 1)[-1] == "tc"]
                cell_values = [
                    normalize_text(
                        " ".join(
                            node.text or ""
                            for node in cell.iter()
                            if node.tag.rsplit("}", 1)[-1] == "t"
                        )
                    )
                    for cell in cells
                ]
                fragments = [
                    f"C{cell_index}={value}"
                    for cell_index, value in enumerate(cell_values, start=1)
                    if value
                ]
                if not fragments:
                    continue
                rel_ids = {
                    value
                    for item in row.iter()
                    if item.tag.rsplit("}", 1)[-1] == "hyperlink"
                    for key, value in item.attrib.items()
                    if key.rsplit("}", 1)[-1] == "id"
                }
                blocks.append(
                    {
                        "type": "table_row",
                        "text": " | ".join(fragments),
                        "source": "word/document.xml",
                        "source_location": {"table": table_index, "row": row_index},
                        "heading_path": list(heading_stack),
                        "metadata": {
                            "table_index": table_index,
                            "table_row_index": row_index,
                            "table_row_count": len(table_rows),
                            "table_cell_count": len(cells),
                            "nonempty_cell_count": sum(bool(value) for value in cell_values),
                            "hyperlinks": sorted(
                                target
                                for rel_id in rel_ids
                                if (target := (relationships or {}).get(rel_id))
                            ),
                        },
                    }
                )
            continue
        text = normalize_text(" ".join(item.text or "" for item in child.iter() if item.tag.rsplit("}", 1)[-1] == "t"))
        if not text:
            continue
        block_type = "paragraph"
        style = next(
            (
                value
                for item in child.iter()
                if item.tag.rsplit("}", 1)[-1] == "pStyle"
                for key, value in item.attrib.items()
                if key.rsplit("}", 1)[-1] == "val"
            ),
            "",
        )
        heading_match = re.search(r"heading\s*(\d+)", style, flags=re.IGNORECASE)
        if heading_match:
            level = max(1, int(heading_match.group(1)))
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(text)
            block_type = "heading"
        rel_ids = {
            value
            for item in child.iter()
            if item.tag.rsplit("}", 1)[-1] == "hyperlink"
            for key, value in item.attrib.items()
            if key.rsplit("}", 1)[-1] == "id"
        }
        hyperlink_targets = sorted(
            target
            for rel_id in rel_ids
            if (target := (relationships or {}).get(rel_id))
        )
        blocks.append(
            {
                "type": block_type,
                "text": text,
                "source": "word/document.xml",
                "heading_path": list(heading_stack),
                "metadata": {
                    "style": style or None,
                    "hyperlinks": hyperlink_targets,
                    "table_row_count": 0,
                    "table_cell_count": 0,
                },
            }
        )
    return blocks


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    path = "xl/sharedStrings.xml"
    if path not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(path))
    return [
        normalize_text(" ".join(item.text or "" for item in si.iter() if item.tag.rsplit("}", 1)[-1] == "t"))
        for si in root
    ]


def _xlsx_sheet_inventory(archive: ZipFile) -> list[dict]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        item.attrib.get("Id"): item.attrib.get("Target", "")
        for item in rels
    }
    sheets = []
    for sheet in workbook.iter():
        if sheet.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        rel_id = next((value for key, value in sheet.attrib.items() if key.rsplit("}", 1)[-1] == "id"), "")
        target = rel_map.get(rel_id, "")
        if target.startswith("/"):
            path = posixpath.normpath(target.lstrip("/"))
        elif target.startswith("xl/"):
            path = posixpath.normpath(target)
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        sheets.append(
            {
                "name": sheet.attrib.get("name") or f"Sheet {len(sheets) + 1}",
                "state": sheet.attrib.get("state", "visible"),
                "path": path,
            }
        )
    return sheets


def _xlsx_defined_names(archive: ZipFile) -> list[dict]:
    root = ET.fromstring(archive.read("xl/workbook.xml"))
    values = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "definedName":
            continue
        local_sheet_id = item.attrib.get("localSheetId")
        values.append(
            {
                "name": item.attrib.get("name") or "",
                "value": normalize_text(item.text or ""),
                "local_sheet_id": int(local_sheet_id) if str(local_sheet_id or "").isdigit() else None,
                "hidden": item.attrib.get("hidden") == "1",
            }
        )
    return values


def _xlsx_sheet_rows(
    xml_data: bytes,
    shared_strings: list[str],
    relationships: dict[str, str] | None = None,
    comments: dict[str, str] | None = None,
) -> tuple[list[str], dict]:
    root = ET.fromstring(xml_data)
    rows = []
    value_cells = 0
    formula_cells = 0
    for row in root.iter():
        if row.tag.rsplit("}", 1)[-1] != "row":
            continue
        row_number = int(row.attrib.get("r") or len(rows) + 1)
        values = []
        for cell in row:
            if cell.tag.rsplit("}", 1)[-1] != "c":
                continue
            reference = cell.attrib.get("r") or "?"
            cell_type = cell.attrib.get("t") or ""
            raw_value = next((item.text or "" for item in cell if item.tag.rsplit("}", 1)[-1] == "v"), "")
            inline_value = normalize_text(" ".join(item.text or "" for item in cell.iter() if item.tag.rsplit("}", 1)[-1] == "t"))
            formula = next((item.text or "" for item in cell if item.tag.rsplit("}", 1)[-1] == "f"), "")
            if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared_strings):
                value = shared_strings[int(raw_value)]
            elif cell_type == "inlineStr":
                value = inline_value
            else:
                value = raw_value or inline_value
            value = normalize_text(value)
            formula = normalize_text(formula)
            comment = normalize_text((comments or {}).get(reference) or "")
            if not value and not formula and not comment:
                continue
            value_cells += 1
            if formula:
                formula_cells += 1
            fragment = f"{reference}={value}" if value else reference
            if formula:
                fragment += f" [formula: {formula}]"
            if comment:
                fragment += f" [comment: {comment}]"
            values.append(fragment)
        if values:
            rows.append(f"Baris {row_number}: " + " | ".join(values))
    merge_ranges = [
        item.attrib.get("ref")
        for item in root.iter()
        if item.tag.rsplit("}", 1)[-1] == "mergeCell" and item.attrib.get("ref")
    ]
    hyperlinks = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "hyperlink":
            continue
        relationship_id = next(
            (value for key, value in item.attrib.items() if key.rsplit("}", 1)[-1] == "id"),
            None,
        )
        hyperlinks.append(
            {
                "cell": item.attrib.get("ref"),
                "relationship_id": relationship_id,
                "target": (relationships or {}).get(relationship_id or ""),
                "location": item.attrib.get("location"),
                "display": item.attrib.get("display"),
                "tooltip": item.attrib.get("tooltip"),
            }
        )
    dimension_ref = next(
        (
            item.attrib.get("ref")
            for item in root.iter()
            if item.tag.rsplit("}", 1)[-1] == "dimension" and item.attrib.get("ref")
        ),
        None,
    )
    return rows, {
        "dimension_ref": dimension_ref,
        "value_rows": len(rows),
        "value_cells": value_cells,
        "formula_cells": formula_cells,
        "merged_ranges": merge_ranges,
        "hyperlinks": hyperlinks,
        "comment_cells": sorted((comments or {}).keys()),
    }


def _xlsx_risk_matrix_features(
    xml_data,
    shared_strings: list[str],
    *,
    row_limit: int,
) -> dict:
    """Profile bounded worksheet rows before normal screening selection.

    The profile is structural and deterministic.  It never promotes a Grade and
    keeps the adaptive screening budget bounded even when a workbook has many
    sheets.
    """
    source = BytesIO(xml_data) if isinstance(xml_data, bytes) else xml_data
    rows: list[tuple[int, dict[int, str], set[int]]] = []
    formula_cell_count = 0
    literal_value_cell_count = 0
    total_value_cell_count = 0
    dimension_ref = None
    for _event, element in ET.iterparse(source, events=("end",)):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "dimension" and element.attrib.get("ref"):
            dimension_ref = element.attrib.get("ref")
            element.clear()
            continue
        if tag != "row":
            continue
        if len(rows) >= max(1, row_limit):
            break
        row_number = int(element.attrib.get("r") or len(rows) + 1)
        values: dict[int, str] = {}
        formula_columns: set[int] = set()
        for cell in element:
            if cell.tag.rsplit("}", 1)[-1] != "c":
                continue
            reference = str(cell.attrib.get("r") or "")
            column = _xlsx_column_number(reference)
            if column <= 0:
                continue
            value, formula = _xlsx_profile_cell_value(cell, shared_strings)
            if formula:
                formula_cell_count += 1
                formula_columns.add(column)
            if value:
                literal_value_cell_count += int(not formula)
                total_value_cell_count += 1
                values[column] = value
        if values or formula_columns:
            rows.append((row_number, values, formula_columns))
        element.clear()

    header_row_number = None
    header_categories: set[str] = set()
    header_columns: dict[str, int] = {}
    for row_number, values, _formulas in rows[:20]:
        candidate_columns: dict[str, int] = {}
        for column, value in values.items():
            normalized = " ".join(value.casefold().split())
            for category, aliases in RISK_MATRIX_HEADER_ALIASES.items():
                if any(alias == normalized or alias in normalized for alias in aliases):
                    candidate_columns.setdefault(category, column)
        if len(candidate_columns) > len(header_categories):
            header_row_number = row_number
            header_categories = set(candidate_columns)
            header_columns = candidate_columns

    placeholder_count = 0
    considered_value_count = 0
    substantive_row_count = 0
    risk_statement_values: set[str] = set()
    filled_rtp_count = 0
    filled_likelihood_count = 0
    filled_impact_count = 0
    filled_risk_owner_count = 0
    contains_example_data = False
    placeholder_re = re.compile(
        r"^(?:[-–—_. ]+|x{2,}|n/?a|tbd|isi(?:lah)?|diisi|contoh(?: pengisian)?)$",
        flags=re.IGNORECASE,
    )
    for row_number, values, _formulas in rows:
        if header_row_number is None or row_number <= header_row_number:
            continue
        normalized_values = {
            column: " ".join(value.casefold().split())
            for column, value in values.items()
            if value.strip()
        }
        if not normalized_values:
            continue
        placeholders = {
            column for column, value in normalized_values.items()
            if placeholder_re.fullmatch(value) or "contoh pengisian" in value
        }
        placeholder_count += len(placeholders)
        considered_value_count += len(normalized_values)
        contains_example_data = contains_example_data or any(
            "contoh" in value or "petunjuk" in value for value in normalized_values.values()
        )
        risk_value = normalized_values.get(header_columns.get("risk_statement", -1), "")
        cause_value = normalized_values.get(header_columns.get("cause", -1), "")
        impact_value = normalized_values.get(header_columns.get("impact", -1), "")
        core_values = [value for value in (risk_value, cause_value, impact_value) if value]
        non_placeholder_values = [
            value for column, value in normalized_values.items()
            if column not in placeholders
        ]
        is_substantive = bool(
            len(non_placeholder_values) >= 3
            and core_values
            and len(placeholders) < max(1, len(normalized_values) / 2)
            and not all("contoh" in value for value in core_values)
        )
        if not is_substantive:
            continue
        substantive_row_count += 1
        if risk_value and "contoh" not in risk_value:
            risk_statement_values.add(risk_value)
        filled_rtp_count += int(bool(
            normalized_values.get(header_columns.get("control_plan", -1), "")
        ))
        filled_likelihood_count += int(bool(
            normalized_values.get(header_columns.get("likelihood", -1), "")
        ))
        filled_impact_count += int(bool(impact_value))
        filled_risk_owner_count += int(bool(
            normalized_values.get(header_columns.get("risk_owner", -1), "")
        ))

    max_column = max(
        (max(values, default=0) for _row_number, values, _formulas in rows),
        default=0,
    )
    filled_denominator = max(1, len(rows) * max_column)
    placeholder_ratio = placeholder_count / max(1, considered_value_count)
    header_count = len(header_categories)
    return {
        "dimension_ref": dimension_ref,
        "profiled_row_count": len(rows),
        "profiled_cell_count": total_value_cell_count,
        "risk_matrix_relevant": header_count >= 3,
        "risk_header_categories": sorted(header_categories),
        "risk_header_category_count": header_count,
        "risk_header_row": header_row_number,
        "substantive_row_count": substantive_row_count,
        "risk_statement_values": sorted(risk_statement_values)[:100],
        "filled_rtp_count": filled_rtp_count,
        "filled_likelihood_count": filled_likelihood_count,
        "filled_impact_count": filled_impact_count,
        "filled_risk_owner_count": filled_risk_owner_count,
        "filled_cell_ratio": round(total_value_cell_count / filled_denominator, 4),
        "placeholder_ratio": round(placeholder_ratio, 4),
        "contains_example_data": contains_example_data,
        "formula_only": bool(formula_cell_count and not literal_value_cell_count),
        "formula_cell_count": formula_cell_count,
    }


def _xlsx_profile_cell_value(
    cell: ET.Element,
    shared_strings: list[str],
) -> tuple[str, str]:
    cell_type = str(cell.attrib.get("t") or "")
    raw_value = next(
        (item.text or "" for item in cell if item.tag.rsplit("}", 1)[-1] == "v"),
        "",
    )
    inline_value = normalize_text(" ".join(
        item.text or "" for item in cell.iter()
        if item.tag.rsplit("}", 1)[-1] == "t"
    ))
    formula = normalize_text(next(
        (item.text or "" for item in cell if item.tag.rsplit("}", 1)[-1] == "f"),
        "",
    ))
    if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared_strings):
        value = shared_strings[int(raw_value)]
    elif cell_type == "inlineStr":
        value = inline_value
    else:
        value = raw_value or inline_value
    return normalize_text(value), formula


def _xlsx_column_number(reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", reference)
    if not match:
        return 0
    value = 0
    for character in match.group(1).upper():
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _relationship_targets(xml_data: bytes | None) -> dict[str, str]:
    if not xml_data:
        return {}
    root = ET.fromstring(xml_data)
    return {
        item.attrib.get("Id", ""): item.attrib.get("Target", "")
        for item in root
        if item.attrib.get("Id") and item.attrib.get("Target")
    }


def _ooxml_relationships_path(part_path: str) -> str:
    directory, filename = posixpath.split(part_path)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def _resolve_ooxml_target(part_path: str, target: str) -> str:
    normalized = str(target or "").strip()
    if normalized.startswith(("http://", "https://", "mailto:", "#")):
        return normalized
    if normalized.startswith("/"):
        return posixpath.normpath(normalized.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(part_path), normalized))


def _xlsx_comments(xml_data: bytes) -> dict[str, str]:
    root = ET.fromstring(xml_data)
    comments = {}
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "comment":
            continue
        reference = item.attrib.get("ref")
        text = normalize_text(
            " ".join(
                node.text or ""
                for node in item.iter()
                if node.tag.rsplit("}", 1)[-1] == "t"
            )
        )
        if reference and text:
            comments[reference] = text
    return comments


_XLSX_CHART_TYPES = {
    "area3DChart", "areaChart", "bar3DChart", "barChart", "bubbleChart",
    "doughnutChart", "line3DChart", "lineChart", "ofPieChart", "pie3DChart",
    "pieChart", "radarChart", "scatterChart", "stockChart", "surface3DChart",
    "surfaceChart",
}


def _xlsx_drawing_items(xml_data: bytes) -> dict:
    root = ET.fromstring(xml_data)
    shapes: list[dict] = []
    charts: dict[str, dict] = {}
    for anchor in root:
        local = _xml_local_name(anchor.tag)
        if local not in {"oneCellAnchor", "twoCellAnchor", "absoluteAnchor"}:
            continue
        anchor_info = _xlsx_anchor_info(anchor)
        for node in anchor:
            node_local = _xml_local_name(node.tag)
            if node_local == "sp":
                text = normalize_text(
                    " ".join(
                        child.text or ""
                        for child in node.iter()
                        if _xml_local_name(child.tag) == "t"
                    )
                )
                if not text:
                    continue
                name = next(
                    (
                        child.attrib.get("name")
                        for child in node.iter()
                        if _xml_local_name(child.tag) == "cNvPr" and child.attrib.get("name")
                    ),
                    None,
                )
                shape_type = next(
                    (
                        child.attrib.get("prst")
                        for child in node.iter()
                        if _xml_local_name(child.tag) == "prstGeom" and child.attrib.get("prst")
                    ),
                    None,
                )
                shapes.append({
                    "text": text,
                    "name": name,
                    "shape_type": shape_type,
                    "anchor": anchor_info,
                })
            elif node_local == "graphicFrame":
                relationship_id = next(
                    (
                        value
                        for child in node.iter()
                        if _xml_local_name(child.tag) == "chart"
                        for key, value in child.attrib.items()
                        if _xml_local_name(key) == "id"
                    ),
                    None,
                )
                if not relationship_id:
                    continue
                name = next(
                    (
                        child.attrib.get("name")
                        for child in node.iter()
                        if _xml_local_name(child.tag) == "cNvPr" and child.attrib.get("name")
                    ),
                    None,
                )
                charts[str(relationship_id)] = {**anchor_info, "drawing_name": name}
    return {"shapes": shapes, "charts": charts}


def _xlsx_anchor_info(anchor: ET.Element) -> dict:
    def marker(name: str) -> tuple[int | None, int | None]:
        marker_node = next(
            (child for child in anchor if _xml_local_name(child.tag) == name),
            None,
        )
        if marker_node is None:
            return None, None
        values = {
            _xml_local_name(child.tag): int((child.text or "0").strip() or 0)
            for child in marker_node
            if _xml_local_name(child.tag) in {"row", "col"}
        }
        return values.get("row"), values.get("col")

    from_row, from_column = marker("from")
    to_row, to_column = marker("to")
    result: dict[str, object] = {}
    if from_row is not None and from_column is not None:
        result.update({
            "from_row": from_row + 1,
            "from_column": from_column + 1,
            "from_cell": _xlsx_cell_reference(from_row, from_column),
        })
    if to_row is not None and to_column is not None:
        result.update({
            "to_row": to_row + 1,
            "to_column": to_column + 1,
            "to_cell": _xlsx_cell_reference(to_row, to_column),
        })
    ext = next((child for child in anchor if _xml_local_name(child.tag) == "ext"), None)
    if ext is not None:
        result["extent_emu"] = {
            key: int(value)
            for key, value in ext.attrib.items()
            if key in {"cx", "cy"} and str(value).isdigit()
        }
    return result


def _xlsx_cell_reference(row_zero_based: int, column_zero_based: int) -> str:
    value = max(0, int(column_zero_based)) + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{max(0, int(row_zero_based)) + 1}"


def _xlsx_chart_semantics(xml_data: bytes) -> dict:
    root = ET.fromstring(xml_data)
    chart_type = next(
        (_xml_local_name(item.tag) for item in root.iter() if _xml_local_name(item.tag) in _XLSX_CHART_TYPES),
        None,
    )
    title_node = next((item for item in root.iter() if _xml_local_name(item.tag) == "title"), None)
    title = _xml_descendant_values(title_node, {"t", "v"})[0] if title_node is not None and _xml_descendant_values(title_node, {"t", "v"}) else None
    series = []
    for index, series_node in enumerate(
        (item for item in root.iter() if _xml_local_name(item.tag) == "ser"),
        start=1,
    ):
        name_node = next(
            (item for item in series_node if _xml_local_name(item.tag) == "tx"),
            None,
        )
        name_values = _xml_descendant_values(name_node, {"t", "v"}) if name_node is not None else []
        categories = _xlsx_chart_component(series_node, {"cat", "xVal"})
        values = _xlsx_chart_component(series_node, {"val", "yVal"})
        bubble_sizes = _xlsx_chart_component(series_node, {"bubbleSize"})
        formulas = []
        for component in (categories, values, bubble_sizes):
            for formula in component.get("formulas") or []:
                if formula not in formulas:
                    formulas.append(formula)
        item = {
            "index": index,
            "name": name_values[0] if name_values else None,
            "formulas": formulas,
            "categories": categories.get("cached_values") or [],
            "values": values.get("cached_values") or [],
        }
        if bubble_sizes.get("cached_values"):
            item["bubble_sizes"] = bubble_sizes["cached_values"]
        series.append(item)
    text_parts = []
    if title:
        text_parts.append(f"Judul chart: {title}")
    if chart_type:
        text_parts.append(f"Jenis chart: {chart_type}")
    for item in series:
        label = item.get("name") or f"Series {item['index']}"
        detail = []
        if item.get("categories"):
            detail.append("kategori=" + ", ".join(item["categories"][:50]))
        if item.get("values"):
            detail.append("nilai=" + ", ".join(item["values"][:50]))
        if item.get("formulas"):
            detail.append("sumber=" + ", ".join(item["formulas"][:10]))
        text_parts.append(f"{label}: " + ("; ".join(detail) if detail else "tanpa cache/reference"))
    return {
        "chart_type": chart_type,
        "title": title,
        "series_count": len(series),
        "series": series,
        "text": normalize_text(" | ".join(text_parts)),
    }


def _xlsx_chart_component(series_node: ET.Element, component_names: set[str]) -> dict:
    component = next(
        (item for item in series_node.iter() if _xml_local_name(item.tag) in component_names),
        None,
    )
    if component is None:
        return {"formulas": [], "cached_values": []}
    return {
        "formulas": _xml_descendant_values(component, {"f"})[:20],
        "cached_values": _xml_descendant_values(component, {"v", "t"})[:200],
    }


def _xml_descendant_values(node: ET.Element | None, local_names: set[str]) -> list[str]:
    if node is None:
        return []
    values: list[str] = []
    for item in node.iter():
        if _xml_local_name(item.tag) not in local_names:
            continue
        value = normalize_text(item.text or "")
        if value and value not in values:
            values.append(value)
    return values


def _xml_local_name(value: str) -> str:
    return str(value).rsplit("}", 1)[-1]


def _xlsx_chart_text(xml_data: bytes) -> str:
    return str(_xlsx_chart_semantics(xml_data).get("text") or "")


def _image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")
    if payload.startswith((b"GIF87a", b"GIF89a")) and len(payload) >= 10:
        return int.from_bytes(payload[6:8], "little"), int.from_bytes(payload[8:10], "little")
    if payload.startswith(b"BM") and len(payload) >= 26:
        return int.from_bytes(payload[18:22], "little"), abs(int.from_bytes(payload[22:26], "little", signed=True))
    if payload.startswith(b"\xff\xd8\xff"):
        offset = 2
        while offset + 9 <= len(payload):
            if payload[offset] != 0xFF:
                offset += 1
                continue
            marker = payload[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(payload):
                break
            length = int.from_bytes(payload[offset:offset + 2], "big")
            if length < 2 or offset + length > len(payload):
                break
            if marker in {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            } and length >= 7:
                height = int.from_bytes(payload[offset + 3:offset + 5], "big")
                width = int.from_bytes(payload[offset + 5:offset + 7], "big")
                return width, height
            offset += length
    return None, None


def _numeric_path_key(path: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.xml$)", path)
    return (int(match.group(1)) if match else 0, path)
