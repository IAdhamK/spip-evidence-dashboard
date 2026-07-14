from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from pypdf import PdfReader


class OfficeRenderError(RuntimeError):
    pass


def office_renderer_binary() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def office_renderer_status() -> dict[str, object]:
    office = office_renderer_binary()
    pdf = shutil.which("pdftoppm")
    return {
        "available": bool(office and pdf),
        "office_binary": Path(office).name if office else None,
        "pdf_renderer": Path(pdf).name if pdf else None,
        "method": "libreoffice_ooxml_to_pdf_to_png_v2",
        "missing": [
            name
            for name, value in (("libreoffice", office), ("pdftoppm", pdf))
            if not value
        ],
    }


office_slide_renderer_status = office_renderer_status


def render_pptx_slide(
    payload: bytes,
    slide_number: int,
    *,
    dpi: int = 144,
    timeout_seconds: int = 90,
    max_input_bytes: int = 64 * 1024 * 1024,
    max_output_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    if slide_number < 1 or slide_number > 100_000:
        raise OfficeRenderError("Nomor slide PPTX tidak valid.")
    pdf = convert_office_to_pdf(
        payload,
        "pptx",
        timeout_seconds=timeout_seconds,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    return render_pdf_page(
        pdf,
        slide_number,
        dpi=dpi,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def render_office_page(
    payload: bytes,
    file_kind: str,
    page_number: int,
    *,
    dpi: int = 144,
    timeout_seconds: int = 90,
    max_input_bytes: int = 64 * 1024 * 1024,
    max_output_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    pdf = convert_office_to_pdf(
        payload,
        file_kind,
        timeout_seconds=timeout_seconds,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    return render_pdf_page(
        pdf,
        page_number,
        dpi=dpi,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def convert_office_to_pdf(
    payload: bytes,
    file_kind: str,
    *,
    timeout_seconds: int = 90,
    max_input_bytes: int = 64 * 1024 * 1024,
    max_output_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    normalized_kind = str(file_kind or "").strip().lower()
    export_filter = {
        "docx": "writer_pdf_Export",
        "xlsx": (
            'calc_pdf_Export:{"SinglePageSheets":'
            '{"type":"boolean","value":"true"}}'
        ),
        "pptx": "impress_pdf_Export",
    }.get(normalized_kind)
    if not export_filter:
        raise OfficeRenderError("Format Office tidak didukung oleh renderer.")
    if not payload or len(payload) > max(1, int(max_input_bytes)):
        raise OfficeRenderError("Ukuran OOXML tidak valid atau melampaui batas renderer.")
    office = office_renderer_binary()
    if not office:
        raise OfficeRenderError("LibreOffice headless wajib tersedia.")

    effective_timeout = max(5, min(300, int(timeout_seconds)))
    try:
        with TemporaryDirectory(prefix="spip-office-convert-") as directory:
            root = Path(directory)
            source = root / f"source.{normalized_kind}"
            profile = root / "libreoffice-profile"
            cache = root / "cache"
            source.write_bytes(
                _sanitized_ooxml_for_render(payload, normalized_kind, max_input_bytes)
            )
            subprocess_environment = {
                "HOME": str(root),
                "XDG_CACHE_HOME": str(cache),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.environ.get("PATH", ""),
            }
            conversion = subprocess.run(
                [
                    office,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    f"pdf:{export_filter}",
                    "--outdir",
                    str(root),
                    str(source),
                ],
                check=False,
                capture_output=True,
                env=subprocess_environment,
                timeout=effective_timeout,
            )
            rendered_pdf = root / "source.pdf"
            if conversion.returncode != 0 or not rendered_pdf.is_file():
                raise OfficeRenderError("LibreOffice gagal mengubah OOXML menjadi PDF.")
            if rendered_pdf.stat().st_size > max_output_bytes:
                raise OfficeRenderError("PDF hasil rendering Office melampaui batas.")
            return rendered_pdf.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise OfficeRenderError("Konversi Office melewati batas waktu.") from exc
    except OSError as exc:
        raise OfficeRenderError("Converter Office gagal dijalankan.") from exc


def office_pdf_page_count(pdf_payload: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(pdf_payload)).pages)
    except Exception as exc:
        raise OfficeRenderError("PDF hasil konversi Office tidak valid.") from exc


def render_pdf_page(
    pdf_payload: bytes,
    page_number: int,
    *,
    dpi: int = 144,
    timeout_seconds: int = 90,
    max_output_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    if page_number < 1 or page_number > 100_000:
        raise OfficeRenderError("Nomor halaman hasil render tidak valid.")
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise OfficeRenderError("pdftoppm wajib tersedia untuk raster halaman Office.")
    effective_timeout = max(5, min(300, int(timeout_seconds)))
    effective_dpi = max(72, min(300, int(dpi)))
    try:
        with TemporaryDirectory(prefix="spip-office-raster-") as directory:
            root = Path(directory)
            rendered_pdf = root / "source.pdf"
            rendered_pdf.write_bytes(pdf_payload)
            prefix = root / f"page-{page_number}"
            subprocess_environment = {
                "HOME": str(root),
                "XDG_CACHE_HOME": str(root / "cache"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.environ.get("PATH", ""),
            }
            raster = subprocess.run(
                [
                    pdftoppm,
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-png",
                    "-r",
                    str(effective_dpi),
                    str(rendered_pdf),
                    str(prefix),
                ],
                check=False,
                capture_output=True,
                env=subprocess_environment,
                timeout=effective_timeout,
            )
            image = prefix.with_suffix(".png")
            if raster.returncode != 0 or not image.is_file():
                raise OfficeRenderError("Halaman Office tidak ditemukan atau gagal dirasterisasi.")
            if image.stat().st_size > max_output_bytes:
                raise OfficeRenderError("Preview halaman Office melampaui batas.")
            return image.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise OfficeRenderError("Raster halaman Office melewati batas waktu.") from exc
    except OSError as exc:
        raise OfficeRenderError("Raster halaman Office gagal dijalankan.") from exc


def _sanitized_pptx_for_render(payload: bytes, max_input_bytes: int) -> bytes:
    return _sanitized_ooxml_for_render(payload, "pptx", max_input_bytes)


def _sanitized_ooxml_for_render(
    payload: bytes,
    file_kind: str,
    max_input_bytes: int,
) -> bytes:
    max_entries = 5_000
    max_total_uncompressed = min(256 * 1024 * 1024, max(1, max_input_bytes) * 4)
    seen: set[str] = set()
    total_uncompressed = 0
    output = BytesIO()
    try:
        with ZipFile(BytesIO(payload)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
            infos = source.infolist()
            if not infos or len(infos) > max_entries:
                raise OfficeRenderError("Struktur OOXML kosong atau melampaui batas entry renderer.")
            for info in infos:
                path = PurePosixPath(info.filename)
                normalized = path.as_posix().lower()
                if path.is_absolute() or ".." in path.parts or normalized in seen:
                    raise OfficeRenderError("OOXML mengandung lokasi ZIP yang tidak aman.")
                seen.add(normalized)
                if info.flag_bits & 0x1:
                    raise OfficeRenderError("OOXML terenkripsi tidak dapat dirender.")
                total_uncompressed += max(0, int(info.file_size))
                if total_uncompressed > max_total_uncompressed:
                    raise OfficeRenderError("OOXML melampaui batas dekompresi renderer.")
                if info.file_size / max(1, info.compress_size) > 100.0:
                    raise OfficeRenderError("OOXML memiliki rasio kompresi berbahaya.")
                if _unsafe_embedded_part(normalized, file_kind):
                    continue
                data = source.read(info)
                if normalized.endswith(".rels"):
                    data = _strip_external_relationships(data)
                target.writestr(info.filename, data)
    except BadZipFile as exc:
        raise OfficeRenderError("Payload bukan OOXML yang valid.") from exc
    sanitized = output.getvalue()
    if not sanitized:
        raise OfficeRenderError("OOXML tidak menghasilkan salinan render yang aman.")
    return sanitized


def _unsafe_embedded_part(normalized_path: str, file_kind: str = "pptx") -> bool:
    root = {"pptx": "ppt", "docx": "word", "xlsx": "xl"}.get(file_kind, "")
    return bool(
        root
        and (
            normalized_path.startswith(f"{root}/embeddings/")
            or normalized_path.startswith(f"{root}/activex/")
            or normalized_path.endswith("vbaproject.bin")
        )
    )


def _strip_external_relationships(payload: bytes) -> bytes:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise OfficeRenderError("Relationship OOXML PPTX tidak valid.") from exc
    removed = False
    for relationship in list(root):
        if relationship.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        target = str(relationship.attrib.get("Target") or "").strip()
        target_mode = str(relationship.attrib.get("TargetMode") or "").strip().lower()
        parsed = urlsplit(target)
        if (
            target_mode == "external"
            or bool(parsed.scheme)
            or target.startswith(("//", "\\\\"))
        ):
            root.remove(relationship)
            removed = True
    if not removed:
        return payload
    namespace = root.tag.partition("}")[0].lstrip("{")
    if namespace:
        ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
