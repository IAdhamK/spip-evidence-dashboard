from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
from io import BytesIO, StringIO
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
from time import perf_counter
from typing import Protocol
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings


@dataclass
class LocalOCRItem:
    unit_key: str
    text: str
    confidence: float
    regions: list[dict] = field(default_factory=list)
    method: str = "local_ocr"
    languages: list[str] = field(default_factory=list)


@dataclass
class LocalOCRResponse:
    items: list[LocalOCRItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class _UnitOCRBudget:
    document_deadline: float
    unit_deadline: float
    max_attempts: int
    attempts: int = 0
    timeouts: int = 0
    exhaustion_reason: str | None = None

    def claim_timeout(self, requested_seconds: float) -> float | None:
        if self.attempts >= self.max_attempts:
            self.exhaustion_reason = "unit_attempt_budget_exhausted"
            return None
        now = perf_counter()
        remaining_document = self.document_deadline - now
        remaining_unit = self.unit_deadline - now
        remaining = min(remaining_document, remaining_unit)
        if remaining <= 0.25:
            self.exhaustion_reason = (
                "document_time_budget_exhausted"
                if remaining_document <= remaining_unit
                else "unit_time_budget_exhausted"
            )
            return None
        self.attempts += 1
        return max(0.25, min(float(requested_seconds), remaining))


class LocalOCRProvider(Protocol):
    name: str

    def analyze_images(self, images: list[dict]) -> LocalOCRResponse: ...


class TesseractLocalOCRProvider:
    name = "tesseract"

    def __init__(
        self,
        binary: str,
        languages: str,
        timeout_seconds: int,
        minimum_confidence: float = 0.45,
        psm_modes: str = "6,3,11",
        preprocessing_enabled: bool = True,
        max_image_pixels: int = 16_000_000,
        max_tiles: int = 16,
        unit_budget_seconds: int = 180,
        document_budget_seconds: int = 900,
        max_attempts_per_unit: int = 24,
    ):
        self.binary = binary
        self.requested_languages = [item for item in languages.split("+") if item]
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.minimum_confidence = max(0.0, min(1.0, float(minimum_confidence)))
        self.psm_modes = _parse_psm_modes(psm_modes)
        self.preprocessing_enabled = bool(preprocessing_enabled)
        self.max_image_pixels = max(10_000, int(max_image_pixels))
        self.max_tiles = max(1, min(100, int(max_tiles)))
        self.unit_budget_seconds = max(self.timeout_seconds, int(unit_budget_seconds))
        self.document_budget_seconds = max(
            self.unit_budget_seconds, int(document_budget_seconds)
        )
        self.max_attempts_per_unit = max(1, min(1_000, int(max_attempts_per_unit)))
        self.languages = self._available_languages()

    def analyze_images(self, images: list[dict]) -> LocalOCRResponse:
        started = perf_counter()
        items: list[LocalOCRItem] = []
        warnings: list[str] = []
        attempt_count = 0
        timeout_count = 0
        exhausted_units: dict[str, str] = {}
        language_arg = "+".join(self.languages)
        default_document_deadline = started + self.document_budget_seconds
        for image in images:
            best_item: LocalOCRItem | None = None
            attempt_warnings: list[str] = []
            document_deadline = min(
                default_document_deadline,
                float(
                    image.get("_ocr_document_deadline_monotonic")
                    or default_document_deadline
                ),
            )
            budget = _UnitOCRBudget(
                document_deadline=document_deadline,
                unit_deadline=min(
                    document_deadline, perf_counter() + self.unit_budget_seconds
                ),
                max_attempts=self.max_attempts_per_unit,
            )
            if document_deadline - perf_counter() <= 0.25:
                budget.exhaustion_reason = "document_time_budget_exhausted"
                reason = budget.exhaustion_reason
                exhausted_units[str(image.get("unit_key") or "unknown")] = reason
                warnings.append(
                    f"Tesseract {image.get('unit_key')} ditunda: {reason}."
                )
                continue
            try:
                payload = bytes(image["payload"])
                width, height = _image_dimensions(payload)
                if (
                    width
                    and height
                    and width * height > self.max_image_pixels
                ):
                    tiled_item, tiled_warnings, tile_count = self._analyze_tiled_image(
                        str(image["unit_key"]),
                        payload,
                        language_arg,
                        budget,
                    )
                    if tiled_item:
                        items.append(tiled_item)
                        warnings.append(
                            f"Tesseract {image['unit_key']} memakai {tile_count} tile "
                            f"karena raster {width}x{height} melampaui pixel budget."
                        )
                    else:
                        warnings.append(
                            f"Tesseract tiled gagal pada {image['unit_key']}: "
                            + "; ".join(tiled_warnings)[:400]
                        )
                    continue
                variants = [(
                    "raw",
                    payload,
                    _image_suffix(str(image.get("mime_type") or "")),
                )]
                render_dpi = int(image.get("render_dpi") or 0)
                if self.preprocessing_enabled and (not render_dpi or render_dpi >= 200):
                    variants.extend(
                        (name, variant_payload, ".png")
                        for name, variant_payload in _preprocessed_image_variants(payload)
                    )
                for variant_name, variant_payload, suffix in variants:
                    variant_best = self._analyze_variant(
                        str(image["unit_key"]),
                        variant_payload,
                        variant_name,
                        language_arg,
                        attempt_warnings,
                        suffix,
                        budget,
                    )
                    if variant_best and (
                        best_item is None
                        or (variant_best.confidence, len(variant_best.text))
                        > (best_item.confidence, len(best_item.text))
                    ):
                        best_item = variant_best
                    if best_item and best_item.confidence >= self.minimum_confidence:
                        break
                if best_item:
                    items.append(best_item)
                elif attempt_warnings:
                    warnings.append(
                        f"Tesseract gagal pada {image['unit_key']}: "
                        + "; ".join(attempt_warnings)[:400]
                    )
                else:
                    warnings.append(f"Tesseract tidak menemukan teks pada {image['unit_key']}.")
            except (OSError, ValueError) as exc:
                warnings.append(f"Tesseract gagal pada {image['unit_key']}: {exc}"[:500])
            finally:
                attempt_count += budget.attempts
                timeout_count += budget.timeouts
                if budget.exhaustion_reason:
                    unit_key = str(image.get("unit_key") or "unknown")
                    exhausted_units[unit_key] = budget.exhaustion_reason
                    warnings.append(
                        f"Tesseract {unit_key} berhenti aman: {budget.exhaustion_reason}."
                    )
        return LocalOCRResponse(
            items=items,
            warnings=warnings,
            metrics={
                "attempt_count": attempt_count,
                "timeout_count": timeout_count,
                "budget_exhausted_unit_count": len(exhausted_units),
                "budget_exhaustion_reasons": dict(sorted(exhausted_units.items())),
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "unit_budget_seconds": self.unit_budget_seconds,
                "document_budget_seconds": self.document_budget_seconds,
                "max_attempts_per_unit": self.max_attempts_per_unit,
            },
        )

    def _analyze_tiled_image(
        self,
        unit_key: str,
        payload: bytes,
        language_arg: str,
        budget: _UnitOCRBudget,
    ) -> tuple[LocalOCRItem | None, list[str], int]:
        try:
            tiles, full_size = _tiled_image_payloads(
                payload,
                max_pixels=self.max_image_pixels,
                max_tiles=self.max_tiles,
            )
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            return None, [str(exc)], 0
        merged_items: list[tuple[LocalOCRItem, tuple[int, int, int, int]]] = []
        warnings: list[str] = []
        for index, (tile_payload, bounds) in enumerate(tiles, start=1):
            tile_warnings: list[str] = []
            item = self._analyze_variant(
                f"{unit_key}-tile-{index}",
                tile_payload,
                "tile",
                language_arg,
                tile_warnings,
                ".png",
                budget,
            )
            if item:
                merged_items.append((item, bounds))
                continue
            if tile_warnings:
                warnings.extend(
                    f"tile {index}: {warning}" for warning in tile_warnings
                )
        if warnings:
            return None, warnings, len(tiles)
        if not merged_items:
            return None, ["tidak ada teks pada seluruh tile"], len(tiles)
        return (
            _merge_tiled_ocr_items(unit_key, merged_items, full_size, self.languages),
            [],
            len(tiles),
        )

    def _analyze_variant(
        self,
        unit_key: str,
        payload: bytes,
        variant_name: str,
        language_arg: str,
        attempt_warnings: list[str],
        suffix: str,
        budget: _UnitOCRBudget,
    ) -> LocalOCRItem | None:
        best_item: LocalOCRItem | None = None
        with tempfile.NamedTemporaryFile(suffix=suffix) as source:
            source.write(payload)
            source.flush()
            for psm_mode in self.psm_modes:
                attempt_timeout = budget.claim_timeout(self.timeout_seconds)
                if attempt_timeout is None:
                    attempt_warnings.append(
                        f"{variant_name} PSM {psm_mode} {budget.exhaustion_reason}"
                    )
                    break
                command = [self.binary, source.name, "stdout"]
                if language_arg:
                    command.extend(["-l", language_arg])
                command.extend(["--psm", str(psm_mode), "tsv"])
                try:
                    result = subprocess.run(
                        command,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=attempt_timeout,
                    )
                except subprocess.TimeoutExpired:
                    budget.timeouts += 1
                    attempt_warnings.append(f"{variant_name} PSM {psm_mode} timeout")
                    continue
                except (subprocess.CalledProcessError, OSError) as exc:
                    attempt_warnings.append(
                        f"{variant_name} PSM {psm_mode} gagal: {exc}"
                    )
                    continue
                version = "v1" if variant_name == "raw" else "v2"
                method_variant = "" if variant_name == "raw" else f"_{variant_name}"
                item = _parse_tesseract_tsv(
                    unit_key,
                    result.stdout,
                    payload,
                    self.languages,
                    method=f"local_tesseract{method_variant}_psm_{psm_mode}_{version}",
                )
                if item.text and (
                    best_item is None
                    or (item.confidence, len(item.text))
                    > (best_item.confidence, len(best_item.text))
                ):
                    best_item = item
                if item.text and item.confidence >= self.minimum_confidence:
                    break
        return best_item

    def _available_languages(self) -> list[str]:
        try:
            result = subprocess.run(
                [self.binary, "--list-langs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            available = {
                line.strip() for line in result.stdout.splitlines()
                if line.strip() and not line.lower().startswith("list of available")
            }
        except (subprocess.SubprocessError, OSError):
            available = set()
        selected = [language for language in self.requested_languages if language in available]
        if not selected and "eng" in available:
            selected = ["eng"]
        return selected


class AppleVisionLocalOCRProvider:
    name = "apple_vision"
    _compile_lock = threading.Lock()
    _cached_binary: Path | None = None
    _compile_error: str | None = None
    _runtime_available: bool | None = None
    _runtime_error: str | None = None

    def __init__(
        self,
        swiftc: str,
        timeout_seconds: int,
        document_budget_seconds: int = 900,
    ):
        self.swiftc = swiftc
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.document_budget_seconds = max(
            self.timeout_seconds, int(document_budget_seconds)
        )
        self.script_path = Path(__file__).with_name("local_ocr_vision.swift")

    def analyze_images(self, images: list[dict]) -> LocalOCRResponse:
        started = perf_counter()
        binary = self._compiled_binary()
        items: list[LocalOCRItem] = []
        warnings: list[str] = []
        attempts = 0
        timeouts = 0
        exhausted_units: dict[str, str] = {}
        default_deadline = started + self.document_budget_seconds
        for image in images:
            unit_key = str(image.get("unit_key") or "unknown")
            deadline = min(
                default_deadline,
                float(
                    image.get("_ocr_document_deadline_monotonic")
                    or default_deadline
                ),
            )
            remaining = deadline - perf_counter()
            if remaining <= 0.25:
                exhausted_units[unit_key] = "document_time_budget_exhausted"
                warnings.append(
                    f"Apple Vision {unit_key} ditunda: document_time_budget_exhausted."
                )
                continue
            suffix = _image_suffix(str(image.get("mime_type") or ""))
            try:
                attempts += 1
                with tempfile.NamedTemporaryFile(suffix=suffix) as source:
                    source.write(bytes(image["payload"]))
                    source.flush()
                    result = subprocess.run(
                        [str(binary), source.name],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=max(0.25, min(self.timeout_seconds, remaining)),
                    )
                payload = json.loads(result.stdout)
                text = " ".join(str(payload.get("text") or "").split())
                if not text:
                    warnings.append(f"Apple Vision tidak menemukan teks pada {image['unit_key']}.")
                    continue
                items.append(LocalOCRItem(
                    unit_key=str(image["unit_key"]),
                    text=text,
                    confidence=max(0.0, min(1.0, float(payload.get("confidence") or 0))),
                    regions=_normalized_regions(payload.get("regions") or []),
                    method="local_apple_vision_v1",
                    languages=[str(value) for value in payload.get("languages") or []],
                ))
            except subprocess.TimeoutExpired:
                timeouts += 1
                warnings.append(f"Apple Vision timeout pada {image['unit_key']}.")
            except (subprocess.CalledProcessError, OSError, ValueError, json.JSONDecodeError) as exc:
                detail = str(exc)
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                    detail = f"{detail}; stderr={exc.stderr.strip()}"
                warnings.append(f"Apple Vision gagal pada {image['unit_key']}: {detail}"[:500])
        return LocalOCRResponse(
            items=items,
            warnings=warnings,
            metrics={
                "attempt_count": attempts,
                "timeout_count": timeouts,
                "budget_exhausted_unit_count": len(exhausted_units),
                "budget_exhaustion_reasons": exhausted_units,
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "document_budget_seconds": self.document_budget_seconds,
                "max_attempts_per_unit": 1,
            },
        )

    def available(self) -> bool:
        if self.__class__._runtime_available is not None:
            return self.__class__._runtime_available
        try:
            binary = self._compiled_binary()
            from app.analysis.governance import synthetic_probe_png

            with tempfile.NamedTemporaryFile(suffix=".png") as source:
                source.write(synthetic_probe_png())
                source.flush()
                result = subprocess.run(
                    [str(binary), source.name],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            json.loads(result.stdout)
            self.__class__._runtime_available = True
            return True
        except (RuntimeError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            detail = str(exc)
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                detail = f"{detail}; stderr={exc.stderr.strip()}"
            self.__class__._runtime_error = detail[:500]
            self.__class__._runtime_available = False
            return False

    def _compiled_binary(self) -> Path:
        if self.__class__._cached_binary is not None:
            return self.__class__._cached_binary
        if self.__class__._compile_error is not None:
            raise RuntimeError(self.__class__._compile_error)
        if not self.script_path.is_file():
            raise RuntimeError("Script Apple Vision OCR tidak ditemukan.")
        digest = hashlib.sha256(self.script_path.read_bytes()).hexdigest()[:16]
        binary = Path(tempfile.gettempdir()) / f"spip-local-ocr-vision-{digest}"
        if binary.is_file() and os.access(binary, os.X_OK):
            self.__class__._cached_binary = binary
            return binary
        with self._compile_lock:
            if binary.is_file() and os.access(binary, os.X_OK):
                self.__class__._cached_binary = binary
                return binary
            try:
                environment = dict(os.environ)
                environment["CLANG_MODULE_CACHE_PATH"] = str(
                    Path(tempfile.gettempdir()) / "spip-swift-module-cache"
                )
                subprocess.run(
                    [self.swiftc, "-O", str(self.script_path), "-o", str(binary)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(60, self.timeout_seconds),
                    env=environment,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                self.__class__._compile_error = f"Apple Vision helper tidak dapat dikompilasi: {exc}"
                raise RuntimeError(self.__class__._compile_error) from exc
        self.__class__._cached_binary = binary
        return binary


def configured_local_ocr_provider(settings: Settings) -> LocalOCRProvider | None:
    if not settings.analysis_local_ocr_enabled:
        return None
    requested = str(settings.analysis_local_ocr_provider or "auto").strip().lower()
    if requested in {"auto", "tesseract"}:
        binary = shutil.which("tesseract")
        if binary:
            return TesseractLocalOCRProvider(
                binary,
                settings.analysis_local_ocr_languages,
                settings.analysis_local_ocr_timeout_seconds,
                settings.analysis_local_ocr_min_confidence,
                settings.analysis_local_ocr_tesseract_psm_modes,
                settings.analysis_local_ocr_preprocessing_enabled,
                settings.analysis_local_ocr_max_image_pixels,
                settings.analysis_local_ocr_max_tiles,
                settings.analysis_local_ocr_unit_budget_seconds,
                settings.analysis_local_ocr_document_budget_seconds,
                settings.analysis_local_ocr_max_attempts_per_unit,
            )
        if requested == "tesseract":
            return None
    if requested in {"auto", "apple_vision"} and platform.system() == "Darwin":
        swiftc = shutil.which("swiftc")
        if swiftc and Path(__file__).with_name("local_ocr_vision.swift").is_file():
            provider = AppleVisionLocalOCRProvider(
                swiftc,
                settings.analysis_local_ocr_timeout_seconds,
                settings.analysis_local_ocr_document_budget_seconds,
            )
            return provider if provider.available() else None
    return None


def local_ocr_runtime_status(
    settings: Settings,
    provider: LocalOCRProvider | None = None,
) -> dict:
    provider = provider or configured_local_ocr_provider(settings)
    reason = None
    if provider is None:
        requested = str(settings.analysis_local_ocr_provider or "auto").strip().lower()
        if not settings.analysis_local_ocr_enabled:
            reason = "Local OCR dinonaktifkan oleh konfigurasi."
        elif requested in {"auto", "tesseract"} and not shutil.which("tesseract"):
            reason = "Binary Tesseract tidak ditemukan."
        if (
            requested in {"auto", "apple_vision"}
            and platform.system() == "Darwin"
            and AppleVisionLocalOCRProvider._runtime_error
        ):
            reason = "Apple Vision runtime self-test gagal: " + AppleVisionLocalOCRProvider._runtime_error
    return {
        "enabled": bool(settings.analysis_local_ocr_enabled),
        "requested_provider": settings.analysis_local_ocr_provider,
        "available": provider is not None,
        "provider": getattr(provider, "name", None),
        "languages": (
            list(getattr(provider, "languages", []))
            if provider else []
        ),
        "min_confidence": max(0.0, min(1.0, settings.analysis_local_ocr_min_confidence)),
        "max_units": max(1, int(settings.analysis_local_ocr_max_units)),
        "timeout_seconds": max(3, int(settings.analysis_local_ocr_timeout_seconds)),
        "unit_budget_seconds": max(
            max(3, int(settings.analysis_local_ocr_timeout_seconds)),
            int(settings.analysis_local_ocr_unit_budget_seconds),
        ),
        "document_budget_seconds": max(
            max(3, int(settings.analysis_local_ocr_timeout_seconds)),
            int(settings.analysis_local_ocr_unit_budget_seconds),
            int(settings.analysis_local_ocr_document_budget_seconds),
        ),
        "max_attempts_per_unit": max(
            1, min(1_000, int(settings.analysis_local_ocr_max_attempts_per_unit))
        ),
        "render_batch_units": max(
            1, min(100, int(settings.analysis_local_ocr_render_batch_units))
        ),
        "tesseract_psm_modes": _parse_psm_modes(
            settings.analysis_local_ocr_tesseract_psm_modes
        ),
        "preprocessing_enabled": bool(
            settings.analysis_local_ocr_preprocessing_enabled
        ),
        "max_image_pixels": max(10_000, int(settings.analysis_local_ocr_max_image_pixels)),
        "max_tiles": max(1, min(100, int(settings.analysis_local_ocr_max_tiles))),
        "pdf_render_dpi": max(72, min(300, int(settings.analysis_pdf_render_dpi))),
        "pdf_retry_render_dpi": max(
            72, min(300, int(settings.analysis_pdf_retry_render_dpi))
        ),
        "pdf_retry_max_units": max(0, int(settings.analysis_pdf_retry_max_units)),
        "external_data_sent": False,
        "requires_external_consent": False,
        "availability_reason": reason,
    }


def _parse_tesseract_tsv(
    unit_key: str,
    tsv: str,
    image_payload: bytes,
    languages: list[str],
    method: str = "local_tesseract_psm_6_v1",
) -> LocalOCRItem:
    width, height = _image_dimensions(image_payload)
    line_groups: dict[tuple[str, str, str, str], list[dict]] = {}
    # Tesseract may recognize a literal double quote. TSV is not CSV-quoted;
    # disabling quote parsing prevents one unmatched glyph from swallowing rows.
    for row in csv.DictReader(
        StringIO(tsv),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    ):
        text = " ".join(str(row.get("text") or "").split())
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            confidence = -1
        if not text or confidence < 0:
            continue
        key = tuple(str(row.get(field) or "") for field in ("page_num", "block_num", "par_num", "line_num"))
        line_groups.setdefault(key, []).append({**row, "text": text, "confidence": confidence})
    regions = []
    for words in line_groups.values():
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        text = " ".join(word["text"] for word in words)
        weight = sum(max(1, len(word["text"])) for word in words)
        confidence = sum(word["confidence"] * max(1, len(word["text"])) for word in words) / max(1, weight) / 100
        bbox = {
            "x": left / width if width else 0,
            "y": top / height if height else 0,
            "width": (right - left) / width if width else 0,
            "height": (bottom - top) / height if height else 0,
        }
        regions.append({
            "text": text,
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "bbox": bbox,
            "coordinate_space": "normalized_top_left",
        })
    text = " ".join(region["text"] for region in regions)
    total_weight = sum(max(1, len(region["text"])) for region in regions)
    confidence = sum(region["confidence"] * max(1, len(region["text"])) for region in regions) / max(1, total_weight)
    return LocalOCRItem(
        unit_key=unit_key,
        text=text,
        confidence=round(confidence, 4),
        regions=regions,
        method=method,
        languages=languages,
    )


def _tiled_image_payloads(
    payload: bytes,
    *,
    max_pixels: int,
    max_tiles: int,
) -> tuple[list[tuple[bytes, tuple[int, int, int, int]]], tuple[int, int]]:
    detected_width, detected_height = _image_dimensions(payload)
    if not detected_width or not detected_height:
        raise ValueError("Dimensi raster OCR tidak dapat diverifikasi dari header.")
    width, height = detected_width, detected_height
    if width * height <= max_pixels:
        return [(payload, (0, 0, width, height))], (width, height)
    columns, rows = _tile_grid(width, height, max_pixels)
    tile_count = columns * rows
    if tile_count > max_tiles:
        raise ValueError(
            f"raster {width}x{height} membutuhkan {tile_count} tile, "
            f"melebihi budget {max_tiles}"
        )
    with warnings.catch_warnings():
        # Header dimensions and an explicit tile budget were validated above.
        # Preserve Pillow's hard DecompressionBombError while avoiding its
        # lower advisory warning for renderer-produced rasters within budget.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(BytesIO(payload)) as source:
            source.load()
            if source.size != (width, height):
                raise ValueError("Dimensi header raster berubah saat decoding.")
            tiles: list[tuple[bytes, tuple[int, int, int, int]]] = []
            tile_width = math.ceil(width / columns)
            tile_height = math.ceil(height / rows)
            rgb = source.convert("RGB")
            for row in range(rows):
                top = row * tile_height
                bottom = min(height, top + tile_height)
                for column in range(columns):
                    left = column * tile_width
                    right = min(width, left + tile_width)
                    if right <= left or bottom <= top:
                        continue
                    tile = rgb.crop((left, top, right, bottom))
                    buffer = BytesIO()
                    tile.save(buffer, format="PNG", compress_level=1)
                    tiles.append((buffer.getvalue(), (left, top, right, bottom)))
            return tiles, (width, height)


def local_ocr_tile_requirement(
    payload: bytes,
    max_pixels: int,
) -> tuple[int, int, int]:
    """Return header-verified width, height, and required tile count."""
    width, height = _image_dimensions(payload)
    if not width or not height:
        raise ValueError("Dimensi raster OCR tidak dapat diverifikasi dari header.")
    columns, rows = _tile_grid(width, height, max_pixels)
    return width, height, columns * rows


def _tile_grid(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    effective_pixels = max(1, int(max_pixels))
    if width * height <= effective_pixels:
        return 1, 1
    target_side = max(1, math.isqrt(effective_pixels))
    columns = max(1, math.ceil(width / target_side))
    rows = max(1, math.ceil(height / target_side))
    while math.ceil(width / columns) * math.ceil(height / rows) > effective_pixels:
        if math.ceil(width / columns) >= math.ceil(height / rows):
            columns += 1
        else:
            rows += 1
    return columns, rows


def _merge_tiled_ocr_items(
    unit_key: str,
    items: list[tuple[LocalOCRItem, tuple[int, int, int, int]]],
    full_size: tuple[int, int],
    languages: list[str],
) -> LocalOCRItem:
    full_width, full_height = full_size
    regions: list[dict] = []
    texts: list[str] = []
    weighted_confidence = 0.0
    total_weight = 0
    for item, (left, top, right, bottom) in items:
        tile_width = max(1, right - left)
        tile_height = max(1, bottom - top)
        text = " ".join(item.text.split())
        if text:
            texts.append(text)
            weight = max(1, len(text))
            weighted_confidence += item.confidence * weight
            total_weight += weight
        for region in item.regions:
            bbox = region.get("bbox") or {}
            regions.append({
                **region,
                "bbox": {
                    "x": (left + float(bbox.get("x") or 0) * tile_width) / full_width,
                    "y": (top + float(bbox.get("y") or 0) * tile_height) / full_height,
                    "width": float(bbox.get("width") or 0) * tile_width / full_width,
                    "height": float(bbox.get("height") or 0) * tile_height / full_height,
                },
                "coordinate_space": "normalized_top_left",
            })
    return LocalOCRItem(
        unit_key=unit_key,
        text=" ".join(texts),
        confidence=round(weighted_confidence / max(1, total_weight), 4),
        regions=_normalized_regions(regions),
        method="local_tesseract_tiled_v1",
        languages=languages,
    )


def _preprocessed_image_variants(payload: bytes) -> list[tuple[str, bytes]]:
    """Create geometry-preserving OCR variants; normalized regions remain comparable."""
    try:
        with Image.open(BytesIO(payload)) as source:
            source.load()
            variants: list[tuple[str, Image.Image]] = []
            rgba = source.convert("RGBA")
            alpha = rgba.getchannel("A")
            extrema = alpha.getextrema()
            if extrema and extrema != (255, 255):
                variants.append(("alpha_mask", ImageOps.invert(alpha)))
                flattened = Image.new("RGB", source.size, "white")
                flattened.paste(rgba, mask=alpha)
            else:
                flattened = rgba.convert("RGB")
            variants.append(("enhanced", ImageOps.autocontrast(ImageOps.grayscale(flattened))))

            output: list[tuple[str, bytes]] = []
            seen = {hashlib.sha256(payload).digest()}
            for name, variant in variants:
                width, height = variant.size
                if width < 1 or height < 1:
                    continue
                scale = min(
                    4.0,
                    max(1.0, 1000 / width if width < 1000 else 1.0, 256 / height if height < 256 else 1.0),
                )
                if scale > 1:
                    variant = variant.resize(
                        (max(1, round(width * scale)), max(1, round(height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                buffer = BytesIO()
                variant.save(buffer, format="PNG", optimize=True)
                encoded = buffer.getvalue()
                digest = hashlib.sha256(encoded).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                output.append((name, encoded))
            return output
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        return []


def _parse_psm_modes(value: str) -> list[int]:
    modes: list[int] = []
    for raw in str(value or "").replace("+", ",").split(","):
        try:
            mode = int(raw.strip())
        except ValueError:
            continue
        if 0 <= mode <= 13 and mode not in modes:
            modes.append(mode)
    return modes or [6]


def _normalized_regions(regions: list[dict]) -> list[dict]:
    normalized = []
    for region in regions[:1000]:
        bbox = region.get("bbox") or {}
        normalized.append({
            "text": " ".join(str(region.get("text") or "").split())[:2000],
            "confidence": round(max(0.0, min(1.0, float(region.get("confidence") or 0))), 4),
            "bbox": {
                key: round(max(0.0, min(1.0, float(bbox.get(key) or 0))), 6)
                for key in ("x", "y", "width", "height")
            },
            "coordinate_space": "normalized_top_left",
        })
    return normalized


def _image_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")
    if payload.startswith((b"GIF87a", b"GIF89a")) and len(payload) >= 10:
        return int.from_bytes(payload[6:8], "little"), int.from_bytes(payload[8:10], "little")
    if payload.startswith(b"BM") and len(payload) >= 26:
        return int.from_bytes(payload[18:22], "little"), abs(
            int.from_bytes(payload[22:26], "little", signed=True)
        )
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
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
                height = int.from_bytes(payload[offset + 3:offset + 5], "big")
                width = int.from_bytes(payload[offset + 5:offset + 7], "big")
                return width, height
            offset += length
    return None, None


def _image_suffix(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }.get(mime_type.lower(), ".png")
