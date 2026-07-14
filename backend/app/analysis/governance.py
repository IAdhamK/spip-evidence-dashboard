from __future__ import annotations

import binascii
import hashlib
import json
import struct
import zlib

from app.analysis.provider import CompatibleChatVisionProvider, VisionModelProvider
from app.config import Settings


VISION_POLICY_VERSION = "vision-governance-v1"
VISION_PROBE_UNIT_KEY = "synthetic-vision-probe"
VISION_PROBE_EXPECTED_TOKENS = ["SPIP", "2026"]


def run_synthetic_vision_probe(
    settings: Settings,
    provider: VisionModelProvider | None = None,
) -> dict:
    base = {
        "provider": settings.ai_provider,
        "model": settings.deepseek_model,
        "api_surface": "chat_completions_vision",
        "expected_tokens": VISION_PROBE_EXPECTED_TOKENS,
        "synthetic_only": True,
        "user_document_sent": False,
    }
    observed_text = ""
    warnings: list[str] = []
    error_message = None
    passed = False
    if not settings.has_ai_key:
        error_message = "API key provider belum dikonfigurasi."
    else:
        try:
            effective_provider = provider or CompatibleChatVisionProvider(settings)
            response = effective_provider.analyze_images(
                [{
                    "unit_key": VISION_PROBE_UNIT_KEY,
                    "mime_type": "image/png",
                    "payload": synthetic_probe_png(),
                }]
            )
            warnings = list(response.warnings)
            matching = next(
                (item for item in response.items if item.unit_key == VISION_PROBE_UNIT_KEY),
                None,
            )
            if not matching:
                error_message = "Provider tidak mengembalikan unit_key synthetic yang diminta."
            else:
                observed_text = " ".join(matching.ocr_text.split())[:1000]
                normalized = observed_text.upper()
                missing = [token for token in VISION_PROBE_EXPECTED_TOKENS if token not in normalized]
                if missing:
                    error_message = "OCR synthetic tidak membaca token wajib: " + ", ".join(missing)
                else:
                    passed = True
        except Exception as exc:  # provider/network errors are captured as fail-closed evidence
            error_message = f"Uji provider gagal: {exc}"[:1000]

    report = {
        **base,
        "status": "passed" if passed else "failed",
        "observed_text": observed_text,
        "warnings": warnings[:20],
        "error_message": error_message,
    }
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return report


def synthetic_probe_png() -> bytes:
    glyphs = {
        "S": ("11111", "10000", "10000", "11111", "00001", "00001", "11111"),
        "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
        "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
        "2": ("11110", "00001", "00001", "11110", "10000", "10000", "11111"),
        "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
        "6": ("01111", "10000", "10000", "11110", "10001", "10001", "01110"),
        " ": ("00000",) * 7,
    }
    text = "SPIP 2026"
    scale = 6
    margin = 12
    glyph_width = 5
    width = margin * 2 + (len(text) * (glyph_width + 1) - 1) * scale
    height = margin * 2 + 7 * scale
    pixels = [[255] * width for _ in range(height)]
    cursor = margin
    for character in text:
        glyph = glyphs[character]
        for row_index, row in enumerate(glyph):
            for column_index, value in enumerate(row):
                if value != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        pixels[margin + row_index * scale + dy][cursor + column_index * scale + dx] = 0
        cursor += (glyph_width + 1) * scale

    raw = b"".join(b"\x00" + bytes(row) for row in pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(
        b"IDAT", zlib.compress(raw, 9)
    ) + _png_chunk(b"IEND", b"")


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)
