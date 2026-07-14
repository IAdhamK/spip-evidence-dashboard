from __future__ import annotations

import re
from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus


TEMPLATE_TERMS = (
    "template", "petunjuk pengisian", "cara pengisian", "harap diisi", "wajib diisi",
    "contoh pengisian", "format laporan", "kolom ini", "pilih salah satu", "coret yang tidak perlu",
)
PLACEHOLDER_PATTERNS = (
    r"\[(?:nama|tanggal|unit|jabatan|isi|diisi)[^\]]*\]",
    r"\.{4,}",
    r"_{4,}",
    r"\b(?:xxxx+|n/?a|tbd)\b",
)
ACTIVITY_TERMS = (
    "telah dilaksanakan", "telah ditetapkan", "ditandatangani", "dihadiri",
    "hasil evaluasi", "realisasi", "tindak lanjut", "notulen", "laporan pelaksanaan",
    "keputusan nomor", "surat nomor",
)
EXPLICIT_INSTRUCTION_TERMS = (
    "petunjuk pengisian", "cara pengisian", "harap diisi", "wajib diisi",
    "contoh pengisian", "pilih salah satu", "coret yang tidak perlu",
)


class TemplateCompletenessEngine:
    name = "template_completeness"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        units: list[dict],
    ) -> tuple[list[dict], dict, EngineResult]:
        started = perf_counter()
        updated = [
            {
                **unit,
                "metadata": dict(unit.get("metadata") or {}),
                "warnings": list(unit.get("warnings") or []),
            }
            for unit in units
        ]
        template_units = []
        for unit in updated:
            if unit.get("status") not in {"processed", "partial"}:
                continue
            text = " ".join(str(unit.get("text") or "").lower().split())
            template_hits = [term for term in TEMPLATE_TERMS if term in text]
            placeholder_hits = [
                pattern for pattern in PLACEHOLDER_PATTERNS
                if re.search(pattern, text, flags=re.IGNORECASE)
            ]
            activity_hits = [term for term in ACTIVITY_TERMS if term in text]
            dated_activity = bool(activity_hits and re.search(r"\b(?:19|20)\d{2}\b", text))
            template_score = len(template_hits) + len(placeholder_hits)
            explicit_instruction = any(term in text for term in EXPLICIT_INSTRUCTION_TERMS)
            template_only = bool(
                not dated_activity
                and (
                    template_score >= 2
                    or explicit_instruction
                )
            )
            unit["metadata"]["template_detection"] = {
                "template_only": template_only,
                "template_score": template_score,
                "template_terms": template_hits[:10],
                "placeholder_pattern_count": len(placeholder_hits),
                "activity_terms": activity_hits[:10],
            }
            if template_only:
                template_units.append(str(unit.get("unit_key") or ""))
                unit["warnings"].append(
                    "Unit terdeteksi sebagai template/instruksi tanpa bukti aktivitas; dikecualikan dari fact extraction."
                )
        checked = sum(item.get("status") in {"processed", "partial"} for item in updated)
        ledger = {
            "checked_units": checked,
            "template_only_units": len(template_units),
            "template_unit_keys": template_units,
            "substantive_units": checked - len(template_units),
        }
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED,
            input_checksum=identity.sha256,
            input_refs=[f"unit:{item.get('unit_key')}" for item in units],
            output_refs=[f"template-ledger:{identity.sha256}"],
            coverage={"required": checked, "processed": checked, "failed": 0},
            warnings=(
                [f"{len(template_units)} unit template-only dikecualikan dari bukti aktivitas."]
                if template_units else []
            ),
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "checked_units": checked,
                "template_only_units": len(template_units),
            },
            output={"template_ledger": ledger},
        ).finish()
        return updated, ledger, result
