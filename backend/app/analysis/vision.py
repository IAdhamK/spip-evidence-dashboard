from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Callable
from zipfile import ZipFile

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.local_ocr import (
    LocalOCRItem,
    LocalOCRProvider,
    local_ocr_tile_requirement,
)
from app.analysis.office_render import (
    OfficeRenderError,
    convert_office_to_pdf,
    office_pdf_page_count,
    render_pdf_page,
    render_pptx_slide,
)
from app.analysis.provider import VisionModelProvider
from app.analysis.processors.native import normalize_text


class VisualCheckpointError(RuntimeError):
    pass


class VisualCancellationRequested(RuntimeError):
    pass


def _adaptive_render_metadata(image: dict) -> dict:
    metadata: dict = {}
    for source_key, target_key in (
        ("adaptive_render_from_dpi", "ocr_adaptive_render_from_dpi"),
        ("adaptive_render_original_pixels", "ocr_adaptive_render_original_pixels"),
        ("adaptive_render_tile_requirement", "ocr_adaptive_render_tile_requirement"),
    ):
        if image.get(source_key) is not None:
            metadata[target_key] = int(image[source_key])
    return metadata


def _apply_local_success(
    unit: dict,
    match: tuple[LocalOCRItem, str, dict],
) -> tuple[int, bool]:
    item, text, image = match
    regions = list(item.regions)[:1000]
    metadata = unit.setdefault("metadata", {})
    for key in (
        "ocr_budget_reason",
        "ocr_manual_review_required",
        "ocr_review_candidate_text",
        "ocr_review_candidate_text_sha256",
        "ocr_review_candidate_method",
        "ocr_review_candidate_confidence",
        "ocr_review_candidate_languages",
        "ocr_review_candidate_regions",
        "ocr_review_candidate_region_count",
    ):
        metadata.pop(key, None)
    metadata.update({
        "ocr_method": item.method,
        "ocr_provider": "local",
        "ocr_confidence": item.confidence,
        "ocr_languages": item.languages,
        "ocr_regions": regions,
        "ocr_region_count": len(regions),
        "ocr_source_image_sha256": hashlib.sha256(
            bytes(image.get("payload") or b"")
        ).hexdigest(),
        **(
            {"ocr_render_dpi": int(image["render_dpi"])}
            if image.get("render_dpi") else {}
        ),
        **(
            {"ocr_render_method": str(image["render_method"])}
            if image.get("render_method") else {}
        ),
        **_adaptive_render_metadata(image),
    })
    unit["text"] = text
    visual_pending = str(unit.get("unit_type") or "") in {
        "image", "embedded_image", "slide_visual", "office_visual_page"
    }
    unit["status"] = "partial" if visual_pending else "processed"
    unit["warnings"] = [
        warning
        for warning in (unit.get("warnings") or [])
        if "OCR" not in warning
        and "ocr" not in warning
        and "vision" not in warning.lower()
    ]
    if visual_pending:
        metadata.update({
            "ocr_text_extracted": True,
            "visual_semantics_status": "pending_review_or_vision",
        })
        unit["warnings"].append(
            "Teks OCR lokal tersedia, tetapi makna visual gambar belum diverifikasi."
        )
    return len(regions), visual_pending


def _apply_local_pending(
    unit: dict,
    review_candidate: tuple[LocalOCRItem, str, dict] | None,
    source_image: dict,
) -> bool:
    metadata = unit.setdefault("metadata", {})
    if review_candidate:
        candidate_item, candidate_text, candidate_image = review_candidate
        candidate_regions = list(candidate_item.regions)[:1000]
        candidate_payload = bytes(candidate_image.get("payload") or b"")
        metadata.update({
            "ocr_review_candidate_text": candidate_text,
            "ocr_review_candidate_text_sha256": hashlib.sha256(
                candidate_text.encode("utf-8")
            ).hexdigest(),
            "ocr_review_candidate_method": candidate_item.method,
            "ocr_review_candidate_confidence": candidate_item.confidence,
            "ocr_review_candidate_languages": candidate_item.languages,
            "ocr_review_candidate_regions": candidate_regions,
            "ocr_review_candidate_region_count": len(candidate_regions),
            "ocr_source_image_sha256": hashlib.sha256(candidate_payload).hexdigest(),
            **(
                {"ocr_render_dpi": int(candidate_image["render_dpi"])}
                if candidate_image.get("render_dpi") else {}
            ),
            **(
                {"ocr_render_method": str(candidate_image["render_method"])}
                if candidate_image.get("render_method") else {}
            ),
            **_adaptive_render_metadata(candidate_image),
        })
        return True
    source_payload = bytes(source_image.get("payload") or b"")
    if source_payload:
        metadata.update({
            "ocr_manual_review_required": True,
            "ocr_source_image_sha256": hashlib.sha256(source_payload).hexdigest(),
            **(
                {"ocr_render_dpi": int(source_image["render_dpi"])}
                if source_image.get("render_dpi") else {}
            ),
            **(
                {"ocr_render_method": str(source_image["render_method"])}
                if source_image.get("render_method") else {}
            ),
            **_adaptive_render_metadata(source_image),
        })
    return False


