from __future__ import annotations

import subprocess
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from app.analysis.office_render import (
    OfficeRenderError,
    convert_office_to_pdf,
    render_pptx_slide,
)


class OfficeSlideRenderTests(unittest.TestCase):
    def test_render_uses_isolated_profile_and_exact_slide(self) -> None:
        commands: list[list[str]] = []
        payload_buffer = BytesIO()
        with ZipFile(payload_buffer, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<slide/>")

        def fake_run(command, **_kwargs):
            commands.append(command)
            if "--convert-to" in command:
                out_dir = Path(command[command.index("--outdir") + 1])
                (out_dir / "source.pdf").write_bytes(b"rendered-pdf")
            else:
                Path(f"{command[-1]}.png").write_bytes(b"slide-three-png")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            patch("app.analysis.office_render.office_renderer_binary", return_value="/usr/bin/soffice"),
            patch("app.analysis.office_render.shutil.which", return_value="/usr/bin/pdftoppm"),
            patch("app.analysis.office_render.subprocess.run", side_effect=fake_run),
        ):
            rendered = render_pptx_slide(
                payload_buffer.getvalue(), 3, dpi=180, timeout_seconds=12
            )

        self.assertEqual(rendered, b"slide-three-png")
        self.assertEqual(len(commands), 2)
        self.assertIn("--headless", commands[0])
        self.assertTrue(any(arg.startswith("-env:UserInstallation=file:") for arg in commands[0]))
        self.assertEqual(commands[1][commands[1].index("-f") + 1], "3")
        self.assertEqual(commands[1][commands[1].index("-l") + 1], "3")
        self.assertEqual(commands[1][commands[1].index("-r") + 1], "180")

    def test_missing_renderer_and_invalid_slide_fail_closed(self) -> None:
        with self.assertRaisesRegex(OfficeRenderError, "Nomor slide"):
            render_pptx_slide(b"pptx", 0)
        with patch("app.analysis.office_render.office_renderer_binary", return_value=None):
            with self.assertRaisesRegex(OfficeRenderError, "wajib tersedia"):
                render_pptx_slide(b"pptx", 1)

    def test_external_relationships_and_embedded_objects_are_removed_from_render_copy(self) -> None:
        payload_buffer = BytesIO()
        with ZipFile(payload_buffer, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<slide/>")
            archive.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId1" Target="../media/image1.png"/>
                <Relationship Id="rIdInternal" Target="/ppt/slides/slide1.xml"/>
                <Relationship Id="rId2" Target="http://127.0.0.1/private" TargetMode="External"/>
                </Relationships>""",
            )
            archive.writestr("ppt/embeddings/object1.bin", b"embedded")
            archive.writestr("ppt/media/image1.png", b"image")

        inspected = {}

        def fake_run(command, **_kwargs):
            if "--convert-to" in command:
                source_path = Path(command[-1])
                with ZipFile(source_path) as archive:
                    inspected["names"] = archive.namelist()
                    inspected["rels"] = archive.read(
                        "ppt/slides/_rels/slide1.xml.rels"
                    ).decode("utf-8")
                (source_path.parent / "source.pdf").write_bytes(b"pdf")
            else:
                Path(f"{command[-1]}.png").write_bytes(b"png")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            patch("app.analysis.office_render.office_renderer_binary", return_value="/usr/bin/soffice"),
            patch("app.analysis.office_render.shutil.which", return_value="/usr/bin/pdftoppm"),
            patch("app.analysis.office_render.subprocess.run", side_effect=fake_run),
        ):
            render_pptx_slide(payload_buffer.getvalue(), 1)

        self.assertNotIn("ppt/embeddings/object1.bin", inspected["names"])
        self.assertIn("../media/image1.png", inspected["rels"])
        self.assertIn("/ppt/slides/slide1.xml", inspected["rels"])
        self.assertNotIn("127.0.0.1", inspected["rels"])

    def test_docx_conversion_uses_writer_filter_and_sanitized_copy(self) -> None:
        payload_buffer = BytesIO()
        with ZipFile(payload_buffer, "w") as archive:
            archive.writestr("word/document.xml", "<document/>")
            archive.writestr(
                "word/_rels/document.xml.rels",
                """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                <Relationship Id="rId1" Target="https://example.invalid/source" TargetMode="External"/>
                </Relationships>""",
            )
            archive.writestr("word/embeddings/object1.bin", b"embedded")
        inspected = {}

        def fake_run(command, **_kwargs):
            inspected["command"] = command
            source_path = Path(command[-1])
            with ZipFile(source_path) as archive:
                inspected["names"] = archive.namelist()
                inspected["rels"] = archive.read(
                    "word/_rels/document.xml.rels"
                ).decode("utf-8")
            (source_path.parent / "source.pdf").write_bytes(b"writer-pdf")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            patch("app.analysis.office_render.office_renderer_binary", return_value="/usr/bin/soffice"),
            patch("app.analysis.office_render.subprocess.run", side_effect=fake_run),
        ):
            pdf = convert_office_to_pdf(payload_buffer.getvalue(), "docx")

        self.assertEqual(pdf, b"writer-pdf")
        self.assertIn("pdf:writer_pdf_Export", inspected["command"])
        self.assertNotIn("word/embeddings/object1.bin", inspected["names"])
        self.assertNotIn("example.invalid", inspected["rels"])


if __name__ == "__main__":
    unittest.main()
