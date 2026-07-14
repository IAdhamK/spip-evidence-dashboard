from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from app.analysis.security import (
    host_is_allowed,
    inspect_upload_security,
    inspect_office_archive,
    sniff_file_kind,
    validate_external_url,
)
from app.evidence_link_crawler import validate_link_content_type


def office_payload(member: str, content: bytes = b"<xml/>") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(member, content)
    return buffer.getvalue()


class AnalysisSecurityTests(unittest.TestCase):
    def test_sniffs_supported_signatures(self) -> None:
        self.assertEqual(sniff_file_kind("a.pdf", "application/pdf", b"%PDF-1.4"), "pdf")
        self.assertEqual(sniff_file_kind("a.png", "image/png", b"\x89PNG\r\n\x1a\nrest"), "image")
        self.assertEqual(sniff_file_kind("a.txt", "text/plain", b"hello"), "text")

    def test_blocks_extension_signature_mismatch(self) -> None:
        kind, findings = inspect_upload_security(
            "laporan.pdf",
            "application/pdf",
            b"not-a-pdf",
        )
        self.assertEqual(kind, "unknown")
        self.assertTrue(any(item.code == "signature_mismatch" and item.blocking for item in findings))

    def test_accepts_minimal_docx_structure(self) -> None:
        kind, findings = inspect_upload_security(
            "laporan.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            office_payload("word/document.xml"),
        )
        self.assertEqual(kind, "docx")
        self.assertFalse(any(item.blocking for item in findings))

    def test_blocks_office_path_traversal(self) -> None:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", b"<xml/>")
            archive.writestr("../escape.txt", b"unsafe")
        _, findings = inspect_upload_security(
            "laporan.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer.getvalue(),
        )
        self.assertTrue(any(item.code == "archive_path_traversal" and item.blocking for item in findings))

    def test_allowlist_matches_host_or_subdomain_only(self) -> None:
        allowed = {"docs.google.com", "googleusercontent.com"}
        self.assertTrue(host_is_allowed("docs.google.com", allowed))
        self.assertTrue(host_is_allowed("a.googleusercontent.com", allowed))
        self.assertFalse(host_is_allowed("docs.google.com.example.org", allowed))

    def test_blocks_office_zip_bomb_limits(self) -> None:
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"A" * 20_000)
            archive.writestr("word/media/image.bin", b"B" * 20_000)
        findings = inspect_office_archive(
            buffer.getvalue(),
            "docx",
            max_entries=1,
            max_uncompressed_bytes=1_000,
            max_compression_ratio=2,
        )
        codes = {item.code for item in findings if item.blocking}
        self.assertIn("archive_entry_limit", codes)
        self.assertIn("archive_uncompressed_limit", codes)
        self.assertIn("archive_compression_ratio", codes)

    def test_blocks_binary_payload_declared_as_text(self) -> None:
        kind, findings = inspect_upload_security("payload.txt", "text/plain", b"safe\x00binary")
        self.assertEqual(kind, "text")
        self.assertTrue(any(item.code == "binary_text_payload" and item.blocking for item in findings))

    @patch("app.analysis.security.socket.getaddrinfo")
    def test_ssrf_blocks_private_dns_resolution(self, getaddrinfo) -> None:
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        valid, message = validate_external_url("https://docs.google.com/evidence", {"docs.google.com"})
        self.assertFalse(valid)
        self.assertIn("privat", message)

    def test_link_content_type_allowlist(self) -> None:
        self.assertEqual(validate_link_content_type("application/pdf; charset=binary"), (True, ""))
        valid, message = validate_link_content_type("application/x-executable")
        self.assertFalse(valid)
        self.assertIn("tidak diizinkan", message)


if __name__ == "__main__":
    unittest.main()