class VisualOCREngine:
    name = "visual_ocr"
    version = PIPELINE_VERSION

    def __init__(
        self,
        provider: VisionModelProvider | None,
        local_provider: LocalOCRProvider | None = None,
    ):
        self.provider = provider
        self.local_provider = local_provider

    def run(
        self,
        identity: DocumentIdentity,
        payload: bytes,
        units: list[dict],
        *,
        max_units: int = 12,
        local_max_units: int = 100,
        local_min_confidence: float = 0.45,
        pdf_dpi: int = 144,
        pdf_retry_dpi: int = 288,
        pdf_retry_max_units: int = 8,
        local_unit_budget_seconds: int = 180,
        local_document_budget_seconds: int = 900,
        local_max_attempts_per_unit: int = 24,
        local_render_batch_units: int = 4,
        checkpoint_callback: Callable[[list[dict], dict], None] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        timeout_seconds: int = 90,
        office_rendering_enabled: bool = True,
        office_page_expansion_enabled: bool = True,
        office_render_max_pages: int = 24,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        document_budget_seconds = max(3, int(local_document_budget_seconds))
        document_deadline = started + document_budget_seconds
        updated_units = [{**unit, "metadata": dict(unit.get("metadata") or {})} for unit in units]
        warnings: list[str] = []
        office_pdf_payload: bytes | None = None
        office_pdf_total_pages = 0
        office_total_pages = 0
        office_pages_scheduled = 0
        office_pages_deferred = 0
        office_hidden_pages_excluded = 0

        def ensure_not_cancelled() -> None:
            if cancellation_check and cancellation_check():
                raise VisualCancellationRequested(
                    "Visual/OCR dihentikan pada batas aman antar-batch."
                )

        ensure_not_cancelled()
        should_expand_office = bool(
            office_page_expansion_enabled
            and identity.file_kind in {"docx", "xlsx"}
            and not any(
                str(unit.get("unit_type") or "").startswith("office_visual_")
                for unit in updated_units
            )
        )
        if should_expand_office and not office_rendering_enabled:
            updated_units.append({
                "unit_key": "office-visual-document",
                "unit_type": "office_visual_document",
                "ordinal": len(updated_units) + 1,
                "heading_path": [],
                "source_location": {"rendered_from": identity.file_kind},
                "text": "",
                "status": "ocr_required",
                "warnings": ["Full-document Office rendering dinonaktifkan; coverage ditahan."],
                "metadata": {
                    "requires_visual_verification": True,
                    "office_render_error": "Full-document Office rendering dinonaktifkan.",
                },
            })
            warnings.append("Full-document Office rendering dinonaktifkan; coverage ditahan.")
        elif should_expand_office:
            try:
                office_conversion_timeout = _remaining_budget_timeout(
                    document_deadline, timeout_seconds
                )
                if office_conversion_timeout is None:
                    raise OfficeRenderError(
                        "document_time_budget_exhausted sebelum konversi Office."
                    )
                office_pdf_payload = convert_office_to_pdf(
                    payload,
                    identity.file_kind,
                    timeout_seconds=office_conversion_timeout,
                )
                office_pdf_total_pages = office_pdf_page_count(office_pdf_payload)
                if office_pdf_total_pages < 1:
                    raise OfficeRenderError("Dokumen Office tidak menghasilkan halaman visual.")
                page_specs: list[dict] = [
                    {"rendered_page": page}
                    for page in range(1, office_pdf_total_pages + 1)
                ]
                if identity.file_kind == "xlsx":
                    all_sheets = [
                        {
                            "sheet": (unit.get("source_location") or {}).get("sheet"),
                            "sheet_index": (unit.get("source_location") or {}).get("sheet_index"),
                            "hidden": bool((unit.get("source_location") or {}).get("hidden")),
                        }
                        for unit in updated_units
                        if str(unit.get("unit_type") or "") == "sheet"
                    ]
                    visible_sheets = [sheet for sheet in all_sheets if not sheet["hidden"]]
                    if all_sheets and len(all_sheets) == office_pdf_total_pages:
                        page_specs = [
                            {
                                "rendered_page": page,
                                "sheet": sheet["sheet"],
                                "sheet_index": sheet["sheet_index"],
                            }
                            for page, sheet in enumerate(all_sheets, start=1)
                            if not sheet["hidden"]
                        ]
                        office_hidden_pages_excluded = len(all_sheets) - len(page_specs)
                    elif visible_sheets and len(visible_sheets) == office_pdf_total_pages:
                        page_specs = [
                            {
                                "rendered_page": page,
                                "sheet": sheet["sheet"],
                                "sheet_index": sheet["sheet_index"],
                            }
                            for page, sheet in enumerate(visible_sheets, start=1)
                        ]
                    else:
                        warnings.append(
                            "Jumlah snapshot Calc tidak cocok dengan inventaris sheet; "
                            "locator nama sheet dan filtering hidden ditahan."
                        )
                office_total_pages = len(page_specs)
                if office_total_pages < 1:
                    raise OfficeRenderError(
                        "Dokumen Office tidak memiliki halaman visual visible yang dapat diroute."
                    )
                page_budget = max(1, min(1_000, int(office_render_max_pages)))
                office_pages_scheduled = min(office_total_pages, page_budget)
                office_pages_deferred = max(0, office_total_pages - office_pages_scheduled)
                for visual_index, page_spec in enumerate(page_specs, start=1):
                    page = int(page_spec["rendered_page"])
                    selected = visual_index <= office_pages_scheduled
                    sheet_location = {
                        key: page_spec[key]
                        for key in ("sheet", "sheet_index")
                        if page_spec.get(key) is not None
                    }
                    updated_units.append({
                        "unit_key": f"office-visual-page-{page}",
                        "unit_type": "office_visual_page",
                        "ordinal": len(updated_units) + 1,
                        "heading_path": [],
                        "source_location": {
                            "rendered_page": page,
                            "rendered_from": identity.file_kind,
                            **sheet_location,
                        },
                        "text": "",
                        "status": "ocr_required" if selected else "pending",
                        "warnings": (
                            ["Halaman visual Office membutuhkan OCR dan verifikasi layout."]
                            if selected
                            else ["Halaman visual Office ditunda oleh page budget."]
                        ),
                        "metadata": {
                            "requires_visual_verification": True,
                            "render_method": "libreoffice_ooxml_to_pdf_to_png_v2",
                            "office_total_rendered_pages": office_total_pages,
                            "office_pdf_total_pages": office_pdf_total_pages,
                            "office_hidden_pages_excluded": office_hidden_pages_excluded,
                            "office_page_budget": page_budget,
                            "single_page_sheet": identity.file_kind == "xlsx",
                        },
                    })
                if office_pages_deferred:
                    warnings.append(
                        f"Full-document Office rendering menjadwalkan {office_pages_scheduled} "
                        f"dari {office_total_pages} halaman; {office_pages_deferred} halaman "
                        "tetap pending karena page budget."
                    )
            except OfficeRenderError as exc:
                updated_units.append({
                    "unit_key": "office-visual-document",
                    "unit_type": "office_visual_document",
                    "ordinal": len(updated_units) + 1,
                    "heading_path": [],
                    "source_location": {"rendered_from": identity.file_kind},
                    "text": "",
                    "status": "ocr_required",
                    "warnings": ["Full-document Office rendering gagal; coverage ditahan."],
                    "metadata": {
                        "requires_visual_verification": True,
                        "office_render_error": str(exc),
                    },
                })
                warnings.append(f"Full-document Office rendering gagal: {exc}"[:500])
        candidates = [unit for unit in updated_units if unit.get("status") == "ocr_required"]
        if not candidates:
            return updated_units, self._result(
                identity,
                started,
                EngineStatus.SKIPPED,
                0,
                0,
                warnings,
                "Tidak ada unit yang membutuhkan OCR.",
                output_extra={
                    "office_pdf_total_pages": office_pdf_total_pages,
                    "office_total_pages": office_total_pages,
                    "office_pages_scheduled": office_pages_scheduled,
                    "office_pages_deferred": office_pages_deferred,
                    "office_hidden_pages_excluded": office_hidden_pages_excluded,
                },
            )
        if not self.provider and not self.local_provider:
            warning = "Local OCR dan vision provider tidak tersedia; unit OCR tetap diblokir."
            return updated_units, self._result(
                identity, started, EngineStatus.PARTIAL, len(candidates), 0, [warning], warning
            )

        local_limit = max(1, int(local_max_units)) if self.local_provider else 0
        vision_limit = max(1, int(max_units)) if self.provider else 0
        render_limit = max(local_limit, vision_limit)
        selected = candidates[:render_limit]
        if len(selected) < len(candidates):
            warnings.append(
                f"OCR dibatasi pada {len(selected)} dari {len(candidates)} unit; "
                "sisanya tetap ocr_required."
            )
        render_batch_units = max(1, min(100, int(local_render_batch_units)))
        initial_selected = (
            selected[:render_batch_units] if self.local_provider else selected
        )
        try:
            ensure_not_cancelled()
            images = self._prepare_images(
                identity,
                payload,
                initial_selected,
                pdf_dpi,
                timeout_seconds,
                office_rendering_enabled,
                office_pdf_payload,
                document_deadline,
            )
        except VisualCancellationRequested:
            raise
        except Exception as exc:
            warning = f"Renderer OCR tidak tersedia atau gagal: {exc}"
            return updated_units, self._result(
                identity, started, EngineStatus.PARTIAL, len(candidates), 0, [*warnings, warning], warning
            )
        for unit in selected:
            render_error = str((unit.get("metadata") or {}).get("office_render_error") or "")
            if render_error:
                warnings.append(f"{unit.get('unit_key')}: {render_error}"[:500])
        if not images:
            warning = "Tidak ada aset visual yang dapat diproses oleh Local/Visual OCR Engine."
            return updated_units, self._result(
                identity, started, EngineStatus.PARTIAL, len(candidates), 0, [*warnings, warning], warning
            )

        images_by_key = {str(image["unit_key"]): image for image in images}
        local_by_key: dict[str, tuple[LocalOCRItem, str, dict]] = {}
        local_best_by_key: dict[str, tuple[LocalOCRItem, str, dict]] = {}
        pdf_retry_required = 0
        pdf_retry_processed = 0
        pdf_retry_deferred = 0
        office_adaptive_low_dpi_keys: set[str] = set()
        local_attempt_count = 0
        local_timeout_count = 0
        local_budget_exhaustion_reasons: dict[str, str] = {}
        min_confidence = max(0.0, min(1.0, float(local_min_confidence)))

        def register_local_response(
            response,
            allowed_keys: set[str],
            source_images: list[dict],
        ) -> None:
            nonlocal local_attempt_count, local_timeout_count
            warnings.extend(response.warnings)
            response_metrics = getattr(response, "metrics", {}) or {}
            local_attempt_count += int(response_metrics.get("attempt_count") or 0)
            local_timeout_count += int(response_metrics.get("timeout_count") or 0)
            local_budget_exhaustion_reasons.update(
                {
                    str(key): str(value)
                    for key, value in (
                        response_metrics.get("budget_exhaustion_reasons") or {}
                    ).items()
                }
            )
            source_images_by_key = {
                str(image.get("unit_key") or ""): image for image in source_images
            }
            for item in response.items:
                if item.unit_key not in allowed_keys:
                    warnings.append(
                        f"Local OCR mengembalikan unit_key tidak dikenal: {item.unit_key}."
                    )
                    continue
                text = normalize_text(item.text)
                if not text:
                    warnings.append(f"Local OCR untuk {item.unit_key} tidak berisi teks valid.")
                    continue
                source_image = source_images_by_key.get(item.unit_key) or {}
                if not source_image.get("payload"):
                    warnings.append(
                        f"Aset sumber Local OCR untuk {item.unit_key} tidak tersedia."
                    )
                    continue
                current = local_best_by_key.get(item.unit_key)
                if current is None or (float(item.confidence), len(text)) > (
                    float(current[0].confidence), len(current[1])
                ):
                    local_best_by_key[item.unit_key] = (item, text, source_image)

        def accept_confident_local(keys: set[str]) -> None:
            for key in keys:
                match = local_best_by_key.get(key)
                if match and float(match[0].confidence) >= min_confidence:
                    local_by_key[key] = match

        def materialize_local_keys(keys: set[str]) -> None:
            if not keys:
                return
            for unit in updated_units:
                unit_key = str(unit.get("unit_key") or "")
                if unit_key not in keys:
                    continue
                match = local_by_key.get(unit_key)
                if match:
                    _apply_local_success(unit, match)
                else:
                    _apply_local_pending(
                        unit,
                        local_best_by_key.get(unit_key),
                        images_by_key.get(unit_key) or {},
                    )

        def emit_checkpoint(keys: set[str], phase: str) -> None:
            if not checkpoint_callback or not keys:
                return
            materialize_local_keys(keys)
            try:
                checkpoint_callback(updated_units, {
                    "phase": phase,
                    "unit_keys": sorted(keys),
                    "attempt_count": local_attempt_count,
                    "timeout_count": local_timeout_count,
                })
            except Exception as exc:
                raise VisualCheckpointError(
                    f"Durable Visual/OCR checkpoint gagal: {exc}"
                ) from exc

        if self.local_provider:
            try:
                local_images = images[:local_limit]
                local_images, adaptive_keys, adaptive_warnings = (
                    self._adapt_office_images_to_local_budget(
                        identity,
                        selected,
                        local_images,
                        office_pdf_payload,
                        timeout_seconds,
                        document_deadline,
                    )
                )
                office_adaptive_low_dpi_keys.update(adaptive_keys)
                warnings.extend(adaptive_warnings)
                for image in local_images:
                    image["_ocr_document_deadline_monotonic"] = document_deadline
                    images_by_key[str(image["unit_key"])] = image
                local_keys = {str(image["unit_key"]) for image in local_images}
                register_local_response(
                    self.local_provider.analyze_images(local_images),
                    local_keys,
                    local_images,
                )
                accept_confident_local(local_keys)
                emit_checkpoint(local_keys, "base")

                for batch_start in range(
                    render_batch_units, len(selected), render_batch_units
                ):
                    ensure_not_cancelled()
                    batch_units = selected[
                        batch_start:batch_start + render_batch_units
                    ]
                    if document_deadline - perf_counter() <= 0.25:
                        for unit in selected[batch_start:]:
                            unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                                "document_time_budget_exhausted"
                            )
                        break
                    batch_images = self._prepare_images(
                        identity,
                        payload,
                        batch_units,
                        pdf_dpi,
                        timeout_seconds,
                        office_rendering_enabled,
                        office_pdf_payload,
                        document_deadline,
                    )
                    batch_images, adaptive_keys, adaptive_warnings = (
                        self._adapt_office_images_to_local_budget(
                            identity,
                            batch_units,
                            batch_images,
                            office_pdf_payload,
                            timeout_seconds,
                            document_deadline,
                        )
                    )
                    office_adaptive_low_dpi_keys.update(adaptive_keys)
                    warnings.extend(adaptive_warnings)
                    for image in batch_images:
                        image["_ocr_document_deadline_monotonic"] = document_deadline
                        images_by_key[str(image["unit_key"])] = image
                    batch_keys = {
                        str(image["unit_key"]) for image in batch_images
                    }
                    images.extend(batch_images)
                    local_keys.update(batch_keys)
                    register_local_response(
                        self.local_provider.analyze_images(batch_images),
                        batch_keys,
                        batch_images,
                    )
                    accept_confident_local(batch_keys)
                    emit_checkpoint(batch_keys, "base")

                effective_retry_dpi = max(72, min(300, int(pdf_retry_dpi)))
                effective_base_dpi = max(72, min(300, int(pdf_dpi)))
                retry_keys = {
                    key for key in local_keys
                    if key not in local_by_key
                }
                if (
                    identity.file_kind in {"pdf", "docx", "xlsx"}
                    and retry_keys
                    and effective_retry_dpi > effective_base_dpi
                ):
                    ensure_not_cancelled()
                    all_retry_units = [
                        unit for unit in selected
                        if str(unit.get("unit_key") or "") in retry_keys
                        and (
                            identity.file_kind == "pdf"
                            or str(unit.get("unit_type") or "") == "office_visual_page"
                        )
                    ]
                    retry_limit = max(0, int(pdf_retry_max_units))
                    retry_units = all_retry_units[:retry_limit]
                    if document_deadline - perf_counter() <= 0.25:
                        retry_units = []
                        for unit in all_retry_units:
                            unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                                "document_time_budget_exhausted"
                            )
                        warnings.append(
                            "Retry OCR resolusi tinggi tidak dimulai: "
                            "document_time_budget_exhausted."
                        )
                    pdf_retry_required = len(retry_units)
                    pdf_retry_deferred = len(all_retry_units) - len(retry_units)
                    if pdf_retry_deferred:
                        warnings.append(
                            f"Retry OCR PDF resolusi tinggi dibatasi pada {len(retry_units)} "
                            f"unit; {pdf_retry_deferred} unit ditunda."
                        )
                    try:
                        retry_images = self._prepare_images(
                            identity,
                            payload,
                            retry_units,
                            effective_retry_dpi,
                            timeout_seconds,
                            office_rendering_enabled,
                            office_pdf_payload,
                            document_deadline,
                        )
                        retry_images, adaptive_keys, adaptive_warnings = (
                            self._adapt_office_images_to_local_budget(
                                identity,
                                retry_units,
                                retry_images,
                                office_pdf_payload,
                                timeout_seconds,
                                document_deadline,
                            )
                        )
                        office_adaptive_low_dpi_keys.update(adaptive_keys)
                        warnings.extend(adaptive_warnings)
                        for image in retry_images:
                            image["_ocr_document_deadline_monotonic"] = document_deadline
                        retry_image_keys = {
                            str(image["unit_key"]) for image in retry_images
                        }
                        register_local_response(
                            self.local_provider.analyze_images(retry_images),
                            retry_image_keys,
                            retry_images,
                        )
                        for image in retry_images:
                            images_by_key[str(image["unit_key"])] = image
                        accept_confident_local(retry_image_keys)
                        emit_checkpoint(retry_image_keys, "retry")
                        pdf_retry_processed = sum(
                            1 for key in retry_image_keys if key in local_by_key
                        )
                    except VisualCheckpointError:
                        raise
                    except Exception as exc:
                        warnings.append(f"Retry OCR PDF resolusi tinggi gagal: {exc}"[:500])

                for key, (item, _text, _image) in local_best_by_key.items():
                    if key not in local_by_key:
                        warnings.append(
                            f"Local OCR {key} ditolak karena confidence "
                            f"{float(item.confidence):.4f} < {min_confidence:.4f}."
                        )
            except (VisualCheckpointError, VisualCancellationRequested):
                raise
            except Exception as exc:
                warnings.append(f"Local OCR provider gagal: {exc}"[:500])

        pending_images = [
            images_by_key[str(image["unit_key"])] for image in images
            if str(image["unit_key"]) not in local_by_key
        ]
        vision_by_key = {}
        if self.provider and pending_images:
            try:
                ensure_not_cancelled()
                response = self.provider.analyze_images(pending_images[:vision_limit])
                warnings.extend(response.warnings)
                pending_keys = {
                    str(image["unit_key"]) for image in pending_images[:vision_limit]
                }
                for item in response.items:
                    if item.unit_key not in pending_keys:
                        warnings.append(
                            f"Vision OCR mengembalikan unit_key tidak dikenal: {item.unit_key}."
                        )
                        continue
                    text = normalize_text(item.ocr_text)
                    if not text:
                        warnings.append(f"Vision OCR untuk {item.unit_key} tidak berisi teks valid.")
                        continue
                    vision_by_key[item.unit_key] = (item, text)
            except VisualCancellationRequested:
                raise
            except Exception as exc:
                warnings.append(f"Vision/OCR provider gagal: {exc}"[:500])

        processed = 0
        local_processed = 0
        local_tiled_processed = 0
        vision_processed = 0
        region_count = 0
        visual_semantics_pending = 0
        review_candidates = 0

        for unit in updated_units:
            unit_key = str(unit.get("unit_key") or "")
            if unit_key in local_budget_exhaustion_reasons:
                unit["metadata"]["ocr_budget_reason"] = (
                    local_budget_exhaustion_reasons[unit_key]
                )
            local_match = local_by_key.get(unit_key)
            vision_match = vision_by_key.get(unit_key)
            if not local_match and not vision_match:
                review_candidates += int(_apply_local_pending(
                    unit,
                    local_best_by_key.get(unit_key),
                    images_by_key.get(unit_key) or {},
                ))
                continue
            if local_match:
                region_delta, local_text_only_visual = _apply_local_success(
                    unit, local_match
                )
                local_processed += 1
                local_tiled_processed += int(
                    "tiled" in str(local_match[0].method or "").lower()
                )
                region_count += region_delta
                visual_semantics_pending += int(local_text_only_visual)
                processed += 1
                continue
            else:
                item, text = vision_match
                image = images_by_key.get(unit_key) or {}
                unit["metadata"].update({
                    "ocr_method": "compatible_chat_vision_v1",
                    "ocr_provider": "external_vision",
                    "ocr_confidence": item.confidence,
                    "visual_observations": item.observations,
                    **(
                        {"ocr_render_dpi": int(image["render_dpi"])}
                        if image.get("render_dpi") else {}
                    ),
                    **(
                        {"ocr_render_method": str(image["render_method"])}
                        if image.get("render_method") else {}
                    ),
                    **_adaptive_render_metadata(image),
                })
                vision_processed += 1
            unit["text"] = text
            unit["status"] = "processed"
            unit["warnings"] = [
                warning
                for warning in (unit.get("warnings") or [])
                if "OCR" not in warning
                and "ocr" not in warning
                and "vision" not in warning.lower()
            ]
            processed += 1

        remaining = len(candidates) - processed
        if remaining:
            warnings.append(f"{remaining} unit masih membutuhkan OCR/vision.")
        if visual_semantics_pending:
            warnings.append(
                f"{visual_semantics_pending} unit mempunyai teks OCR tetapi makna visual masih pending."
            )
        for unit in updated_units:
            unit_key = str(unit.get("unit_key") or "")
            reason = str((unit.get("metadata") or {}).get("ocr_budget_reason") or "")
            if unit_key and reason:
                local_budget_exhaustion_reasons[unit_key] = reason
        status = (
            EngineStatus.COMPLETED
            if remaining == 0 and visual_semantics_pending == 0
            else EngineStatus.PARTIAL
        )
        return updated_units, self._result(
            identity,
            started,
            status,
            len(candidates),
            processed,
            warnings,
            None,
            output_extra={
                "local_ocr_processed": local_processed,
                "local_ocr_tiled_processed": local_tiled_processed,
                "external_vision_processed": vision_processed,
                "ocr_region_count": region_count,
                "visual_semantics_pending": visual_semantics_pending,
                "ocr_review_candidates": review_candidates,
                "pdf_retry_required": pdf_retry_required,
                "pdf_retry_processed": pdf_retry_processed,
                "pdf_retry_deferred": pdf_retry_deferred,
                "pdf_render_dpi": max(72, min(300, int(pdf_dpi))),
                "pdf_retry_dpi": max(72, min(300, int(pdf_retry_dpi))),
                "pdf_retry_max_units": max(0, int(pdf_retry_max_units)),
                "local_ocr_attempt_count": local_attempt_count,
                "local_ocr_timeout_count": local_timeout_count,
                "local_ocr_budget_exhausted_units": len(
                    local_budget_exhaustion_reasons
                ),
                "local_ocr_budget_exhaustion_reasons": dict(
                    sorted(local_budget_exhaustion_reasons.items())
                ),
                "local_ocr_unit_budget_seconds": max(
                    3, int(local_unit_budget_seconds)
                ),
                "local_ocr_document_budget_seconds": document_budget_seconds,
                "local_ocr_max_attempts_per_unit": max(
                    1, int(local_max_attempts_per_unit)
                ),
                "local_ocr_render_batch_units": render_batch_units,
                "local_ocr_document_elapsed_ms": round(
                    (perf_counter() - started) * 1000
                ),
                "office_adaptive_low_dpi_pages": len(office_adaptive_low_dpi_keys),
                "local_provider": getattr(self.local_provider, "name", None),
                "external_fallback_available": self.provider is not None,
                "office_pdf_total_pages": office_pdf_total_pages,
                "office_total_pages": office_total_pages,
                "office_pages_scheduled": office_pages_scheduled,
                "office_pages_deferred": office_pages_deferred,
                "office_hidden_pages_excluded": office_hidden_pages_excluded,
            },
        )

    def _prepare_images(
        self,
        identity: DocumentIdentity,
        payload: bytes,
        candidates: list[dict],
        dpi: int,
        timeout_seconds: int,
        office_rendering_enabled: bool = True,
        office_pdf_payload: bytes | None = None,
        document_deadline: float | None = None,
    ) -> list[dict]:
        if identity.file_kind == "image":
            return [
                {
                    "unit_key": candidates[0]["unit_key"],
                    "mime_type": identity.content_type or "image/png",
                    "payload": payload,
                }
            ]
        if identity.file_kind in {"docx", "xlsx", "pptx"}:
            images = []
            with ZipFile(BytesIO(payload)) as archive:
                names = set(archive.namelist())
                for unit in candidates:
                    render_timeout = _remaining_budget_timeout(
                        document_deadline, timeout_seconds
                    )
                    if render_timeout is None:
                        unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                            "document_time_budget_exhausted"
                        )
                        continue
                    location = unit.get("source_location") or {}
                    if (
                        identity.file_kind in {"docx", "xlsx"}
                        and str(unit.get("unit_type") or "") == "office_visual_page"
                    ):
                        if not office_pdf_payload:
                            unit.setdefault("metadata", {})["office_render_error"] = (
                                "PDF hasil konversi Office tidak tersedia."
                            )
                            continue
                        page = int(location.get("rendered_page") or 0)
                        try:
                            rendered = render_pdf_page(
                                office_pdf_payload,
                                page,
                                dpi=dpi,
                                timeout_seconds=render_timeout,
                            )
                        except OfficeRenderError as exc:
                            unit.setdefault("metadata", {})["office_render_error"] = str(exc)
                            continue
                        images.append({
                            "unit_key": unit["unit_key"],
                            "mime_type": "image/png",
                            "payload": rendered,
                            "render_dpi": max(72, min(300, int(dpi))),
                            "render_method": "libreoffice_ooxml_to_pdf_to_png_v2",
                        })
                        continue
                    if (
                        identity.file_kind == "pptx"
                        and str(unit.get("unit_type") or "") == "slide_visual"
                    ):
                        if not office_rendering_enabled:
                            unit.setdefault("metadata", {})["office_render_error"] = (
                                "Full-slide rendering dinonaktifkan; unit tetap fail-closed."
                            )
                            continue
                        slide = int(location.get("slide") or 0)
                        try:
                            rendered = render_pptx_slide(
                                payload,
                                slide,
                                dpi=dpi,
                                timeout_seconds=render_timeout,
                            )
                        except OfficeRenderError as exc:
                            unit.setdefault("metadata", {})["office_render_error"] = str(exc)
                            continue
                        images.append({
                            "unit_key": unit["unit_key"],
                            "mime_type": "image/png",
                            "payload": rendered,
                            "render_dpi": max(72, min(300, int(dpi))),
                            "render_method": "libreoffice_impress_to_pdf_to_png_v1",
                        })
                        continue
                    part = str(location.get("part") or "")
                    if not part or part not in names:
                        continue
                    images.append(
                        {
                            "unit_key": unit["unit_key"],
                            "mime_type": _image_mime_type(part),
                            "payload": archive.read(part),
                        }
                    )
            return images
        if identity.file_kind != "pdf":
            return []
        renderer = shutil.which("pdftoppm")
        if not renderer:
            raise RuntimeError("pdftoppm tidak ditemukan")
        rendered: list[dict] = []
        with TemporaryDirectory(prefix="spip-ocr-") as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(payload)
            for unit in candidates:
                render_timeout = _remaining_budget_timeout(
                    document_deadline, timeout_seconds
                )
                if render_timeout is None:
                    unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                        "document_time_budget_exhausted"
                    )
                    continue
                page = int((unit.get("source_location") or {}).get("page") or 0)
                if page < 1:
                    continue
                prefix = root / f"page-{page}"
                try:
                    subprocess.run(
                        [
                            renderer,
                            "-f",
                            str(page),
                            "-l",
                            str(page),
                            "-singlefile",
                            "-png",
                            "-r",
                            str(max(72, min(300, dpi))),
                            str(source),
                            str(prefix),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=render_timeout,
                    )
                except subprocess.TimeoutExpired:
                    unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                        "render_attempt_timeout"
                    )
                    continue
                except (subprocess.CalledProcessError, OSError) as exc:
                    unit.setdefault("metadata", {})["ocr_render_error"] = str(exc)[:500]
                    continue
                image_path = prefix.with_suffix(".png")
                if image_path.is_file():
                    rendered.append(
                        {
                            "unit_key": unit["unit_key"],
                            "mime_type": "image/png",
                            "payload": image_path.read_bytes(),
                            "render_dpi": max(72, min(300, int(dpi))),
                        }
                    )
        return rendered

    def _adapt_office_images_to_local_budget(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        images: list[dict],
        office_pdf_payload: bytes | None,
        timeout_seconds: int,
        document_deadline: float | None = None,
    ) -> tuple[list[dict], set[str], list[str]]:
        if (
            identity.file_kind not in {"docx", "xlsx"}
            or not office_pdf_payload
            or not self.local_provider
        ):
            return images, set(), []
        max_pixels = int(getattr(self.local_provider, "max_image_pixels", 0) or 0)
        max_tiles = int(getattr(self.local_provider, "max_tiles", 0) or 0)
        if max_pixels < 1 or max_tiles < 1:
            return images, set(), []
        units_by_key = {
            str(unit.get("unit_key") or ""): unit
            for unit in units
        }
        adapted: list[dict] = []
        adapted_keys: set[str] = set()
        warnings: list[str] = []
        for image in images:
            unit_key = str(image.get("unit_key") or "")
            unit = units_by_key.get(unit_key) or {}
            if str(unit.get("unit_type") or "") != "office_visual_page":
                adapted.append(image)
                continue
            try:
                width, height, required_tiles = local_ocr_tile_requirement(
                    bytes(image.get("payload") or b""),
                    max_pixels,
                )
            except ValueError:
                adapted.append(image)
                continue
            if required_tiles <= max_tiles:
                adapted.append(image)
                continue
            page = int((unit.get("source_location") or {}).get("rendered_page") or 0)
            source_dpi = max(72, min(300, int(image.get("render_dpi") or 144)))
            candidate_dpi = max(
                72,
                min(
                    source_dpi - 1,
                    int(
                        math.floor(
                            source_dpi
                            * math.sqrt(max_tiles / max(1, required_tiles))
                            * 0.9
                        )
                    ),
                ),
            )
            fitted: dict | None = None
            last_requirement = required_tiles
            while page > 0 and candidate_dpi >= 72:
                render_timeout = _remaining_budget_timeout(
                    document_deadline, timeout_seconds
                )
                if render_timeout is None:
                    unit.setdefault("metadata", {})["ocr_budget_reason"] = (
                        "document_time_budget_exhausted"
                    )
                    warnings.append(
                        f"Adaptive render {unit_key} ditunda: "
                        "document_time_budget_exhausted."
                    )
                    break
                try:
                    rendered = render_pdf_page(
                        office_pdf_payload,
                        page,
                        dpi=candidate_dpi,
                        timeout_seconds=render_timeout,
                    )
                    _, _, last_requirement = local_ocr_tile_requirement(
                        rendered,
                        max_pixels,
                    )
                except (OfficeRenderError, ValueError) as exc:
                    warnings.append(
                        f"Adaptive render {unit_key} gagal pada {candidate_dpi} DPI: {exc}"[:500]
                    )
                    break
                if last_requirement <= max_tiles:
                    fitted = {
                        **image,
                        "payload": rendered,
                        "render_dpi": candidate_dpi,
                        "adaptive_render_from_dpi": source_dpi,
                        "adaptive_render_original_pixels": width * height,
                        "adaptive_render_tile_requirement": last_requirement,
                    }
                    break
                if candidate_dpi == 72:
                    break
                candidate_dpi = max(
                    72,
                    min(
                        candidate_dpi - 1,
                        int(
                            math.floor(
                                candidate_dpi
                                * math.sqrt(max_tiles / max(1, last_requirement))
                                * 0.9
                            )
                        ),
                    ),
                )
            if fitted:
                adapted.append(fitted)
                adapted_keys.add(unit_key)
                warnings.append(
                    f"Halaman Office {unit_key} dirender adaptif {source_dpi}→"
                    f"{fitted['render_dpi']} DPI agar {required_tiles} tile menjadi "
                    f"{fitted['adaptive_render_tile_requirement']} tanpa menaikkan budget."
                )
            else:
                adapted.append(image)
                warnings.append(
                    f"Halaman Office {unit_key} tetap membutuhkan {last_requirement} tile; "
                    f"budget lokal {max_tiles} dipertahankan."
                )
        return adapted, adapted_keys, warnings

    def _result(
        self,
        identity: DocumentIdentity,
        started: float,
        status: EngineStatus,
        required: int,
        processed: int,
        warnings: list[str],
        message: str | None,
        output_extra: dict | None = None,
    ) -> EngineResult:
        return EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=status,
            input_checksum=identity.sha256,
            input_refs=[f"identity:{identity.sha256}"],
            coverage={"required": required, "processed": processed, "failed": 0, "pending": required - processed},
            warnings=warnings[:20],
            metrics={"duration_ms": max(0, round((perf_counter() - started) * 1000))},
            output={
                "ocr_required": required,
                "ocr_processed": processed,
                **(output_extra or {}),
            },
            error_message=message if status == EngineStatus.FAILED else None,
        ).finish()


def _remaining_budget_timeout(
    document_deadline: float | None,
    requested_seconds: int | float,
) -> float | None:
    requested = max(0.25, float(requested_seconds))
    if document_deadline is None:
        return requested
    remaining = float(document_deadline) - perf_counter()
    if remaining <= 0.25:
        return None
    return max(0.25, min(requested, remaining))


def _image_mime_type(path: str) -> str:
    extension = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
        ".tiff": "image/tiff", ".webp": "image/webp",
    }.get(extension, "application/octet-stream")
