from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
from time import perf_counter


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.analysis.document_map import CoverageEngine, NativeParsingEngine  # noqa: E402
from app.analysis.intake import FileIntakeSecurityEngine  # noqa: E402
from app.analysis.local_ocr import configured_local_ocr_provider  # noqa: E402
from app.analysis.vision import VisualOCREngine  # noqa: E402
from app.config import Settings  # noqa: E402


def _bounded(value: int, lower: int, upper: int, label: str) -> int:
    if value < lower or value > upper:
        raise SystemExit(f"{label} harus di antara {lower} dan {upper}.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe OCR lokal satu dokumen dengan hierarchical hard budget."
    )
    parser.add_argument("document", type=Path)
    parser.add_argument("--attempt-timeout-seconds", type=int, default=30)
    parser.add_argument("--unit-budget-seconds", type=int, default=180)
    parser.add_argument("--document-budget-seconds", type=int, default=900)
    parser.add_argument("--max-attempts-per-unit", type=int, default=24)
    parser.add_argument("--render-batch-units", type=int, default=4)
    parser.add_argument("--max-units", type=int, default=100)
    parser.add_argument("--pdf-retry-max-units", type=int, default=8)
    args = parser.parse_args()

    document = args.document.expanduser().resolve()
    if not document.is_file():
        raise SystemExit(f"Dokumen tidak ditemukan: {document}")
    attempt_timeout = _bounded(
        args.attempt_timeout_seconds, 3, 120, "--attempt-timeout-seconds"
    )
    unit_budget = _bounded(
        args.unit_budget_seconds, attempt_timeout, 1_800, "--unit-budget-seconds"
    )
    document_budget = _bounded(
        args.document_budget_seconds, attempt_timeout, 7_200, "--document-budget-seconds"
    )
    max_attempts = _bounded(
        args.max_attempts_per_unit, 1, 100, "--max-attempts-per-unit"
    )
    render_batch_units = _bounded(
        args.render_batch_units, 1, 100, "--render-batch-units"
    )
    max_units = _bounded(args.max_units, 1, 500, "--max-units")
    retry_max_units = _bounded(
        args.pdf_retry_max_units, 0, 50, "--pdf-retry-max-units"
    )

    payload = document.read_bytes()
    settings = Settings(
        _env_file=None,
        analysis_local_ocr_enabled=True,
        analysis_local_ocr_timeout_seconds=attempt_timeout,
        analysis_local_ocr_unit_budget_seconds=unit_budget,
        analysis_local_ocr_document_budget_seconds=document_budget,
        analysis_local_ocr_max_attempts_per_unit=max_attempts,
        analysis_local_ocr_render_batch_units=render_batch_units,
        analysis_local_ocr_max_units=max_units,
        analysis_pdf_retry_max_units=retry_max_units,
    )
    started = perf_counter()
    identity, findings, intake = FileIntakeSecurityEngine().run(
        file_name=document.name,
        content_type=mimetypes.guess_type(document.name)[0],
        payload=payload,
    )
    if intake.status.value == "blocked":
        print(json.dumps({
            "status": "blocked",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "blocking_findings": sum(bool(item.blocking) for item in findings),
        }, indent=2))
        return 2
    units, inventory, _ = NativeParsingEngine().run(identity, payload, "full_audit")
    provider = configured_local_ocr_provider(settings)
    if provider is None:
        raise SystemExit("Provider OCR lokal tidak tersedia.")
    units, result = VisualOCREngine(None, provider).run(
        identity,
        payload,
        units,
        local_max_units=max_units,
        local_min_confidence=settings.analysis_local_ocr_min_confidence,
        pdf_dpi=settings.analysis_pdf_render_dpi,
        pdf_retry_dpi=settings.analysis_pdf_retry_render_dpi,
        pdf_retry_max_units=retry_max_units,
        local_unit_budget_seconds=unit_budget,
        local_document_budget_seconds=document_budget,
        local_max_attempts_per_unit=max_attempts,
        local_render_batch_units=render_batch_units,
        timeout_seconds=attempt_timeout,
        office_rendering_enabled=settings.analysis_office_rendering_enabled,
        office_page_expansion_enabled=True,
        office_render_max_pages=settings.analysis_office_render_max_pages,
    )
    coverage, _ = CoverageEngine().run(identity, units)
    reason_counts = Counter(
        str((unit.get("metadata") or {}).get("ocr_budget_reason") or "")
        for unit in units
        if (unit.get("metadata") or {}).get("ocr_budget_reason")
    )
    report = {
        "probe_version": "ocr-safe-budget-v1",
        "local_only": True,
        "external_ai_used": False,
        "file_name": document.name,
        "sha256": identity.sha256,
        "file_kind": identity.file_kind,
        "inventory": {
            key: inventory.get(key)
            for key in ("total_pages", "total_sheets", "total_blocks")
            if inventory.get(key) is not None
        },
        "limits": {
            "attempt_timeout_seconds": attempt_timeout,
            "unit_budget_seconds": unit_budget,
            "document_budget_seconds": document_budget,
            "max_attempts_per_unit": max_attempts,
            "render_batch_units": render_batch_units,
            "max_units": max_units,
            "pdf_retry_max_units": retry_max_units,
        },
        "result": {
            "status": result.status.value,
            "coverage_status": coverage.get("coverage_status"),
            "primary_blocked": coverage.get("primary_blocked"),
            "unit_status_counts": dict(sorted(Counter(
                str(unit.get("status") or "pending") for unit in units
            ).items())),
            "attempt_count": int(result.output.get("local_ocr_attempt_count") or 0),
            "timeout_count": int(result.output.get("local_ocr_timeout_count") or 0),
            "budget_exhausted_units": int(
                result.output.get("local_ocr_budget_exhausted_units") or 0
            ),
            "budget_reason_counts": dict(sorted(reason_counts.items())),
            "processed_units": int(result.output.get("local_ocr_processed") or 0),
            "review_candidates": int(result.output.get("ocr_review_candidates") or 0),
            "retry_processed": int(result.output.get("pdf_retry_processed") or 0),
            "retry_deferred": int(result.output.get("pdf_retry_deferred") or 0),
            "elapsed_ms": round((perf_counter() - started) * 1000),
        },
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(encoded).hexdigest()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
