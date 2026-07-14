from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import unicodedata
from zipfile import BadZipFile, ZipFile, ZipInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.document_map import CoverageEngine, NativeParsingEngine  # noqa: E402
from app.analysis.intake import FileIntakeSecurityEngine  # noqa: E402
from app.analysis.local_ocr import configured_local_ocr_provider, local_ocr_runtime_status  # noqa: E402
from app.analysis.template_detection import TemplateCompletenessEngine  # noqa: E402
from app.analysis.vision import VisualOCREngine  # noqa: E402
from app.config import Settings  # noqa: E402


DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_RATIO = 200.0
CHUNK_SIZE = 1024 * 1024
SENSITIVE_SIGNAL_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "indonesian_phone": re.compile(r"(?<!\d)(?:\+62|62|0)8\d{7,12}(?!\d)"),
    "possible_nik": re.compile(r"(?<!\d)\d{16}(?!\d)"),
}


class UnsafeArchiveError(ValueError):
    pass


def _json_dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jsonl_dump(path: Path, items: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _jsonl_load(path: Path) -> list[dict]:
    items: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            value = line.strip()
            if not value:
                continue
            try:
                item = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL tidak valid pada {path}:{line_number}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"Record JSONL bukan object pada {path}:{line_number}.")
            items.append(item)
    return items


