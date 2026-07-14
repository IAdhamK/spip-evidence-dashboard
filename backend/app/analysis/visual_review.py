from __future__ import annotations

import hashlib
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from app.analysis.office_render import OfficeRenderError, render_office_page, render_pptx_slide


RASTER_MEDIA_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


class VisualPreviewError(ValueError):
    pass


def extract_visual_preview(
    run: dict,
    unit: dict,
    document_payload: bytes,
    *,
    max_bytes: int = 64 * 1024 * 1024,
    max_compression_ratio: float = 100.0,
    timeout_seconds: int = 90,
) -> tuple[bytes, str, str]:
    content_type = str(run.get("content_type") or "").lower()
    file_name = str(run.get("file_name") or "visual-source")
    suffix = PurePosixPath(file_name).suffix.lower()
    if content_type in RASTER_MEDIA_TYPES.values() and suffix in RASTER_MEDIA_TYPES:
        preview = document_payload
        media_type = RASTER_MEDIA_TYPES[suffix]
        preview_name = PurePosixPath(file_name).name
    elif (
        (content_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation" or suffix == ".pptx")
        and str(unit.get("unit_type") or "") == "slide_visual"
    ):
        slide = int((unit.get("source_location") or {}).get("slide") or 0)
        dpi = max(72, min(300, int((unit.get("metadata") or {}).get("ocr_render_dpi") or 144)))
        try:
            preview = render_pptx_slide(
                document_payload,
                slide,
                dpi=dpi,
                timeout_seconds=timeout_seconds,
                max_input_bytes=max_bytes,
                max_output_bytes=max_bytes,
            )
        except OfficeRenderError as exc:
            raise VisualPreviewError("Slide PPTX gagal dirender untuk review.") from exc
        media_type = "image/png"
        preview_name = f"slide-{slide}.png"
    elif (
        suffix in {".docx", ".xlsx"}
        and str(unit.get("unit_type") or "") == "office_visual_page"
    ):
        page = int((unit.get("source_location") or {}).get("rendered_page") or 0)
        dpi = max(72, min(300, int((unit.get("metadata") or {}).get("ocr_render_dpi") or 144)))
        try:
            preview = render_office_page(
                document_payload,
                suffix.lstrip("."),
                page,
                dpi=dpi,
                timeout_seconds=timeout_seconds,
                max_input_bytes=max_bytes,
                max_output_bytes=max_bytes,
            )
        except OfficeRenderError as exc:
            raise VisualPreviewError("Halaman visual Office gagal dirender untuk review.") from exc
        media_type = "image/png"
        preview_name = f"{suffix.lstrip('.')}-page-{page}.png"
    elif content_type == "application/pdf" or suffix == ".pdf":
        page = int((unit.get("source_location") or {}).get("page") or 0)
        if page < 1:
            raise VisualPreviewError("Nomor halaman PDF untuk preview tidak valid.")
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise VisualPreviewError("Renderer PDF tidak tersedia untuk preview review.")
        dpi = max(72, min(300, int((unit.get("metadata") or {}).get("ocr_render_dpi") or 144)))
        try:
            with TemporaryDirectory(prefix="spip-review-preview-") as directory:
                root = Path(directory)
                source = root / "source.pdf"
                prefix = root / f"page-{page}"
                source.write_bytes(document_payload)
                subprocess.run(
                    [
                        renderer,
                        "-f", str(page),
                        "-l", str(page),
                        "-singlefile",
                        "-png",
                        "-r", str(dpi),
                        str(source),
                        str(prefix),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=max(5, min(300, int(timeout_seconds))),
                )
                image_path = prefix.with_suffix(".png")
                if not image_path.is_file():
                    raise VisualPreviewError("Renderer PDF tidak menghasilkan preview halaman.")
                preview = image_path.read_bytes()
                media_type = "image/png"
                preview_name = f"page-{page}.png"
        except (subprocess.SubprocessError, OSError) as exc:
            raise VisualPreviewError("Halaman PDF gagal dirender untuk review.") from exc
    else:
        part = str((unit.get("source_location") or {}).get("part") or "")
        path = PurePosixPath(part)
        if not part or path.is_absolute() or ".." in path.parts:
            raise VisualPreviewError("Lokasi embedded image tidak aman atau tidak tersedia.")
        media_type = RASTER_MEDIA_TYPES.get(path.suffix.lower()) or ""
        if not media_type:
            raise VisualPreviewError("Format visual ini belum aman untuk preview inline.")
        try:
            with ZipFile(BytesIO(document_payload)) as archive:
                info = archive.getinfo(part)
                if info.flag_bits & 0x1:
                    raise VisualPreviewError("Embedded image terenkripsi tidak dapat dipreview.")
                if info.file_size < 0 or info.file_size > max_bytes:
                    raise VisualPreviewError("Embedded image melampaui batas preview.")
                ratio = info.file_size / max(1, info.compress_size)
                if ratio > max_compression_ratio:
                    raise VisualPreviewError("Rasio kompresi embedded image tidak aman.")
                preview = archive.read(info)
                preview_name = path.name
        except (BadZipFile, KeyError) as exc:
            raise VisualPreviewError("Embedded image tidak ditemukan pada dokumen sumber.") from exc

    if len(preview) > max_bytes:
        raise VisualPreviewError("Aset visual melampaui batas preview.")
    expected_sha256 = str(
        (unit.get("metadata") or {}).get("ocr_source_image_sha256") or ""
    )
    observed_sha256 = hashlib.sha256(preview).hexdigest()
    if not expected_sha256 or observed_sha256 != expected_sha256:
        raise VisualPreviewError(
            "Checksum aset visual tidak cocok; keputusan review ditahan sebagai stale."
        )
    return preview, media_type, preview_name
