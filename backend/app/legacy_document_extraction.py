from __future__ import annotations

from io import BytesIO
import posixpath
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.legacy_text_utils import clean_ai_text, has_any_keyword, keyword_hits, normalize_text


TEXT_EXTENSIONS = {".csv", ".htm", ".html", ".json", ".md", ".rtf", ".text", ".txt", ".xml"}


IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


PPTX_EXTENSIONS = {".pptx"}


ANALYSIS_MODES = {
    "fast": {
        "label": "Mode Cepat",
        "description": "Nama file dan cuplikan awal untuk screening cepat dengan biaya rendah.",
        "prompt_char_limit": 1000,
        "read_limit": 24000,
        "pdf_page_limit": 5,
        "pdf_strategy": "awal",
        "xlsx_sheet_limit": 4,
        "pptx_slide_limit": 6,
        "candidate_limit": 3,
        "expected_output_tokens": 700,
    },
    "deep": {
        "label": "Mode Mendalam",
        "description": "Cuplikan awal, tengah, akhir, dan halaman kunci untuk akurasi lebih baik.",
        "prompt_char_limit": 3500,
        "read_limit": 80000,
        "pdf_page_limit": 12,
        "pdf_strategy": "sampel",
        "xlsx_sheet_limit": 8,
        "pptx_slide_limit": 12,
        "candidate_limit": 6,
        "expected_output_tokens": 900,
    },
    "full": {
        "label": "Mode Penuh",
        "description": "Audit struktur dokumen terpanjang: baris, kolom evidence, hyperlink, dan konteks lampiran.",
        "prompt_char_limit": 12000,
        "read_limit": 320000,
        "pdf_page_limit": 40,
        "pdf_strategy": "berurutan",
        "xlsx_sheet_limit": 50,
        "pptx_slide_limit": 30,
        "candidate_limit": 8,
        "expected_output_tokens": 1400,
    },
}


DEFAULT_ANALYSIS_MODE = "fast"


READ_LIMIT = 24000


RISK_DOCUMENT_KEYWORDS = [
    "peta risiko", "matriks risiko", "register risiko", "profil risiko", "rtp",
    "rencana tindak pengendalian", "risiko residual", "mitigasi",
    "risiko strategis", "risiko operasional", "manajemen risiko",
]


URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)


XLSX_EVIDENCE_COLUMN_KEYWORDS = [
    "dokumentasi", "evidence", "bukti", "link", "tautan", "url", "lampiran",
    "dokumen pendukung", "evidence rtp", "dokumentasi evidence rtp",
    "dokumentasi/ evidence rtp", "dokumentasi / evidence rtp",
    "progres rtp", "progress rtp", "realisasi rtp",
    "rencana tindak pengendalian", "tindak pengendalian",
]


XLSX_EVIDENCE_ROW_KEYWORDS = [
    "undangan", "notulen", "notulensi", "daftar hadir", "bahan paparan", "sosialisasi",
    "laporan triwulan", "laporan semester", "laporan monev", "instrumen monev",
    "monitoring", "pemantauan", "evaluasi", "reviu", "review", "progres rtp", "progress rtp",
    "dokumen perencanaan", "matriks", "peta risiko", "rtp", "laporan", "bukti", "dokumentasi",
    "google.com", "drive.google", "docs.google",
]


XLSX_REFERENCE_POLICY_KEYWORDS = [
    "kepmen", "keputusan", "kepdirjen", "sk ", "dokumen perencanaan",
    "pedoman", "peraturan", "permendesa", "sop",
]


XLSX_REFERENCE_SOCIALIZATION_KEYWORDS = [
    "undangan", "notulensi", "notulen", "daftar hadir", "sosialisasi",
    "materi sosialisasi", "bahan paparan",
]


XLSX_REFERENCE_IMPLEMENTATION_KEYWORDS = [
    "matriks", "peta risiko", "register risiko", "rtp",
    "rencana tindak pengendalian", "dokumen perencanaan",
    "progres rtp", "progress rtp", "penetapan konteks",
]


XLSX_REFERENCE_EVALUATION_KEYWORDS = [
    "laporan triwulan", "laporan semester", "instrumen monev", "laporan monev",
    "monitoring", "pemantauan", "evaluasi", "reviu", "review",
]


