from __future__ import annotations

import ipaddress
from io import BytesIO
import socket
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from app.analysis.contracts import SecurityFinding


OFFICE_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
GIF_MAGICS = (b"GIF87a", b"GIF89a")
TIFF_MAGICS = (b"II*\x00", b"MM\x00*")
BMP_MAGIC = b"BM"
SUPPORTED_KINDS = {"pdf", "docx", "xlsx", "pptx", "image", "text"}
OFFICE_REQUIRED_MEMBERS = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}


def sniff_file_kind(file_name: str, content_type: str | None, payload: bytes) -> str:
    lowered = str(file_name or "").lower()
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if payload.startswith(PDF_MAGIC):
        return "pdf"
    if payload.startswith(OFFICE_MAGIC):
        if lowered.endswith(".docx"):
            return "docx"
        if lowered.endswith(".xlsx"):
            return "xlsx"
        if lowered.endswith(".pptx"):
            return "pptx"
        return "office_zip"
    if payload.startswith(OLE_MAGIC):
        return "legacy_office"
    if (
        payload.startswith(PNG_MAGIC)
        or payload.startswith(JPEG_MAGIC)
        or payload.startswith(GIF_MAGICS)
        or payload.startswith(TIFF_MAGICS)
        or payload.startswith(BMP_MAGIC)
        or (payload.startswith(b"RIFF") and payload[8:12] == b"WEBP")
    ):
        return "image"
    if declared.startswith("text/"):
        return "text"
    if lowered.endswith((".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm")):
        return "text"
    return "unknown"


def validate_file_signature(file_name: str, content_type: str | None, payload: bytes) -> list[str]:
    warnings: list[str] = []
    kind = sniff_file_kind(file_name, content_type, payload)
    lowered = str(file_name or "").lower()
    expected = None
    for suffix, value in ((".pdf", "pdf"), (".docx", "docx"), (".xlsx", "xlsx"), (".pptx", "pptx")):
        if lowered.endswith(suffix):
            expected = value
            break
    if expected and kind != expected:
        warnings.append(f"Signature file tidak cocok: ekstensi {expected}, isi terdeteksi {kind}.")
    if not payload:
        warnings.append("File kosong.")
    return warnings


def inspect_upload_security(
    file_name: str,
    content_type: str | None,
    payload: bytes,
    *,
    max_bytes: int = 0,
    max_archive_entries: int = 5000,
    max_uncompressed_bytes: int = 256 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> tuple[str, list[SecurityFinding]]:
    kind = sniff_file_kind(file_name, content_type, payload)
    findings: list[SecurityFinding] = []
    if not payload:
        findings.append(SecurityFinding("critical", "empty_file", "File kosong.", True))
        return kind, findings
    if max_bytes > 0 and len(payload) > max_bytes:
        findings.append(
            SecurityFinding(
                "critical",
                "file_too_large",
                "Ukuran file melebihi batas analisis.",
                True,
                {"size_bytes": len(payload), "max_bytes": max_bytes},
            )
        )

    for warning in validate_file_signature(file_name, content_type, payload):
        findings.append(SecurityFinding("critical", "signature_mismatch", warning, True))

    if kind not in SUPPORTED_KINDS:
        findings.append(
            SecurityFinding(
                "critical",
                "unsupported_file_kind",
                f"Jenis isi file '{kind}' belum didukung pipeline V2.",
                True,
            )
        )
    if kind in OFFICE_REQUIRED_MEMBERS:
        findings.extend(
            inspect_office_archive(
                payload,
                kind,
                max_entries=max_archive_entries,
                max_uncompressed_bytes=max_uncompressed_bytes,
                max_compression_ratio=max_compression_ratio,
            )
        )
    if kind == "text" and b"\x00" in payload[:8192]:
        findings.append(
            SecurityFinding(
                "high",
                "binary_text_payload",
                "File yang dinyatakan sebagai teks mengandung byte biner/NUL.",
                True,
            )
        )
    return kind, findings


def inspect_office_archive(
    payload: bytes,
    kind: str,
    *,
    max_entries: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            required_member = OFFICE_REQUIRED_MEMBERS[kind]
            if required_member not in names:
                findings.append(
                    SecurityFinding(
                        "critical",
                        "office_structure_mismatch",
                        f"Struktur {kind.upper()} tidak valid: {required_member} tidak ditemukan.",
                        True,
                    )
                )
            if len(entries) > max_entries:
                findings.append(
                    SecurityFinding(
                        "critical",
                        "archive_entry_limit",
                        "Jumlah entry archive Office melebihi batas.",
                        True,
                        {"entries": len(entries), "max_entries": max_entries},
                    )
                )

            total_uncompressed = 0
            encrypted_entries = 0
            unsafe_paths: list[str] = []
            for entry in entries:
                total_uncompressed += max(0, int(entry.file_size))
                if entry.flag_bits & 0x1:
                    encrypted_entries += 1
                normalized = entry.filename.replace("\\", "/")
                segments = [segment for segment in normalized.split("/") if segment]
                if normalized.startswith("/") or ".." in segments:
                    unsafe_paths.append(entry.filename)

            if total_uncompressed > max_uncompressed_bytes:
                findings.append(
                    SecurityFinding(
                        "critical",
                        "archive_uncompressed_limit",
                        "Ukuran hasil dekompresi archive Office melebihi batas.",
                        True,
                        {
                            "uncompressed_bytes": total_uncompressed,
                            "max_uncompressed_bytes": max_uncompressed_bytes,
                        },
                    )
                )
            ratio = total_uncompressed / max(1, len(payload))
            if ratio > max_compression_ratio:
                findings.append(
                    SecurityFinding(
                        "critical",
                        "archive_compression_ratio",
                        "Rasio kompresi archive Office tidak aman.",
                        True,
                        {"ratio": round(ratio, 2), "max_ratio": max_compression_ratio},
                    )
                )
            if encrypted_entries:
                findings.append(
                    SecurityFinding(
                        "high",
                        "encrypted_archive_entries",
                        "Archive Office mengandung entry terenkripsi yang tidak dapat diaudit.",
                        True,
                        {"encrypted_entries": encrypted_entries},
                    )
                )
            if unsafe_paths:
                findings.append(
                    SecurityFinding(
                        "critical",
                        "archive_path_traversal",
                        "Archive Office mengandung path yang tidak aman.",
                        True,
                        {"paths": unsafe_paths[:10]},
                    )
                )
    except BadZipFile:
        findings.append(
            SecurityFinding(
                "critical",
                "invalid_office_archive",
                "File Office bukan archive ZIP yang valid.",
                True,
            )
        )
    return findings


def validate_external_url(url: str, allowed_hosts: set[str]) -> tuple[bool, str]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https":
        return False, "Hanya URL HTTPS yang diizinkan."
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, "Host URL tidak valid."
    if not host_is_allowed(host, allowed_hosts):
        return False, "Host link evidence tidak termasuk allowlist."
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror:
        return False, "DNS host link evidence tidak dapat diresolusikan."
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False, "Alamat IP hasil resolusi tidak valid."
        if not ip.is_global:
            return False, "Link evidence mengarah ke alamat jaringan privat/non-publik."
    return True, ""


def host_is_allowed(host: str, allowed_hosts: set[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_hosts)
