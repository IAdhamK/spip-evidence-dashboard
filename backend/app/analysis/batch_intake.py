from __future__ import annotations

from collections import Counter
import hashlib
from io import BytesIO
import mimetypes
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata
from zipfile import BadZipFile, ZipFile, ZipInfo

from app.analysis.security import inspect_upload_security
from app.config import Settings


SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".tif": "image",
    ".tiff": "image",
    ".bmp": "image",
    ".webp": "image",
    ".txt": "text",
    ".md": "text",
    ".csv": "text",
    ".json": "text",
    ".xml": "text",
    ".html": "text",
    ".htm": "text",
}
SELECTION_WEIGHTS = {
    "xlsx": 0.40,
    "pdf": 0.36,
    "docx": 0.12,
    "image": 0.12,
}


class UnsafeBatchArchive(ValueError):
    def __init__(self, errors: list[str], audit: dict):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.audit = audit


def inspect_batch_archive(
    archive_payload: bytes,
    archive_name: str,
    settings: Settings,
) -> tuple[dict, list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    members: list[dict] = []
    normalized_seen: dict[str, str] = {}
    casefold_seen: dict[str, str] = {}
    extension_counts: Counter[str] = Counter()
    total_uncompressed = 0
    total_compressed = 0

    if not str(archive_name or "").lower().endswith(".zip"):
        errors.append("File batch harus memakai ekstensi .zip.")
    if len(archive_payload) > settings.analysis_batch_max_archive_bytes:
        errors.append(
            "Ukuran ZIP melebihi batas "
            f"{settings.analysis_batch_max_archive_bytes} byte."
        )
    try:
        with ZipFile(BytesIO(archive_payload)) as archive:
            entries = archive.infolist()
            if len(entries) > settings.analysis_batch_max_entries:
                errors.append(
                    f"Jumlah entry {len(entries)} melebihi batas "
                    f"{settings.analysis_batch_max_entries}."
                )
            for info in entries:
                try:
                    normalized = _normalized_member_name(info)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                canonical = unicodedata.normalize("NFC", normalized)
                folded = canonical.casefold()
                if canonical in normalized_seen:
                    errors.append(f"Collision path ZIP: {normalized!r}.")
                if folded in casefold_seen and casefold_seen[folded] != canonical:
                    errors.append(
                        "Collision path case-insensitive: "
                        f"{casefold_seen[folded]!r} dan {canonical!r}."
                    )
                normalized_seen[canonical] = info.filename
                casefold_seen[folded] = canonical
                if info.flag_bits & 0x1:
                    errors.append(f"Entry terenkripsi tidak diizinkan: {normalized!r}.")
                if _is_symlink(info):
                    errors.append(f"Symlink tidak diizinkan: {normalized!r}.")

                total_uncompressed += max(0, int(info.file_size))
                total_compressed += max(0, int(info.compress_size))
                ratio = info.file_size / max(1, info.compress_size)
                if info.file_size > settings.analysis_batch_max_entry_bytes:
                    errors.append(
                        f"Entry {normalized!r} melebihi batas per-file "
                        f"{settings.analysis_batch_max_entry_bytes} byte."
                    )
                if info.file_size and ratio > settings.analysis_batch_max_compression_ratio:
                    errors.append(
                        f"Rasio kompresi entry {normalized!r} tidak aman "
                        f"({ratio:.2f})."
                    )
                if info.is_dir():
                    continue
                suffix = Path(normalized).suffix.lower()
                extension_counts[suffix or "[none]"] += 1
                ignored = _ignored_metadata_path(normalized)
                kind = SUPPORTED_EXTENSIONS.get(suffix, "unknown")
                members.append(
                    {
                        "archive_path": normalized,
                        "file_name": PurePosixPath(normalized).name,
                        "file_kind": kind,
                        "size_bytes": int(info.file_size),
                        "compressed_size_bytes": int(info.compress_size),
                        "supported": kind != "unknown" and not ignored,
                        "ignored_metadata": ignored,
                    }
                )
    except (BadZipFile, OSError) as exc:
        errors.append(f"ZIP tidak valid: {exc}.")

    if total_uncompressed > settings.analysis_batch_max_uncompressed_bytes:
        errors.append(
            "Ukuran total hasil dekompresi melebihi batas "
            f"{settings.analysis_batch_max_uncompressed_bytes} byte."
        )
    overall_ratio = total_uncompressed / max(1, len(archive_payload))
    if overall_ratio > settings.analysis_batch_max_compression_ratio:
        errors.append(f"Rasio kompresi total ZIP tidak aman ({overall_ratio:.2f}).")
    if not members:
        errors.append("ZIP tidak memiliki file yang dapat diperiksa.")
    if members and not any(item["supported"] for item in members):
        warnings.append("ZIP tidak memiliki format dokumen yang didukung pipeline V2.")

    audit = {
        "archive_file_name": str(archive_name or "batch.zip"),
        "archive_sha256": hashlib.sha256(archive_payload).hexdigest(),
        "archive_size_bytes": len(archive_payload),
        "entry_count": len(normalized_seen),
        "file_count": len(members),
        "supported_file_count": sum(bool(item["supported"]) for item in members),
        "unsupported_file_count": sum(not item["supported"] for item in members),
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_entry_bytes": total_compressed,
        "overall_compression_ratio": round(overall_ratio, 3),
        "extension_counts": dict(sorted(extension_counts.items())),
        "safe_to_process": not errors,
        "errors": errors,
        "warnings": warnings,
        "limits": {
            "max_archive_bytes": settings.analysis_batch_max_archive_bytes,
            "max_entries": settings.analysis_batch_max_entries,
            "max_files": settings.analysis_batch_max_files,
            "max_entry_bytes": settings.analysis_batch_max_entry_bytes,
            "max_uncompressed_bytes": settings.analysis_batch_max_uncompressed_bytes,
            "max_compression_ratio": settings.analysis_batch_max_compression_ratio,
        },
    }
    if errors:
        raise UnsafeBatchArchive(errors, audit)
    return audit, members


def diverse_member_order(members: list[dict], limit: int) -> list[dict]:
    supported = [item for item in members if item.get("supported")]
    stable = sorted(supported, key=lambda item: (-int(item["size_bytes"]), item["archive_path"]))
    selected: list[dict] = []
    selected_paths: set[str] = set()

    quotas = {kind: int(round(limit * weight)) for kind, weight in SELECTION_WEIGHTS.items()}
    while sum(quotas.values()) > limit:
        key = max(quotas, key=quotas.get)
        quotas[key] -= 1
    while sum(quotas.values()) < limit:
        key = max(SELECTION_WEIGHTS, key=lambda kind: SELECTION_WEIGHTS[kind] - quotas[kind] / max(1, limit))
        quotas[key] += 1

    for kind, quota in quotas.items():
        for item in (candidate for candidate in stable if candidate["file_kind"] == kind):
            if quota <= 0:
                break
            selected.append(item)
            selected_paths.add(item["archive_path"])
            quota -= 1
    selected.extend(item for item in stable if item["archive_path"] not in selected_paths)
    return selected


def read_and_validate_member(
    archive_payload: bytes,
    member: dict,
    settings: Settings,
) -> tuple[bytes, str, str | None]:
    try:
        with ZipFile(BytesIO(archive_payload)) as archive:
            payload = archive.read(member["archive_path"])
    except (BadZipFile, KeyError, OSError, RuntimeError) as exc:
        return b"", "application/octet-stream", f"Entry ZIP gagal dibaca: {exc}."
    if len(payload) != int(member["size_bytes"]):
        return b"", "", "Ukuran hasil dekompresi tidak cocok dengan metadata ZIP."
    content_type = mimetypes.guess_type(member["file_name"])[0] or "application/octet-stream"
    kind, findings = inspect_upload_security(
        member["file_name"],
        content_type,
        payload,
        max_bytes=settings.analysis_batch_max_entry_bytes,
        max_archive_entries=settings.analysis_batch_max_entries,
        max_uncompressed_bytes=settings.analysis_batch_max_uncompressed_bytes,
        max_compression_ratio=settings.analysis_batch_max_compression_ratio,
    )
    blocking = [finding.message for finding in findings if finding.blocking]
    if blocking:
        return b"", content_type, "; ".join(blocking)
    if kind != member["file_kind"]:
        return b"", content_type, (
            f"Jenis isi file {kind!r} tidak cocok dengan ekstensi "
            f"{member['file_kind']!r}."
        )
    return payload, content_type, None


def _normalized_member_name(info: ZipInfo) -> str:
    raw = info.filename
    if not raw or "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise ValueError("ZIP memiliki nama entry kosong atau karakter kontrol.")
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Path absolut tidak diizinkan: {raw!r}.")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Path tidak aman: {raw!r}.")
    if len(normalized) > 4096:
        raise ValueError(f"Path terlalu panjang: {raw[:120]!r}.")
    return path.as_posix()


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _ignored_metadata_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "__MACOSX" in parts or PurePosixPath(path).name in {".DS_Store", "Thumbs.db"}