XLSX_REFERENCE_IMPROVEMENT_KEYWORDS = [
    "tindak lanjut hasil evaluasi", "hasil evaluasi ditindaklanjuti",
    "revisi berdasarkan hasil evaluasi", "bukti perbaikan",
    "laporan tindak lanjut", "perbaikan organisasi",
]


def extract_preview_text(file_name: str, content_type: str | None, payload: bytes, allow_full_document: bool, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> dict:
    lowered = file_name.lower()
    extension = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    mode_key, mode_config = normalize_analysis_mode(analysis_mode)
    try:
        if extension == ".pdf" or content_type == "application/pdf":
            return extract_pdf_text(payload, mode_config)
        if extension == ".docx":
            return extract_docx_text(payload, mode_config)
        if extension == ".xlsx":
            return extract_xlsx_text(payload, mode_config)
        if extension in PPTX_EXTENSIONS:
            return extract_pptx_text(payload, mode_config)
        if extension in IMAGE_EXTENSIONS or (content_type or "").startswith("image/"):
            return extract_image_metadata(file_name, content_type, payload)
        if extension in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
            return extract_plain_text(payload, allow_full_document, mode_config)
    except Exception as exc:  # keep upload analysis resilient for malformed files
        return {"status": "partial", "method": extension.lstrip(".") or "unknown", "text": "", "message": f"Ekstraksi gagal: {exc}"}
    return {"status": "unsupported", "method": "metadata_only", "text": "", "message": "Tipe file belum didukung untuk ekstraksi teks penuh."}


def extract_plain_text(payload: bytes, allow_full_document: bool, mode_config: dict) -> dict:
    read_limit = len(payload) if allow_full_document else min(len(payload), mode_config["read_limit"])
    decoded = normalize_text(payload[:read_limit].decode("utf-8", errors="ignore"))
    sent_text = decoded[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok",
        "method": "plain_text",
        "text": sent_text,
        "message": None,
        "extracted_char_count": len(decoded),
        "sent_char_count": len(sent_text),
    }


def extract_pdf_text(payload: bytes, mode_config: dict) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "unsupported", "method": "pdf", "text": "", "message": "Dependency pypdf belum terpasang."}
    reader = PdfReader(BytesIO(payload))
    total_pages = len(reader.pages)
    page_indexes = selected_pdf_pages(total_pages, mode_config)
    parts = []
    page_summaries: list[dict] = []
    scanned_count = 0
    for index in page_indexes:
        raw_text = reader.pages[index].extract_text() or ""
        page_text = normalize_text(raw_text)
        if page_text:
            page_number = index + 1
            parts.append(f"Halaman {page_number}: {page_text}")
            page_summaries.append(
                {
                    "page": page_number,
                    "char_count": len(page_text),
                    "sample": clean_ai_text(page_text, 700),
                }
            )
        else:
            parts.append("")
        scanned_count += 1
        if sum(len(part) for part in parts) > mode_config["read_limit"]:
            break
    text = normalize_text(" ".join(parts))
    sent_text = text[: mode_config["prompt_char_limit"]]
    non_empty_pages = sum(1 for part in parts if normalize_text(part))
    density = round(len(text) / max(1, scanned_count), 1)
    quality_warning = None
    if total_pages >= 10 and (len(text) < 1200 or density < 80):
        quality_warning = (
            "Text layer PDF sangat rendah. Hasil rekomendasi memakai teks yang berhasil diekstrak; "
            "aktifkan OCR untuk membaca isi scan/gambar secara penuh."
        )
    return {
        "status": "ok" if text else "partial",
        "method": "pdf",
        "text": sent_text,
        "message": None if text else "PDF terbaca, tetapi teks tidak ditemukan.",
        "total_pages": total_pages,
        "scanned_pages": scanned_count,
        "scanned_text_pages": non_empty_pages,
        "page_summaries": page_summaries,
        "text_density_chars_per_page": density,
        "page_strategy": mode_config["pdf_strategy"],
        "quality_warning": quality_warning,
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_docx_text(payload: bytes, mode_config: dict) -> dict:
    with ZipFile(BytesIO(payload)) as archive:
        parts, section_summaries = extract_docx_parts(archive, mode_config["read_limit"])
    text = normalize_text(" ".join(parts))[: mode_config["read_limit"]]
    sent_text = text[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok" if text else "partial",
        "method": "docx",
        "text": sent_text,
        "message": None if text else "DOCX terbaca, tetapi teks tidak ditemukan.",
        "section_summaries": section_summaries,
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_docx_parts(archive: ZipFile, read_limit: int) -> tuple[list[str], list[dict]]:
    names = set(archive.namelist())
    ordered_parts: list[tuple[str, str]] = []
    if "word/document.xml" in names:
        ordered_parts.append(("word/document.xml", "Isi utama dokumen"))
    for prefix, label in (
        ("word/header", "Header dokumen"),
        ("word/footer", "Footer dokumen"),
        ("word/footnotes", "Catatan kaki"),
        ("word/endnotes", "Catatan akhir"),
        ("word/comments", "Komentar dokumen"),
    ):
        for name in sorted(item for item in names if item.startswith(prefix) and item.endswith(".xml")):
            ordered_parts.append((name, label))

    values: list[str] = []
    summaries: list[dict] = []
    total_chars = 0
    for part_path, label in ordered_parts:
        if total_chars >= read_limit:
            break
        part_values = extract_xml_text_nodes(archive.read(part_path))
        part_text = normalize_text(" ".join(part_values))
        if not part_text:
            continue
        remaining = max(0, read_limit - total_chars)
        used_text = part_text[:remaining]
        values.append(f"{label}: {used_text}")
        summaries.append(
            {
                "name": label,
                "source": part_path,
                "char_count": len(part_text),
                "sample": clean_ai_text(part_text, 700),
            }
        )
        total_chars += len(used_text)
    return values, summaries


def extract_xlsx_text(payload: bytes, mode_config: dict) -> dict:
    values: list[str] = []
    sheet_summaries: list[dict] = []
    total_sheets = 0
    scanned_sheets = 0
    total_rows = 0
    scanned_rows = 0
    all_evidence_rows: list[dict] = []
    all_evidence_links: list[dict] = []
    evidence_columns_by_sheet: dict[str, list[str]] = {}
    try:
        with ZipFile(BytesIO(payload)) as archive:
            shared_strings = read_xlsx_shared_strings(archive)
            sheet_map = read_xlsx_sheet_map(archive)
            sheet_paths = sorted(
                (name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
                key=office_sort_key,
            )
            total_sheets = len(sheet_paths)
            total_chars = 0
            for sheet_path in sheet_paths[: mode_config["xlsx_sheet_limit"]]:
                scanned_sheets += 1
                sheet_name = sheet_map.get(sheet_path) or sheet_path.rsplit("/", 1)[-1].removesuffix(".xml")
                sheet_result = extract_xlsx_sheet_structured(
                    archive,
                    sheet_path,
                    sheet_name,
                    shared_strings,
                    mode_config["read_limit"] - total_chars,
                )
                total_rows += sheet_result["total_rows"]
                scanned_rows += sheet_result["value_rows"]
                all_evidence_rows.extend(sheet_result["evidence_rows"])
                all_evidence_links.extend(sheet_result["evidence_links"])
                if sheet_result["evidence_columns"]:
                    evidence_columns_by_sheet[sheet_name] = sheet_result["evidence_columns"]
                if sheet_result["rows_text"]:
                    sheet_text = normalize_text(" ".join(sheet_result["rows_text"]))
                    values.append(f"Sheet {sheet_name}: {sheet_text}")
                    sheet_summaries.append(
                        {
                            "name": sheet_name,
                            "source": sheet_path,
                            "row_count": sheet_result["total_rows"],
                            "value_row_count": sheet_result["value_rows"],
                            "evidence_row_count": len(sheet_result["evidence_rows"]),
                            "hyperlink_count": len(sheet_result["evidence_links"]),
                            "evidence_columns": sheet_result["evidence_columns"],
                            "char_count": len(sheet_text),
                            "sample": clean_ai_text(sheet_text, 700),
                        }
                    )
                    total_chars += len(sheet_text)
                if total_chars > mode_config["read_limit"]:
                    break
    except BadZipFile as exc:
        raise ValueError("XLSX bukan arsip zip valid") from exc
    evidence_block = build_xlsx_evidence_block(all_evidence_rows, all_evidence_links)
    text = normalize_text(" ".join(item for item in (evidence_block, *values) if item))
    sent_text = text[: mode_config["prompt_char_limit"]]
    evidence_column_summary = "; ".join(
        f"{sheet}: {', '.join(columns[:8])}"
        for sheet, columns in list(evidence_columns_by_sheet.items())[:8]
    )
    structural_summary = (
        f"Terbaca {scanned_sheets}/{total_sheets} sheet, {scanned_rows} baris berisi nilai "
        f"dari {total_rows} baris XML, {len(all_evidence_rows)} baris evidence/RTP, "
        f"dan {len(all_evidence_links)} hyperlink evidence."
    )
    if evidence_column_summary:
        structural_summary = f"{structural_summary} Kolom evidence: {evidence_column_summary}."
    return {
        "status": "ok" if text else "partial",
        "method": "xlsx",
        "text": sent_text,
        "message": None if text else "XLSX terbaca, tetapi teks tidak ditemukan.",
        "total_sheets": total_sheets,
        "scanned_sheets": scanned_sheets,
        "total_rows": total_rows,
        "scanned_rows": scanned_rows,
        "evidence_row_count": len(all_evidence_rows),
        "hyperlink_count": len(all_evidence_links),
        "structural_summary": structural_summary,
        "sheet_summaries": sheet_summaries,
        "evidence_rows": all_evidence_rows[:160],
        "evidence_links": all_evidence_links[:200],
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_pptx_text(payload: bytes, mode_config: dict) -> dict:
    slide_summaries: list[dict] = []
    section_summaries: list[dict] = []
    values: list[str] = []
    try:
        with ZipFile(BytesIO(payload)) as archive:
            slide_paths = sorted(
                (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
                key=office_sort_key,
            )
            total_slides = len(slide_paths)
            total_chars = 0
            for slide_path in slide_paths[: mode_config["pptx_slide_limit"]]:
                slide_number = office_sort_key(slide_path)[0]
                slide_values = extract_xml_text_nodes(archive.read(slide_path))
                slide_text = normalize_text(" ".join(slide_values))
                notes_text = extract_pptx_notes_text(archive, slide_number)
                slide_name = slide_path.rsplit("/", 1)[-1].removesuffix(".xml")
                combined_text = normalize_text(
                    " ".join(
                        item
                        for item in (
                            slide_text,
                            f"Catatan presenter: {notes_text}" if notes_text else "",
                        )
                        if item
                    )
                )
                if combined_text:
                    values.append(f"Slide {slide_name}: {combined_text}")
                    slide_summaries.append(
                        {
                            "name": slide_name,
                            "source": slide_path,
                            "char_count": len(combined_text),
                            "notes_char_count": len(notes_text),
                            "sample": clean_ai_text(combined_text, 700),
                        }
                    )
                    section_summaries.append(
                        {
                            "name": f"Slide {slide_number}",
                            "source": slide_path,
                            "char_count": len(combined_text),
                            "sample": clean_ai_text(combined_text, 700),
                        }
                    )
                    total_chars += len(combined_text)
                if total_chars > mode_config["read_limit"]:
                    break
    except BadZipFile as exc:
        raise ValueError("PPTX bukan arsip zip valid") from exc
    text = normalize_text(" ".join(values))
    sent_text = text[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok" if text else "partial",
        "method": "pptx",
        "text": sent_text,
        "message": None if text else "PPTX terbaca, tetapi teks tidak ditemukan.",
        "total_slides": total_slides,
        "scanned_slides": len(slide_summaries),
        "slide_summaries": slide_summaries,
        "section_summaries": section_summaries,
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_pptx_notes_text(archive: ZipFile, slide_number: int) -> str:
    names = set(archive.namelist())
    direct_path = f"ppt/notesSlides/notesSlide{slide_number}.xml"
    if direct_path in names:
        return normalize_text(" ".join(extract_xml_text_nodes(archive.read(direct_path))))

    slide_rels_path = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
    if slide_rels_path not in names:
        return ""
    try:
        rel_root = ET.fromstring(archive.read(slide_rels_path))
    except ET.ParseError:
        return ""
    for rel in rel_root:
        target = rel.attrib.get("Target", "")
        rel_type = rel.attrib.get("Type", "")
        if "notesSlide" not in target and "notesSlide" not in rel_type:
            continue
        note_path = normalize_pptx_target("ppt/slides", target)
        if note_path in names:
            return normalize_text(" ".join(extract_xml_text_nodes(archive.read(note_path))))
    return ""


def normalize_pptx_target(base_dir: str, target: str) -> str:
    target = target.strip()
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(base_dir, target))


def extract_image_metadata(file_name: str, content_type: str | None, payload: bytes) -> dict:
    text = normalize_text(
        f"File gambar evidence. Nama file: {file_name}. Tipe: {content_type or 'image'}. Ukuran: {len(payload)} byte."
    )
    return {
        "status": "partial",
        "method": "image_metadata",
        "text": text,
        "message": "Gambar belum dibaca dengan OCR; analisis memakai nama file dan metadata.",
        "quality_warning": "OCR gambar belum aktif, sehingga isi visual belum dianalisis penuh.",
        "extracted_char_count": len(text),
        "sent_char_count": len(text),
    }


def normalize_analysis_mode(value: str | None) -> tuple[str, dict]:
    key = str(value or DEFAULT_ANALYSIS_MODE).strip().lower()
    if key not in ANALYSIS_MODES:
        key = DEFAULT_ANALYSIS_MODE
    return key, ANALYSIS_MODES[key]


def selected_pdf_pages(total_pages: int, mode_config: dict) -> list[int]:
    if total_pages <= 0:
        return []
    limit = min(total_pages, mode_config["pdf_page_limit"])
    strategy = mode_config["pdf_strategy"]
    if strategy == "awal" or total_pages <= limit:
        return list(range(limit))
    if strategy == "berurutan":
        return list(range(limit))

    selected = set(range(min(3, total_pages)))
    tail_start = max(0, total_pages - 3)
    selected.update(range(tail_start, total_pages))
    middle = total_pages // 2
    middle_window = range(max(0, middle - 2), min(total_pages, middle + 2))
    selected.update(middle_window)
    keyword_budget = max(0, limit - len(selected))
    if keyword_budget:
        step = max(1, total_pages // max(1, keyword_budget + 1))
        for index in range(step, total_pages, step):
            selected.add(index)
            if len(selected) >= limit:
                break
    return sorted(selected)[:limit]


def read_xlsx_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root:
        if not item.tag.endswith("}si"):
            continue
        strings.append(" ".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
    return strings


def extract_xml_text_nodes(xml_data: bytes) -> list[str]:
    root = ET.fromstring(xml_data)
    values: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}t") or node.tag == "t":
            text = node.text or ""
            if text.strip():
                values.append(text)
    return values


def office_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.xml$)", name)
    return (int(match.group(1)) if match else 10**9, name)


def normalize_office_target(target: str) -> str:
    target = target.strip()
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    if target.startswith("xl/"):
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join("xl", target))


def read_xlsx_sheet_map(archive: ZipFile) -> dict[str, str]:
    names = archive.namelist()
    if "xl/workbook.xml" not in names:
        return {}
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                rels[rel_id] = normalize_office_target(target)
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet_map: dict[str, str] = {}
    relationship_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sheet in workbook_root.iter():
        if not (sheet.tag.endswith("}sheet") or sheet.tag == "sheet"):
            continue
        sheet_name = sheet.attrib.get("name")
        rel_id = sheet.attrib.get(relationship_key) or sheet.attrib.get("r:id")
        target = rels.get(rel_id)
        if sheet_name and target:
            sheet_map[target] = sheet_name
    return sheet_map


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def xml_attr(element: ET.Element, local_name: str) -> str | None:
    for key, value in element.attrib.items():
        if key == local_name or key.endswith("}" + local_name):
            return value
    return None


def split_xlsx_cell_reference(reference: str) -> tuple[str, int | None]:
    match = re.match(r"([A-Z]+)(\d+)$", reference or "")
    if not match:
        return "", None
    return match.group(1), parse_int(match.group(2), 0) or None


def xlsx_column_index(column: str) -> int:
    index = 0
    for char in column.upper():
        if not ("A" <= char <= "Z"):
            continue
        index = (index * 26) + (ord(char) - ord("A") + 1)
    return index


def xlsx_column_name(index: int) -> str:
    if index <= 0:
        return ""
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def expand_xlsx_range(range_ref: str, max_cells: int = 500) -> list[str]:
    if ":" not in range_ref:
        return [range_ref]
    start_ref, end_ref = range_ref.split(":", 1)
    start_col, start_row = split_xlsx_cell_reference(start_ref)
    end_col, end_row = split_xlsx_cell_reference(end_ref)
    if not start_col or start_row is None or not end_col or end_row is None:
        return [range_ref]
    start_col_index = xlsx_column_index(start_col)
    end_col_index = xlsx_column_index(end_col)
    if start_col_index <= 0 or end_col_index <= 0:
        return [range_ref]
    cells: list[str] = []
    for row_index in range(min(start_row, end_row), max(start_row, end_row) + 1):
        for col_index in range(min(start_col_index, end_col_index), max(start_col_index, end_col_index) + 1):
            cells.append(f"{xlsx_column_name(col_index)}{row_index}")
            if len(cells) >= max_cells:
                return cells
    return cells


def extract_urls(value: object) -> list[str]:
    text = str(value or "")
    return [item.rstrip(".,;") for item in URL_RE.findall(text)]


def xlsx_sheet_relationships_path(sheet_path: str) -> str:
    directory, filename = sheet_path.rsplit("/", 1)
    return f"{directory}/_rels/{filename}.rels"


def normalize_relationship_target(sheet_path: str, target: str) -> str:
    target = (target or "").strip()
    if target.startswith(("http://", "https://", "mailto:")):
        return target
    if target.startswith("#"):
        return target
    base_dir = posixpath.dirname(sheet_path)
    return posixpath.normpath(posixpath.join(base_dir, target))


def read_xlsx_relationships(archive: ZipFile, rels_path: str, sheet_path: str) -> dict[str, str]:
    if rels_path not in archive.namelist():
        return {}
    rels: dict[str, str] = {}
    root = ET.fromstring(archive.read(rels_path))
    for rel in root:
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = normalize_relationship_target(sheet_path, target)
    return rels


def read_xlsx_sheet_hyperlinks(archive: ZipFile, sheet_path: str, root: ET.Element) -> dict[str, list[dict]]:
    rels = read_xlsx_relationships(archive, xlsx_sheet_relationships_path(sheet_path), sheet_path)
    links_by_cell: dict[str, list[dict]] = {}
    for hyperlink in root.iter():
        if not (hyperlink.tag.endswith("}hyperlink") or hyperlink.tag == "hyperlink"):
            continue
        ref = hyperlink.attrib.get("ref")
        if not ref:
            continue
        rel_id = None
        for key, value in hyperlink.attrib.items():
            if key.endswith("}id") or key in {"r:id", "id"}:
                rel_id = value
                break
        location = hyperlink.attrib.get("location")
        target = rels.get(rel_id) if rel_id else None
        if not target and location:
            target = f"#{location}"
        if not target:
            continue
        link = {
            "url": target,
            "display": hyperlink.attrib.get("display") or "",
            "tooltip": hyperlink.attrib.get("tooltip") or "",
        }
        for cell_ref in expand_xlsx_range(ref):
            links_by_cell.setdefault(cell_ref, []).append(link)
    return links_by_cell


def xlsx_cell_formula(cell: ET.Element) -> str:
    for child in cell:
        if child.tag.endswith("}f") or child.tag == "f":
            return normalize_text(child.text or "")
    return ""


def xlsx_reference_stage_hints(text: str) -> list[str]:
    hints: list[str] = []
    lowered = (text or "").lower()
    if has_any_keyword(lowered, XLSX_REFERENCE_POLICY_KEYWORDS):
        hints.append("Kebijakan")
    if has_any_keyword(lowered, XLSX_REFERENCE_SOCIALIZATION_KEYWORDS):
        hints.append("Sosialisasi")
    if has_any_keyword(lowered, XLSX_REFERENCE_IMPLEMENTATION_KEYWORDS):
        hints.append("Implementasi")
    if has_any_keyword(lowered, XLSX_REFERENCE_EVALUATION_KEYWORDS):
        hints.append("Evaluasi Berkala")
    if has_any_keyword(lowered, XLSX_REFERENCE_IMPROVEMENT_KEYWORDS):
        hints.append("Perbaikan")
    return hints


def build_xlsx_row_text(sheet_name: str, row_number: int, cell_records: list[dict]) -> str:
    fragments: list[str] = []
    for record in cell_records:
        bits: list[str] = []
        value = record.get("value") or ""
        formula = record.get("formula") or ""
        if value:
            bits.append(value)
        if formula:
            bits.append(f"formula={formula}")
        for link in record.get("hyperlinks") or []:
            label = link.get("display") or link.get("tooltip") or value or "hyperlink"
            bits.append(f"{label} {link.get('url') or ''}")
        for url in record.get("urls") or []:
            bits.append(url)
        if bits:
            fragments.append(f"{record.get('col') or record.get('cell')}: {' '.join(bits)}")
    if not fragments:
        return ""
    return normalize_text(f"Sheet {sheet_name} baris {row_number}: " + " | ".join(fragments))


def is_xlsx_evidence_context(row_text: str, cell_records: list[dict], evidence_columns: set[str]) -> bool:
    row_has_link = any(record.get("urls") or record.get("hyperlinks") for record in cell_records)
    row_has_evidence_col_value = any(
        record.get("col") in evidence_columns
        and (record.get("value") or record.get("urls") or record.get("hyperlinks") or record.get("formula"))
        for record in cell_records
    )
    row_evidence_keyword = bool(keyword_hits(row_text, XLSX_EVIDENCE_ROW_KEYWORDS))
    return bool(row_has_link or row_has_evidence_col_value or row_evidence_keyword)


def extract_xlsx_sheet_structured(
    archive: ZipFile,
    sheet_path: str,
    sheet_name: str,
    shared_strings: list[str],
    remaining_limit: int,
) -> dict:
    result = {
        "rows_text": [],
        "evidence_rows": [],
        "evidence_links": [],
        "evidence_columns": [],
        "total_rows": 0,
        "value_rows": 0,
    }
    if remaining_limit <= 0:
        return result
    root = ET.fromstring(archive.read(sheet_path))
    hyperlinks_by_cell = read_xlsx_sheet_hyperlinks(archive, sheet_path, root)
    evidence_columns: set[str] = set()
    total_chars = 0
    seen_link_keys: set[tuple[str, str]] = set()
    for row in root.iter():
        if not (row.tag.endswith("}row") or row.tag == "row"):
            continue
        result["total_rows"] += 1
        row_number = parse_int(row.attrib.get("r"), result["total_rows"])
        cell_records: list[dict] = []
        for cell in row:
            if not (cell.tag.endswith("}c") or cell.tag == "c"):
                continue
            cell_ref = xml_attr(cell, "r") or ""
            column, ref_row = split_xlsx_cell_reference(cell_ref)
            value = xlsx_cell_text(cell, shared_strings)
            formula = xlsx_cell_formula(cell)
            hyperlinks = hyperlinks_by_cell.get(cell_ref, [])
            urls = extract_urls(" ".join(item for item in (value, formula) if item))
            record = {
                "cell": cell_ref,
                "col": column,
                "row": ref_row or row_number,
                "value": value,
                "formula": formula,
                "urls": urls,
                "hyperlinks": hyperlinks,
            }
            cell_records.append(record)
            if value and keyword_hits(value, XLSX_EVIDENCE_COLUMN_KEYWORDS):
                evidence_columns.add(column)
        if not cell_records:
            continue
        result["value_rows"] += 1
        row_text = build_xlsx_row_text(sheet_name, row_number, cell_records)
        if not row_text:
            continue
        row_is_evidence = is_xlsx_evidence_context(row_text, cell_records, evidence_columns)
        row_hints = xlsx_reference_stage_hints(row_text)
        if row_is_evidence and len(result["evidence_rows"]) < 300:
            result["evidence_rows"].append(
                {
                    "sheet": sheet_name,
                    "row": row_number,
                    "text": clean_ai_text(row_text, 1200),
                    "stage_hints": row_hints,
                }
            )
        for record in cell_records:
            for link in record.get("hyperlinks") or []:
                url = link.get("url") or ""
                if not url:
                    continue
                link_key = (record.get("cell") or "", url)
                if link_key in seen_link_keys:
                    continue
                seen_link_keys.add(link_key)
                label = link.get("display") or link.get("tooltip") or record.get("value") or "hyperlink"
                context = clean_ai_text(row_text, 900)
                if len(result["evidence_links"]) < 400:
                    result["evidence_links"].append(
                        {
                            "sheet": sheet_name,
                            "cell": record.get("cell"),
                            "label": clean_ai_text(label, 180),
                            "url": url,
                            "context": context,
                            "stage_hints": xlsx_reference_stage_hints(" ".join([label, url, row_text])),
                        }
                    )
            for url in record.get("urls") or []:
                link_key = (record.get("cell") or "", url)
                if link_key in seen_link_keys:
                    continue
                seen_link_keys.add(link_key)
                context = clean_ai_text(row_text, 900)
                if len(result["evidence_links"]) < 400:
                    result["evidence_links"].append(
                        {
                            "sheet": sheet_name,
                            "cell": record.get("cell"),
                            "label": clean_ai_text(record.get("value"), 180),
                            "url": url,
                            "context": context,
                            "stage_hints": xlsx_reference_stage_hints(" ".join([record.get("value") or "", url, row_text])),
                        }
                    )
        include_row = (
            row_is_evidence
            or result["value_rows"] <= 80
            or bool(keyword_hits(row_text, RISK_DOCUMENT_KEYWORDS + XLSX_EVIDENCE_ROW_KEYWORDS))
        )
        if include_row and total_chars < remaining_limit:
            snippet = clean_ai_text(row_text, 2000)
            result["rows_text"].append(snippet)
            total_chars += len(snippet)
        if total_chars >= remaining_limit and len(result["evidence_rows"]) >= 300:
            break
    result["evidence_columns"] = sorted(column for column in evidence_columns if column)
    return result


def build_xlsx_evidence_block(evidence_rows: list[dict], evidence_links: list[dict]) -> str:
    parts: list[str] = []
    if evidence_rows:
        parts.append("REFERENSI EVIDENCE TERSTRUKTUR DARI WORKBOOK:")
        for row in evidence_rows[:100]:
            hints = ", ".join(row.get("stage_hints") or [])
            parts.append(
                normalize_text(
                    f"[{row.get('sheet')} baris {row.get('row')}] {row.get('text') or ''}"
                    + (f" Tahap terbaca: {hints}" if hints else "")
                )
            )
    if evidence_links:
        parts.append("HYPERLINK EVIDENCE TERBACA:")
        for link in evidence_links[:120]:
            hints = ", ".join(link.get("stage_hints") or [])
            parts.append(
                normalize_text(
                    f"[{link.get('sheet')} {link.get('cell')}] {link.get('label') or 'Evidence'} -> {link.get('url') or ''}. "
                    f"Konteks: {link.get('context') or ''}"
                    + (f" Tahap terbaca: {hints}" if hints else "")
                )
            )
    return normalize_text(" ".join(parts))


def xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text_nodes = [
            node.text or ""
            for node in cell.iter()
            if node.tag.endswith("}t") or node.tag == "t"
        ]
        return normalize_text(" ".join(text_nodes))

    raw = None
    for child in cell:
        if child.tag.endswith("}v") or child.tag == "v":
            raw = child.text
            break
    if raw is None:
        text_nodes = [
            node.text or ""
            for node in cell.iter()
            if node.tag.endswith("}t") or node.tag == "t"
        ]
        return normalize_text(" ".join(text_nodes))
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        if index < len(shared_strings):
            return normalize_text(shared_strings[index])
    return normalize_text(raw)