def _jsonl_append(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _normalized_member_name(info: ZipInfo) -> str:
    raw = info.filename
    if not raw or "\x00" in raw or any(ord(char) < 32 for char in raw):
        raise UnsafeArchiveError("Archive memiliki nama entry kosong atau karakter kontrol.")
    normalized = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise UnsafeArchiveError(f"Path absolut tidak diizinkan: {raw!r}")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeArchiveError(f"Path tidak aman: {raw!r}")
    if len(normalized) > 4096:
        raise UnsafeArchiveError(f"Path terlalu panjang: {raw[:120]!r}")
    return path.as_posix()


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def inspect_archive(
    archive_path: Path,
    *,
    max_entries: int,
    max_total_bytes: int,
    max_file_bytes: int,
    max_ratio: float,
) -> tuple[dict, list[tuple[ZipInfo, str]]]:
    archive_sha256 = hashlib.sha256()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            archive_sha256.update(chunk)

    errors: list[str] = []
    warnings: list[str] = []
    checked: list[tuple[ZipInfo, str]] = []
    normalized_seen: dict[str, str] = {}
    casefold_seen: dict[str, str] = {}
    total_uncompressed = 0
    total_compressed = 0
    encrypted_entries = 0
    symlink_entries = 0
    high_ratio_entries = 0
    extension_counts: Counter[str] = Counter()

    try:
        with ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                errors.append(f"Jumlah entry {len(entries)} melebihi batas {max_entries}.")
            for info in entries:
                try:
                    normalized = _normalized_member_name(info)
                except UnsafeArchiveError as exc:
                    errors.append(str(exc))
                    continue
                collision_key = unicodedata.normalize("NFC", normalized)
                folded_key = collision_key.casefold()
                if collision_key in normalized_seen:
                    errors.append(
                        f"Collision path normalisasi: {normalized_seen[collision_key]!r} dan {info.filename!r}."
                    )
                if folded_key in casefold_seen and casefold_seen[folded_key] != collision_key:
                    errors.append(
                        f"Collision path case-insensitive: {casefold_seen[folded_key]!r} dan {normalized!r}."
                    )
                normalized_seen[collision_key] = info.filename
                casefold_seen[folded_key] = collision_key
                if info.flag_bits & 0x1:
                    encrypted_entries += 1
                    errors.append(f"Entry terenkripsi tidak dapat diaudit: {normalized!r}.")
                if _is_symlink(info):
                    symlink_entries += 1
                    errors.append(f"Symlink tidak diizinkan dalam corpus: {normalized!r}.")
                total_uncompressed += max(0, int(info.file_size))
                total_compressed += max(0, int(info.compress_size))
                if info.file_size > max_file_bytes:
                    errors.append(
                        f"Entry {normalized!r} berukuran {info.file_size} byte, melebihi batas {max_file_bytes}."
                    )
                ratio = info.file_size / max(1, info.compress_size)
                if info.file_size and ratio > max_ratio:
                    high_ratio_entries += 1
                    errors.append(
                        f"Rasio kompresi entry {normalized!r} tidak aman ({ratio:.2f} > {max_ratio:.2f})."
                    )
                if not info.is_dir():
                    extension_counts[Path(normalized).suffix.lower() or "[none]"] += 1
                checked.append((info, normalized))
    except BadZipFile as exc:
        errors.append(f"ZIP tidak valid: {exc}")

    if total_uncompressed > max_total_bytes:
        errors.append(
            f"Ukuran total dekompresi {total_uncompressed} byte melebihi batas {max_total_bytes}."
        )
    overall_ratio = total_uncompressed / max(1, archive_path.stat().st_size)
    if overall_ratio > max_ratio:
        errors.append(f"Rasio kompresi total tidak aman ({overall_ratio:.2f} > {max_ratio:.2f}).")
    if not checked:
        errors.append("Archive tidak memiliki entry yang dapat diperiksa.")
    if any(info.is_dir() and info.file_size for info, _ in checked):
        warnings.append("Ada directory entry dengan ukuran non-zero.")

    report = {
        "archive_path": str(archive_path),
        "archive_sha256": archive_sha256.hexdigest(),
        "archive_size_bytes": archive_path.stat().st_size,
        "entry_count": len(checked),
        "file_count": sum(not info.is_dir() for info, _ in checked),
        "directory_count": sum(info.is_dir() for info, _ in checked),
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_entry_bytes": total_compressed,
        "overall_compression_ratio": round(overall_ratio, 3),
        "encrypted_entry_count": encrypted_entries,
        "symlink_entry_count": symlink_entries,
        "high_ratio_entry_count": high_ratio_entries,
        "extension_counts": dict(sorted(extension_counts.items())),
        "safe_to_extract": not errors,
        "errors": errors,
        "warnings": warnings,
        "limits": {
            "max_entries": max_entries,
            "max_total_uncompressed_bytes": max_total_bytes,
            "max_file_bytes": max_file_bytes,
            "max_compression_ratio": max_ratio,
        },
    }
    return report, checked


def safe_extract(archive_path: Path, extract_root: Path, checked: list[tuple[ZipInfo, str]]) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    root = extract_root.resolve()
    with ZipFile(archive_path) as archive:
        for info, normalized in checked:
            destination = (root / normalized).resolve()
            if destination != root and root not in destination.parents:
                raise UnsafeArchiveError(f"Entry keluar dari extraction root: {normalized!r}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise UnsafeArchiveError(f"Tujuan ekstraksi sudah ada: {normalized!r}")
            with archive.open(info, "r") as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_SIZE)
            if destination.stat().st_size != info.file_size:
                raise UnsafeArchiveError(f"Ukuran hasil ekstraksi tidak cocok: {normalized!r}")


def _unit_signal_counts(units: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in SENSITIVE_SIGNAL_PATTERNS}
    for unit in units:
        text = str(unit.get("text") or "")
        for key, pattern in SENSITIVE_SIGNAL_PATTERNS.items():
            counts[key] += len(pattern.findall(text))
    return counts


def _aggregate_unit_metadata(units: list[dict]) -> dict:
    unit_types = Counter(str(unit.get("unit_type") or "unknown") for unit in units)
    warnings = Counter(
        warning
        for unit in units
        for warning in (unit.get("warnings") or [])
    )
    formula_cells = 0
    merged_ranges = 0
    hyperlink_count = 0
    comment_cells = 0
    embedded_images = 0
    for unit in units:
        metadata = unit.get("metadata") or {}
        formula_cells += int(metadata.get("formula_cells") or 0)
        merged_ranges += len(metadata.get("merged_ranges") or [])
        hyperlink_count += len(metadata.get("hyperlinks") or [])
        comment_cells += len(metadata.get("comment_cells") or [])
        embedded_images += int(unit.get("unit_type") == "embedded_image")
    return {
        "unit_type_counts": dict(sorted(unit_types.items())),
        "formula_cell_count": formula_cells,
        "merged_range_count": merged_ranges,
        "hyperlink_count": hyperlink_count,
        "comment_cell_count": comment_cells,
        "embedded_image_count": embedded_images,
        "warning_counts": dict(warnings.most_common(20)),
    }


def analyse_documents(
    extract_root: Path,
    checked: list[tuple[ZipInfo, str]],
    *,
    local_ocr: bool = False,
    local_ocr_min_confidence: float | None = None,
    local_ocr_max_units: int | None = None,
    local_ocr_pdf_retry_max_units: int | None = None,
    local_ocr_unit_budget_seconds: int | None = None,
    local_ocr_document_budget_seconds: int | None = None,
    local_ocr_max_attempts_per_unit: int | None = None,
    local_ocr_render_batch_units: int | None = None,
    office_render_max_pages: int | None = None,
    baseline_records: dict[tuple[str, str], dict] | None = None,
    checkpoint_records: dict[tuple[str, str], dict] | None = None,
    reprocess_kinds: set[str] | None = None,
    checkpoint_path: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    intake = FileIntakeSecurityEngine()
    parser = NativeParsingEngine()
    coverage_engine = CoverageEngine()
    template_engine = TemplateCompletenessEngine()
    settings = Settings()
    effective_local_max_units = (
        local_ocr_max_units
        if local_ocr_max_units is not None
        else settings.analysis_local_ocr_max_units
    )
    effective_min_confidence = (
        local_ocr_min_confidence
        if local_ocr_min_confidence is not None
        else settings.analysis_local_ocr_min_confidence
    )
    effective_retry_max_units = (
        local_ocr_pdf_retry_max_units
        if local_ocr_pdf_retry_max_units is not None
        else settings.analysis_pdf_retry_max_units
    )
    effective_unit_budget_seconds = (
        local_ocr_unit_budget_seconds
        if local_ocr_unit_budget_seconds is not None
        else settings.analysis_local_ocr_unit_budget_seconds
    )
    effective_document_budget_seconds = (
        local_ocr_document_budget_seconds
        if local_ocr_document_budget_seconds is not None
        else settings.analysis_local_ocr_document_budget_seconds
    )
    effective_max_attempts_per_unit = (
        local_ocr_max_attempts_per_unit
        if local_ocr_max_attempts_per_unit is not None
        else settings.analysis_local_ocr_max_attempts_per_unit
    )
    effective_render_batch_units = (
        local_ocr_render_batch_units
        if local_ocr_render_batch_units is not None
        else settings.analysis_local_ocr_render_batch_units
    )
    effective_office_max_pages = (
        office_render_max_pages
        if office_render_max_pages is not None
        else settings.analysis_office_render_max_pages
    )
    effective_settings = settings.model_copy(update={
        "analysis_local_ocr_unit_budget_seconds": effective_unit_budget_seconds,
        "analysis_local_ocr_document_budget_seconds": effective_document_budget_seconds,
        "analysis_local_ocr_max_attempts_per_unit": effective_max_attempts_per_unit,
        "analysis_local_ocr_render_batch_units": effective_render_batch_units,
    })
    local_provider = (
        configured_local_ocr_provider(effective_settings) if local_ocr else None
    )
    if local_ocr and local_provider is None:
        status = local_ocr_runtime_status(effective_settings)
        raise RuntimeError(
            "Local OCR diminta tetapi provider tidak tersedia: "
            + str(status.get("availability_reason") or "provider tidak ditemukan")
        )
    profile_details = {
        "version": "corpus-audit-safe-adaptive-budget-v4",
        "local_ocr": bool(local_ocr),
        "local_provider": getattr(local_provider, "name", None),
        "local_max_units": int(effective_local_max_units),
        "local_min_confidence": float(effective_min_confidence),
        "local_unit_budget_seconds": int(effective_unit_budget_seconds),
        "local_document_budget_seconds": int(effective_document_budget_seconds),
        "local_max_attempts_per_unit": int(effective_max_attempts_per_unit),
        "local_render_batch_units": int(effective_render_batch_units),
        "local_max_image_pixels": int(settings.analysis_local_ocr_max_image_pixels),
        "local_max_tiles": int(settings.analysis_local_ocr_max_tiles),
        "pdf_dpi": int(settings.analysis_pdf_render_dpi),
        "pdf_retry_dpi": int(settings.analysis_pdf_retry_render_dpi),
        "pdf_retry_max_units": int(effective_retry_max_units),
        "office_rendering_enabled": bool(settings.analysis_office_rendering_enabled),
        "office_render_max_pages": int(effective_office_max_pages),
    }
    audit_profile = hashlib.sha256(
        json.dumps(profile_details, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    visual_engine = VisualOCREngine(None, local_provider) if local_provider else None
    baseline_records = baseline_records or {}
    checkpoint_records = checkpoint_records or {}
    reprocess_kinds = reprocess_kinds or set()
    results: list[dict] = []
    manifest: list[dict] = []
    files = [(info, name) for info, name in checked if not info.is_dir()]
    for index, (info, relative_name) in enumerate(files, start=1):
        path = extract_root / relative_name
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(relative_name)[0]
        identity, findings, intake_result = intake.run(
            file_name=relative_name,
            content_type=content_type,
            payload=payload,
        )
        record = {
            "document_id": f"corpus-20260706-{index:03d}",
            "file_name": relative_name,
            "sha256": identity.sha256,
            "size_bytes": identity.size_bytes,
            "content_type": content_type,
            "file_kind": identity.file_kind,
            "intake_status": intake_result.status.value,
            "security_findings": [finding.to_dict() for finding in findings],
            "audit_profile": audit_profile,
            "audit_profile_details": profile_details,
        }
        case_types = ["edge"]
        notes = "Dokumen operasional belum berlabel ahli; hanya untuk analisis lokal dan penemuan failure mode."
        reuse_key = (identity.sha256, relative_name)
        previous = checkpoint_records.get(reuse_key)
        reused_from = ""
        if previous and previous.get("audit_profile") == audit_profile:
            reused_from = "checkpoint"
        elif identity.file_kind not in reprocess_kinds:
            previous = baseline_records.get(reuse_key)
            if previous:
                reused_from = "baseline"
        else:
            previous = None
        if previous and reused_from:
            record = {
                **previous,
                **record,
                "reprocessing": {
                    "reused": True,
                    "source": reused_from,
                },
            }
            previous_coverage = previous.get("coverage") or {}
            if previous.get("coverage_status") == "completed":
                case_types = ["positive"]
            elif previous_coverage.get("ocr_required_units"):
                case_types = ["edge", "historical_failure"]
        elif intake_result.status.value == "blocked":
            record.update(
                {
                    "parser_status": "skipped",
                    "coverage_status": "blocked",
                    "failure_reasons": [finding.message for finding in findings if finding.blocking],
                }
            )
            case_types = ["adversarial"]
        else:
            units, inventory, parsing_result = parser.run(identity, payload, "full_audit")
            ocr_record = None
            visual_audit_required = bool(
                any(unit.get("status") == "ocr_required" for unit in units)
                or identity.file_kind in {"docx", "xlsx"}
            )
            if visual_engine and visual_audit_required:
                units, ocr_result = visual_engine.run(
                    identity,
                    payload,
                    units,
                    local_max_units=(
                        effective_local_max_units
                    ),
                    local_min_confidence=(
                        effective_min_confidence
                    ),
                    pdf_dpi=settings.analysis_pdf_render_dpi,
                    pdf_retry_dpi=settings.analysis_pdf_retry_render_dpi,
                    pdf_retry_max_units=(
                        effective_retry_max_units
                    ),
                    local_unit_budget_seconds=effective_unit_budget_seconds,
                    local_document_budget_seconds=effective_document_budget_seconds,
                    local_max_attempts_per_unit=effective_max_attempts_per_unit,
                    local_render_batch_units=effective_render_batch_units,
                    timeout_seconds=settings.analysis_local_ocr_timeout_seconds,
                    office_rendering_enabled=settings.analysis_office_rendering_enabled,
                    office_page_expansion_enabled=True,
                    office_render_max_pages=(
                        effective_office_max_pages
                    ),
                )
                ocr_record = {
                    "status": ocr_result.status.value,
                    "provider": getattr(local_provider, "name", None),
                    "processed_units": int(ocr_result.output.get("local_ocr_processed") or 0),
                    "tiled_processed_units": int(
                        ocr_result.output.get("local_ocr_tiled_processed") or 0
                    ),
                    "remaining_units": sum(unit.get("status") == "ocr_required" for unit in units),
                    "region_count": int(ocr_result.output.get("ocr_region_count") or 0),
                    "visual_semantics_pending": int(
                        ocr_result.output.get("visual_semantics_pending") or 0
                    ),
                    "review_candidates": int(
                        ocr_result.output.get("ocr_review_candidates") or 0
                    ),
                    "pdf_retry_required": int(
                        ocr_result.output.get("pdf_retry_required") or 0
                    ),
                    "pdf_retry_processed": int(
                        ocr_result.output.get("pdf_retry_processed") or 0
                    ),
                    "pdf_retry_deferred": int(
                        ocr_result.output.get("pdf_retry_deferred") or 0
                    ),
                    "attempt_count": int(
                        ocr_result.output.get("local_ocr_attempt_count") or 0
                    ),
                    "timeout_count": int(
                        ocr_result.output.get("local_ocr_timeout_count") or 0
                    ),
                    "budget_exhausted_units": int(
                        ocr_result.output.get("local_ocr_budget_exhausted_units") or 0
                    ),
                    "budget_exhaustion_reasons": dict(
                        ocr_result.output.get("local_ocr_budget_exhaustion_reasons") or {}
                    ),
                    "document_elapsed_ms": int(
                        ocr_result.output.get("local_ocr_document_elapsed_ms") or 0
                    ),
                    "office_total_pages": int(
                        ocr_result.output.get("office_total_pages") or 0
                    ),
                    "office_pdf_total_pages": int(
                        ocr_result.output.get("office_pdf_total_pages") or 0
                    ),
                    "office_pages_scheduled": int(
                        ocr_result.output.get("office_pages_scheduled") or 0
                    ),
                    "office_pages_deferred": int(
                        ocr_result.output.get("office_pages_deferred") or 0
                    ),
                    "office_hidden_pages_excluded": int(
                        ocr_result.output.get("office_hidden_pages_excluded") or 0
                    ),
                    "office_adaptive_low_dpi_pages": int(
                        ocr_result.output.get("office_adaptive_low_dpi_pages") or 0
                    ),
                    "office_render_failed": any(
                        str(unit.get("unit_type") or "") == "office_visual_document"
                        for unit in units
                    ),
                    "warnings": list(ocr_result.warnings)[:50],
                    "external_ai_used": False,
                }
            classified_units, template_ledger, _ = template_engine.run(identity, units)
            coverage_ledger, coverage_result = coverage_engine.run(identity, classified_units)
            status_counts = Counter(str(unit.get("status") or "pending") for unit in classified_units)
            signal_counts = _unit_signal_counts(classified_units)
            metadata_counts = _aggregate_unit_metadata(classified_units)
            failure_reasons = list(coverage_ledger.get("block_reasons") or [])
            if parsing_result.error_message:
                failure_reasons.append(parsing_result.error_message)
            record.update(
                {
                    "parser_status": parsing_result.status.value,
                    "coverage_status": coverage_result.status.value,
                    "coverage": coverage_ledger,
                    "inventory": inventory,
                    "unit_status_counts": dict(sorted(status_counts.items())),
                    "unit_metrics": metadata_counts,
                    "extracted_char_count": parsing_result.metrics.get("extracted_char_count", 0),
                    "template_ledger": template_ledger,
                    "sensitive_signal_counts": signal_counts,
                    "failure_reasons": failure_reasons,
                    "local_ocr": ocr_record,
                }
            )
            if coverage_ledger.get("coverage_status") == "complete":
                case_types = ["positive"]
            elif coverage_ledger.get("ocr_required_units"):
                case_types = ["edge", "historical_failure"]
            else:
                case_types = ["edge"]
        manifest.append(
            {
                "document_id": record["document_id"],
                "file_name": relative_name,
                "sha256": identity.sha256,
                "consent_scope": "local_analysis_only",
                "sensitivity": "restricted",
                "dataset_status": "pilot_unlabelled",
                "organization": None,
                "period": None,
                "case_types": case_types,
                "expected_mappings": [],
                "expected_source_locations": [],
                "labelled_by": None,
                "labelled_at": None,
                "notes": notes,
            }
        )
        results.append(record)
        if checkpoint_path and reused_from != "checkpoint":
            _jsonl_append(checkpoint_path, record)
        print(
            f"[{index:03d}/{len(files):03d}] {identity.file_kind:<7} "
            f"{record.get('parser_status', 'skipped'):<9} {relative_name}"
            f"{' [reused ' + reused_from + ']' if reused_from else ''}",
            flush=True,
        )
    return results, manifest


def summarize(archive_report: dict, results: list[dict]) -> dict:
    kind_counts = Counter(item["file_kind"] for item in results)
    parser_counts = Counter(item.get("parser_status", "unknown") for item in results)
    coverage_counts = Counter(item.get("coverage_status", "unknown") for item in results)
    hashes: defaultdict[str, list[str]] = defaultdict(list)
    audit_profiles: Counter[str] = Counter()
    for item in results:
        hashes[item["sha256"]].append(item["document_id"])
        audit_profiles[str(item.get("audit_profile") or "legacy_unversioned")] += 1
    duplicate_groups = [ids for ids in hashes.values() if len(ids) > 1]
    unit_status_counts: Counter[str] = Counter()
    sensitive_signal_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    totals = Counter()
    for item in results:
        unit_status_counts.update(item.get("unit_status_counts") or {})
        sensitive_signal_counts.update(item.get("sensitive_signal_counts") or {})
        metrics = item.get("unit_metrics") or {}
        warning_counts.update(metrics.get("warning_counts") or {})
        totals["bytes"] += int(item.get("size_bytes") or 0)
        totals["characters"] += int(item.get("extracted_char_count") or 0)
        totals["templates"] += int((item.get("template_ledger") or {}).get("template_only_units") or 0)
        totals["formula_cells"] += int(metrics.get("formula_cell_count") or 0)
        totals["merged_ranges"] += int(metrics.get("merged_range_count") or 0)
        totals["hyperlinks"] += int(metrics.get("hyperlink_count") or 0)
        totals["comments"] += int(metrics.get("comment_cell_count") or 0)
        totals["embedded_images"] += int(metrics.get("embedded_image_count") or 0)
        totals["reused_documents"] += int(bool((item.get("reprocessing") or {}).get("reused")))
        ocr = item.get("local_ocr") or {}
        totals["local_ocr_documents"] += int(bool(ocr))
        totals["local_ocr_processed_units"] += int(ocr.get("processed_units") or 0)
        if "tiled_processed_units" in ocr:
            tiled_processed = int(ocr.get("tiled_processed_units") or 0)
        else:
            # Older checkpoint/baseline records predate the explicit metric.
            # Preserve their result by counting one successful unit per
            # provider warning, e.g. "... memakai 4 tile ...".
            tiled_processed = sum(
                len(re.findall(r"\bmemakai \d+ tile\b", str(warning)))
                for warning in (ocr.get("warnings") or [])
            )
        totals["local_ocr_tiled_processed_units"] += tiled_processed
        totals["local_ocr_remaining_units"] += int(ocr.get("remaining_units") or 0)
        totals["local_ocr_regions"] += int(ocr.get("region_count") or 0)
        totals["local_ocr_visual_semantics_pending"] += int(
            ocr.get("visual_semantics_pending") or 0
        )
        totals["local_ocr_review_candidates"] += int(
            ocr.get("review_candidates") or 0
        )
        totals["local_ocr_pdf_retry_required"] += int(
            ocr.get("pdf_retry_required") or 0
        )
        totals["local_ocr_pdf_retry_processed"] += int(
            ocr.get("pdf_retry_processed") or 0
        )
        totals["local_ocr_pdf_retry_deferred"] += int(
            ocr.get("pdf_retry_deferred") or 0
        )
        totals["local_ocr_attempts"] += int(ocr.get("attempt_count") or 0)
        totals["local_ocr_timeouts"] += int(ocr.get("timeout_count") or 0)
        totals["local_ocr_budget_exhausted_units"] += int(
            ocr.get("budget_exhausted_units") or 0
        )
        totals["local_ocr_document_elapsed_ms"] += int(
            ocr.get("document_elapsed_ms") or 0
        )
        totals["office_render_documents"] += int(
            item.get("file_kind") in {"docx", "xlsx"} and bool(ocr)
        )
        totals["office_rendered_pages"] += int(ocr.get("office_total_pages") or 0)
        totals["office_pdf_pages"] += int(ocr.get("office_pdf_total_pages") or 0)
        totals["office_scheduled_pages"] += int(ocr.get("office_pages_scheduled") or 0)
        totals["office_deferred_pages"] += int(ocr.get("office_pages_deferred") or 0)
        totals["office_hidden_pages_excluded"] += int(
            ocr.get("office_hidden_pages_excluded") or 0
        )
        totals["office_adaptive_low_dpi_pages"] += int(
            ocr.get("office_adaptive_low_dpi_pages") or 0
        )
        totals["office_render_failures"] += int(bool(ocr.get("office_render_failed")))
        inventory = item.get("inventory") or {}
        totals["pdf_pages"] += int(inventory.get("total_pages") or 0)
        totals["xlsx_sheets"] += int(inventory.get("total_sheets") or 0)
        totals["hidden_xlsx_sheets"] += sum(
            str(sheet.get("state") or "visible") != "visible"
            for sheet in (inventory.get("sheets") or [])
        )
        totals["docx_blocks"] += int(inventory.get("total_blocks") or 0)
        totals["xlsx_drawing_parts"] += int(inventory.get("drawing_part_count") or 0)
        totals["xlsx_shape_drawings"] += int(inventory.get("shape_drawing_count") or 0)
        totals["xlsx_charts"] += int(inventory.get("chart_count") or 0)
        totals["xlsx_external_links"] += int(inventory.get("external_link_part_count") or 0)
        totals["xlsx_table_definitions"] += int(inventory.get("table_definition_count") or 0)
        totals["xlsx_defined_names"] += int(inventory.get("defined_name_count") or 0)
        totals["xlsx_print_areas"] += int(inventory.get("print_area_count") or 0)
    blocked = [item["document_id"] for item in results if item.get("intake_status") == "blocked"]
    incomplete = [
        item["document_id"]
        for item in results
        if item.get("coverage_status") not in {"completed"}
    ]
    return {
        "archive": archive_report,
        "document_count": len(results),
        "file_kind_counts": dict(sorted(kind_counts.items())),
        "parser_status_counts": dict(sorted(parser_counts.items())),
        "coverage_engine_status_counts": dict(sorted(coverage_counts.items())),
        "unit_status_counts": dict(sorted(unit_status_counts.items())),
        "total_document_bytes": totals["bytes"],
        "total_extracted_characters": totals["characters"],
        "template_only_unit_count": totals["templates"],
        "formula_cell_count": totals["formula_cells"],
        "merged_range_count": totals["merged_ranges"],
        "hyperlink_count": totals["hyperlinks"],
        "comment_cell_count": totals["comments"],
        "embedded_image_unit_count": totals["embedded_images"],
        "local_ocr_document_count": totals["local_ocr_documents"],
        "local_ocr_processed_unit_count": totals["local_ocr_processed_units"],
        "local_ocr_tiled_processed_unit_count": totals[
            "local_ocr_tiled_processed_units"
        ],
        "local_ocr_remaining_unit_count": totals["local_ocr_remaining_units"],
        "local_ocr_region_count": totals["local_ocr_regions"],
        "local_ocr_visual_semantics_pending_count": totals[
            "local_ocr_visual_semantics_pending"
        ],
        "local_ocr_review_candidate_count": totals[
            "local_ocr_review_candidates"
        ],
        "local_ocr_pdf_retry_required_count": totals[
            "local_ocr_pdf_retry_required"
        ],
        "local_ocr_pdf_retry_processed_count": totals[
            "local_ocr_pdf_retry_processed"
        ],
        "local_ocr_pdf_retry_deferred_count": totals[
            "local_ocr_pdf_retry_deferred"
        ],
        "local_ocr_attempt_count": totals["local_ocr_attempts"],
        "local_ocr_timeout_count": totals["local_ocr_timeouts"],
        "local_ocr_budget_exhausted_unit_count": totals[
            "local_ocr_budget_exhausted_units"
        ],
        "local_ocr_document_elapsed_ms": totals[
            "local_ocr_document_elapsed_ms"
        ],
        "office_render_document_count": totals["office_render_documents"],
        "office_rendered_page_count": totals["office_rendered_pages"],
        "office_pdf_page_count": totals["office_pdf_pages"],
        "office_scheduled_page_count": totals["office_scheduled_pages"],
        "office_deferred_page_count": totals["office_deferred_pages"],
        "office_hidden_page_excluded_count": totals["office_hidden_pages_excluded"],
        "office_adaptive_low_dpi_page_count": totals[
            "office_adaptive_low_dpi_pages"
        ],
        "office_render_failure_count": totals["office_render_failures"],
        "pdf_page_count": totals["pdf_pages"],
        "xlsx_sheet_count": totals["xlsx_sheets"],
        "hidden_xlsx_sheet_count": totals["hidden_xlsx_sheets"],
        "docx_block_count": totals["docx_blocks"],
        "xlsx_drawing_part_count": totals["xlsx_drawing_parts"],
        "xlsx_shape_drawing_count": totals["xlsx_shape_drawings"],
        "xlsx_chart_count": totals["xlsx_charts"],
        "xlsx_external_link_part_count": totals["xlsx_external_links"],
        "xlsx_table_definition_count": totals["xlsx_table_definitions"],
        "xlsx_defined_name_count": totals["xlsx_defined_names"],
        "xlsx_print_area_count": totals["xlsx_print_areas"],
        "sensitive_signal_counts": dict(sorted(sensitive_signal_counts.items())),
        "top_warning_counts": dict(warning_counts.most_common(20)),
        "exact_duplicate_group_count": len(duplicate_groups),
        "exact_duplicate_document_count": sum(len(group) for group in duplicate_groups),
        "exact_duplicate_groups": duplicate_groups,
        "audit_profile_counts": dict(sorted(audit_profiles.items())),
        "reused_document_count": totals["reused_documents"],
        "blocked_document_ids": blocked,
        "incomplete_document_ids": incomplete,
        "local_only": True,
        "external_ai_used": False,
    }


def build_review_queue(results: list[dict], limit: int = 50) -> list[dict]:
    quotas = {"xlsx": 20, "pdf": 18, "docx": 6, "image": 6}
    duplicate_hashes: set[str] = set()
    selected: list[dict] = []

    def priority(item: dict) -> tuple:
        coverage = item.get("coverage") or {}
        incomplete = int(item.get("coverage_status") != "completed")
        ocr = int(coverage.get("ocr_required_units") or 0)
        partial = int(coverage.get("partial_units") or 0)
        structural = sum(
            int((item.get("inventory") or {}).get(key) or 0)
            for key in ("chart_count", "shape_drawing_count", "external_link_part_count")
        )
        return (-incomplete, -ocr, -partial, -structural, -int(item.get("size_bytes") or 0), item["document_id"])

    for kind, quota in quotas.items():
        candidates = sorted((item for item in results if item["file_kind"] == kind), key=priority)
        picked = 0
        for item in candidates:
            if item["sha256"] in duplicate_hashes:
                continue
            duplicate_hashes.add(item["sha256"])
            selected.append(item)
            picked += 1
            if picked >= quota:
                break
    if len(selected) < limit:
        for item in sorted(results, key=priority):
            if item["sha256"] in duplicate_hashes:
                continue
            duplicate_hashes.add(item["sha256"])
            selected.append(item)
            if len(selected) >= limit:
                break

    queue = []
    for rank, item in enumerate(selected[:limit], start=1):
        coverage = item.get("coverage") or {}
        inventory = item.get("inventory") or {}
        focus = []
        if coverage.get("ocr_required_units"):
            focus.append("validasi OCR/visual terhadap halaman atau gambar sumber")
        if coverage.get("partial_units"):
            focus.append("validasi chart/shape dan hubungan visual")
        if inventory.get("formula_cell_count") or (item.get("unit_metrics") or {}).get("formula_cell_count"):
            focus.append("validasi formula dan nilai workbook")
        if inventory.get("external_link_part_count"):
            focus.append("validasi external-link dan snapshot nilai")
        if item["file_kind"] == "docx":
            focus.append("validasi sumber hingga tabel dan baris")
        if not focus:
            focus.append("baseline positif untuk mapping dan source accuracy")
        queue.append(
            {
                "queue_rank": rank,
                "document_id": item["document_id"],
                "file_name": item["file_name"],
                "sha256": item["sha256"],
                "file_kind": item["file_kind"],
                "parser_status": item.get("parser_status"),
                "coverage": coverage,
                "review_focus": focus,
                "expected_mappings": [],
                "expected_source_locations": [],
                "reviewer_id": None,
                "reviewed_at": None,
                "review_status": "pending_human_expert",
            }
        )
    return queue


def write_markdown(path: Path, summary: dict) -> None:
    kind_rows = "\n".join(
        f"| {key} | {value} |" for key, value in summary["file_kind_counts"].items()
    )
    status_rows = "\n".join(
        f"| {key} | {value} |" for key, value in summary["parser_status_counts"].items()
    )
    unit_rows = "\n".join(
        f"| {key} | {value} |" for key, value in summary["unit_status_counts"].items()
    )
    markdown = f"""# Audit Korpus Dokumen 2026-07-06

Audit ini memproses seluruh dokumen secara lokal. Tidak ada isi dokumen yang dikirim ke DeepSeek, Sumopod, atau layanan eksternal. Dokumen tetap berstatus `pilot_unlabelled`, bukan `expert_gold`.

## Ringkasan

- Dokumen: {summary['document_count']}
- Dokumen yang digunakan ulang dari checkpoint/baseline eksplisit: {summary['reused_document_count']}
- Ukuran dokumen: {summary['total_document_bytes']:,} byte
- Karakter teks terekstrak: {summary['total_extracted_characters']:,}
- Unit template-only: {summary['template_only_unit_count']:,}
- Unit embedded image: {summary['embedded_image_unit_count']:,}
- Dokumen diproses local OCR: {summary['local_ocr_document_count']:,}
- Unit berhasil local OCR: {summary['local_ocr_processed_unit_count']:,}
- Unit berhasil melalui fallback tiled OCR: {summary['local_ocr_tiled_processed_unit_count']:,}
- Unit tetap membutuhkan OCR/vision: {summary['local_ocr_remaining_unit_count']:,}
- Bounding region local OCR: {summary['local_ocr_region_count']:,}
- Unit OCR dengan makna visual masih pending: {summary['local_ocr_visual_semantics_pending_count']:,}
- Unit OCR di bawah ambang yang siap untuk human rescue: {summary['local_ocr_review_candidate_count']:,}
- Unit PDF yang menjalani retry resolusi tinggi: {summary['local_ocr_pdf_retry_required_count']:,}
- Unit PDF berhasil setelah retry: {summary['local_ocr_pdf_retry_processed_count']:,}
- Unit PDF retry ditunda oleh cost budget: {summary['local_ocr_pdf_retry_deferred_count']:,}
- Percobaan subprocess Tesseract: {summary['local_ocr_attempt_count']:,}
- Timeout subprocess Tesseract: {summary['local_ocr_timeout_count']:,}
- Unit dihentikan aman oleh resource budget: {summary['local_ocr_budget_exhausted_unit_count']:,}
- Waktu kumulatif Visual/OCR: {summary['local_ocr_document_elapsed_ms'] / 1000:,.1f} detik
- Dokumen DOCX/XLSX yang menjalani full-document render: {summary['office_render_document_count']:,}
- Halaman Office mentah dalam PDF konversi: {summary['office_pdf_page_count']:,}
- Halaman/sheet Office visible yang diroute: {summary['office_rendered_page_count']:,}
- Halaman hidden sheet yang dikecualikan dari OCR visual: {summary['office_hidden_page_excluded_count']:,}
- Halaman/sheet Office dijadwalkan OCR: {summary['office_scheduled_page_count']:,}
- Halaman Office dirender ulang adaptif untuk menjaga tile budget: {summary['office_adaptive_low_dpi_page_count']:,}
- Halaman/sheet Office ditunda page budget: {summary['office_deferred_page_count']:,}
- Dokumen Office gagal dirender: {summary['office_render_failure_count']:,}
- Halaman PDF: {summary['pdf_page_count']:,}
- Sheet XLSX (hidden): {summary['xlsx_sheet_count']:,} ({summary['hidden_xlsx_sheet_count']:,})
- Blok/baris DOCX: {summary['docx_block_count']:,}
- Grup duplikat identik: {summary['exact_duplicate_group_count']}
- Dokumen intake terblokir: {len(summary['blocked_document_ids'])}
- Dokumen dengan coverage belum lengkap: {len(summary['incomplete_document_ids'])}

## Jenis file

| Jenis | Jumlah |
|---|---:|
{kind_rows}

## Status parser

| Status | Jumlah |
|---|---:|
{status_rows}

## Status unit

| Status | Jumlah |
|---|---:|
{unit_rows}

## Sinyal struktur

- Formula XLSX: {summary['formula_cell_count']:,}
- Merged range XLSX: {summary['merged_range_count']:,}
- Hyperlink: {summary['hyperlink_count']:,}
- Comment cell XLSX: {summary['comment_cell_count']:,}
- Drawing part XLSX: {summary['xlsx_drawing_part_count']:,}
- Shape drawing bermuatan teks: {summary['xlsx_shape_drawing_count']:,}
- Chart XLSX: {summary['xlsx_chart_count']:,}
- External-link part XLSX: {summary['xlsx_external_link_part_count']:,}
- Table definition XLSX: {summary['xlsx_table_definition_count']:,}
- Defined name XLSX: {summary['xlsx_defined_name_count']:,}
- Explicit print area XLSX: {summary['xlsx_print_area_count']:,}

## Interpretasi untuk pengasahan fitur

1. `ocr_required` adalah bukti kebutuhan OCR/vision lokal atau provider vision yang tervalidasi; unit ini tidak boleh diubah menjadi bukti positif secara otomatis.
2. Dokumen `partial`/`failed` menjadi kandidat regression test dan prioritas perbaikan parser.
3. Duplikat identik harus dikelompokkan agar evaluasi dan retrieval tidak memberi bobot berlebih pada salinan dokumen yang sama.
4. Sinyal data sensitif hanya dicatat sebagai hitungan agregat; isi yang cocok tidak ditulis ke laporan.
5. Promotion gate tetap memerlukan label dan lokasi sumber dari reviewer ahli.
"""
    path.write_text(markdown, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a ZIP document corpus safely and locally.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--extract-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO)
    parser.add_argument(
        "--local-ocr",
        action="store_true",
        help="Jalankan OCR lokal fail-closed pada unit ocr_required; tidak mengirim data ke provider eksternal.",
    )
    parser.add_argument("--local-ocr-min-confidence", type=float)
    parser.add_argument("--local-ocr-max-units", type=int)
    parser.add_argument(
        "--local-ocr-unit-budget-seconds",
        type=int,
        help="Hard wall-clock budget per unit OCR, termasuk seluruh PSM/tile.",
    )
    parser.add_argument(
        "--local-ocr-document-budget-seconds",
        type=int,
        help="Hard wall-clock budget kumulatif rendering dan OCR per dokumen.",
    )
    parser.add_argument(
        "--local-ocr-max-attempts-per-unit",
        type=int,
        help="Hard ceiling jumlah subprocess Tesseract per unit.",
    )
    parser.add_argument(
        "--local-ocr-render-batch-units",
        type=int,
        help="Jumlah unit yang dirender sebelum segera dikirim ke OCR lokal.",
    )
    parser.add_argument(
        "--pdf-retry-max-units",
        type=int,
        help="Override budget retry PDF untuk audit offline; runtime default lebih ketat.",
    )
    parser.add_argument(
        "--office-render-max-pages",
        type=int,
        help="Override page budget full-document DOCX/XLSX untuk audit offline.",
    )
    parser.add_argument(
        "--reuse-results",
        type=Path,
        help="Gunakan ulang record JSONL eksplisit untuk format yang tidak dipilih pada --reprocess-kinds.",
    )
    parser.add_argument(
        "--reprocess-kinds",
        default="",
        help="Daftar format dipisahkan koma yang wajib diproses ulang, misalnya docx,xlsx.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Lanjutkan record dengan audit_profile sama dari checkpoint output sebelumnya.",
    )
    args = parser.parse_args()

    archive = args.archive.expanduser().resolve()
    if not archive.is_file():
        raise SystemExit(f"Archive tidak ditemukan: {archive}")
    output_dir = args.output_dir.expanduser().resolve()
    extract_dir = args.extract_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    allowed_reprocess_kinds = {"pdf", "docx", "xlsx", "pptx", "image", "text"}
    reprocess_kinds = {
        value.strip().lower()
        for value in str(args.reprocess_kinds or "").split(",")
        if value.strip()
    }
    unsupported_kinds = sorted(reprocess_kinds - allowed_reprocess_kinds)
    if unsupported_kinds:
        raise SystemExit(
            "--reprocess-kinds tidak dikenal: " + ", ".join(unsupported_kinds)
        )
    checkpoint_path = output_dir / "document_results.checkpoint.jsonl"
    if checkpoint_path.exists() and not args.resume:
        raise SystemExit(
            f"Checkpoint sudah ada; gunakan --resume atau output directory baru: {checkpoint_path}"
        )
    checkpoint_items = _jsonl_load(checkpoint_path) if checkpoint_path.exists() else []
    checkpoint_records = {
        (str(item.get("sha256") or ""), str(item.get("file_name") or "")): item
        for item in checkpoint_items
    }
    baseline_items: list[dict] = []
    if args.reuse_results:
        baseline_path = args.reuse_results.expanduser().resolve()
        if not baseline_path.is_file():
            raise SystemExit(f"Baseline JSONL tidak ditemukan: {baseline_path}")
        baseline_items = _jsonl_load(baseline_path)
    baseline_records = {
        (str(item.get("sha256") or ""), str(item.get("file_name") or "")): item
        for item in baseline_items
    }

    archive_report, checked = inspect_archive(
        archive,
        max_entries=args.max_entries,
        max_total_bytes=args.max_total_bytes,
        max_file_bytes=args.max_file_bytes,
        max_ratio=args.max_ratio,
    )
    _json_dump(output_dir / "archive_audit.json", archive_report)
    if not archive_report["safe_to_extract"]:
        print(json.dumps(archive_report, ensure_ascii=False, indent=2))
        return 2
    if extract_dir.exists() and any(extract_dir.iterdir()):
        raise SystemExit(f"Extraction directory harus kosong: {extract_dir}")
    safe_extract(archive, extract_dir, checked)
    results, manifest = analyse_documents(
        extract_dir,
        checked,
        local_ocr=args.local_ocr,
        local_ocr_min_confidence=args.local_ocr_min_confidence,
        local_ocr_max_units=args.local_ocr_max_units,
        local_ocr_unit_budget_seconds=args.local_ocr_unit_budget_seconds,
        local_ocr_document_budget_seconds=args.local_ocr_document_budget_seconds,
        local_ocr_max_attempts_per_unit=args.local_ocr_max_attempts_per_unit,
        local_ocr_render_batch_units=args.local_ocr_render_batch_units,
        local_ocr_pdf_retry_max_units=args.pdf_retry_max_units,
        office_render_max_pages=args.office_render_max_pages,
        baseline_records=baseline_records,
        checkpoint_records=checkpoint_records,
        reprocess_kinds=reprocess_kinds,
        checkpoint_path=checkpoint_path,
    )
    summary = summarize(archive_report, results)
    review_queue = build_review_queue(results)
    _jsonl_dump(output_dir / "document_results.jsonl", results)
    _jsonl_dump(output_dir / "manifest.jsonl", manifest)
    _jsonl_dump(output_dir / "expert_review_queue_50.jsonl", review_queue)
    _json_dump(output_dir / "corpus_summary.json", summary)
    write_markdown(output_dir / "CORPUS_AUDIT.md", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
