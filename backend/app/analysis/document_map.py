from __future__ import annotations

from collections import Counter
from time import perf_counter

from app.analysis import PARSER_VERSION, PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.processors import parse_native_document


class NativeParsingEngine:
    name = "native_parsing"
    version = PARSER_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        payload: bytes,
        analysis_mode: str,
    ) -> tuple[list[dict], dict, EngineResult]:
        started = perf_counter()
        try:
            units, inventory = parse_native_document(identity, payload, analysis_mode)
        except Exception as exc:
            result = EngineResult(
                engine_name=self.name,
                engine_version=self.version,
                status=EngineStatus.FAILED,
                input_checksum=identity.sha256,
                input_refs=[f"identity:{identity.sha256}"],
                coverage={"required": 1, "processed": 0, "failed": 1},
                metrics={"duration_ms": _duration_ms(started)},
                error_message=f"Native Parsing Engine gagal: {exc}",
            ).finish()
            return [], {"file_kind": identity.file_kind, "error": str(exc)}, result

        counts = Counter(str(unit.get("status") or "pending") for unit in units)
        if not units or counts.get("failed", 0) == len(units):
            status = EngineStatus.FAILED
        elif any(counts.get(key, 0) for key in ("pending", "partial", "failed", "ocr_required")):
            status = EngineStatus.PARTIAL
        else:
            status = EngineStatus.COMPLETED
        warnings = [
            warning
            for unit in units
            for warning in (unit.get("warnings") or [])
        ][:20]
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=status,
            input_checksum=identity.sha256,
            input_refs=[f"identity:{identity.sha256}"],
            output_refs=[f"unit:{unit['unit_key']}" for unit in units],
            coverage={
                "required": len(units),
                "processed": counts.get("processed", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "ocr_required": counts.get("ocr_required", 0),
                "partial": counts.get("partial", 0),
            },
            warnings=warnings,
            metrics={
                "duration_ms": _duration_ms(started),
                "unit_count": len(units),
                "extracted_char_count": sum(len(unit.get("text") or "") for unit in units),
            },
            output={"inventory": inventory, "status_counts": dict(counts)},
            error_message="Tidak ada unit dokumen yang berhasil diproses." if status == EngineStatus.FAILED else None,
        ).finish()
        return units, inventory, result


class DocumentStructureEngine:
    name = "document_structure"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        inventory: dict,
    ) -> tuple[dict, EngineResult]:
        started = perf_counter()
        type_counts = Counter(str(unit.get("unit_type") or "unknown") for unit in units)
        headings = []
        seen_headings = set()
        semantic_regions = []
        for unit in units:
            path = tuple(str(item) for item in (unit.get("heading_path") or []) if str(item).strip())
            if not path or path in seen_headings:
                continue
            seen_headings.add(path)
            headings.append(
                {
                    "heading_path": list(path),
                    "unit_key": unit.get("unit_key"),
                    "source_location": unit.get("source_location") or {},
                }
            )
        for unit in units:
            for region in (unit.get("metadata") or {}).get("semantic_regions") or []:
                semantic_regions.append(
                    {
                        "unit_key": unit.get("unit_key"),
                        "unit_type": unit.get("unit_type"),
                        "source_location": unit.get("source_location") or {},
                        **region,
                    }
                )
                if len(semantic_regions) >= 5000:
                    break
            if len(semantic_regions) >= 5000:
                break
        document_map = {
            "document_kind": identity.file_kind,
            "inventory": inventory,
            "unit_count": len(units),
            "unit_type_counts": dict(type_counts),
            "headings": headings[:500],
            "semantic_region_count": len(semantic_regions),
            "semantic_regions": semantic_regions,
            "units": [
                {
                    "unit_key": unit.get("unit_key"),
                    "unit_type": unit.get("unit_type"),
                    "ordinal": unit.get("ordinal"),
                    "heading_path": unit.get("heading_path") or [],
                    "source_location": unit.get("source_location") or {},
                    "status": unit.get("status"),
                    "char_count": len(unit.get("text") or ""),
                    "semantic_region_count": len(
                        (unit.get("metadata") or {}).get("semantic_regions") or []
                    ),
                }
                for unit in units
            ],
        }
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED if units else EngineStatus.FAILED,
            input_checksum=identity.sha256,
            input_refs=[f"unit:{unit['unit_key']}" for unit in units],
            output_refs=["document_map:root"],
            coverage={"required": len(units), "processed": len(units), "failed": 0 if units else 1},
            metrics={
                "duration_ms": _duration_ms(started),
                "heading_count": len(headings),
                "semantic_region_count": len(semantic_regions),
            },
            output={"document_map": document_map},
            error_message=None if units else "Document map tidak dapat dibuat tanpa unit.",
        ).finish()
        return document_map, result


class CoverageEngine:
    name = "unitization_coverage"
    version = PIPELINE_VERSION

    def run(self, identity: DocumentIdentity, units: list[dict]) -> tuple[dict, EngineResult]:
        started = perf_counter()
        counts = Counter(str(unit.get("status") or "pending") for unit in units)
        total = len(units)
        processed = counts.get("processed", 0)
        failed = counts.get("failed", 0)
        ocr_required = counts.get("ocr_required", 0)
        partial = counts.get("partial", 0)
        pending = counts.get("pending", 0)
        percentage = round((processed / total) * 100, 2) if total else 0.0
        if total == 0 or failed == total:
            coverage_status = "failed"
        elif processed == total:
            coverage_status = "complete"
        else:
            coverage_status = "partial"
        reasons = []
        if pending:
            reasons.append(f"{pending} unit belum diproses.")
        if ocr_required:
            reasons.append(f"{ocr_required} unit membutuhkan OCR/vision.")
        if partial:
            reasons.append(f"{partial} unit hanya terbaca sebagian.")
        if failed:
            reasons.append(f"{failed} unit gagal diproses.")
        ledger = {
            "total_units": total,
            "processed_units": processed,
            "failed_units": failed,
            "ocr_required_units": ocr_required,
            "partial_units": partial,
            "pending_units": pending,
            "coverage_percentage": percentage,
            "coverage_status": coverage_status,
            "primary_blocked": coverage_status != "complete",
            "block_reasons": reasons,
        }
        result_status = {
            "complete": EngineStatus.COMPLETED,
            "partial": EngineStatus.PARTIAL,
            "failed": EngineStatus.FAILED,
        }[coverage_status]
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=result_status,
            input_checksum=identity.sha256,
            input_refs=[f"unit:{unit['unit_key']}" for unit in units],
            output_refs=["coverage:ledger"],
            coverage={
                "required": total,
                "processed": processed,
                "failed": failed,
                "pending": pending,
                "ocr_required": ocr_required,
                "partial": partial,
            },
            warnings=reasons,
            metrics={"duration_ms": _duration_ms(started)},
            output={"coverage_ledger": ledger},
            error_message="Coverage gagal dihitung." if coverage_status == "failed" else None,
        ).finish()
        return ledger, result


def _duration_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
