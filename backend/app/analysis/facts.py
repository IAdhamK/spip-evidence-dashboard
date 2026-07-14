from __future__ import annotations

import hashlib
import re
from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.provider import StructuredModelProvider


FACT_TYPE_KEYWORDS = {
    "policy": (
        "kebijakan", "keputusan", "peraturan", "pedoman", "sop", "ditetapkan",
        "surat edaran", "standar", "prosedur",
    ),
    "socialization": (
        "sosialisasi", "undangan", "daftar hadir", "bahan paparan", "disampaikan",
    ),
    "implementation": (
        "pelaksanaan", "dilaksanakan", "implementasi", "telah disusun", "telah ditetapkan",
        "peta risiko", "register risiko", "rencana tindak pengendalian", "rtp", "realisasi",
    ),
    "evaluation": (
        "evaluasi", "monitoring", "pemantauan", "reviu", "review", "triwulan",
        "semester", "berkala", "hasil pemeriksaan",
    ),
    "improvement": (
        "tindak lanjut", "perbaikan", "penyempurnaan", "revisi berdasarkan",
        "ditindaklanjuti", "perubahan proses", "rencana aksi perbaikan",
    ),
}
ORGANIZATION_PATTERNS = (
    r"\bDitjen\s+PDP\b",
    r"\bDirektorat\s+Jenderal[^,.;:]{0,80}",
    r"\bSekretariat\s+Direktorat\s+Jenderal[^,.;:]{0,80}",
)
PERIOD_RE = re.compile(r"\b(?:19|20)\d{2}\b")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
EVIDENCE_ROLES = {"primary", "supporting", "context", "contradictory"}
CONTRADICTORY_EVIDENCE_PATTERNS = (
    "belum dilaksanakan",
    "tidak dilaksanakan",
    "belum tersedia",
    "tidak tersedia",
    "tidak ditemukan",
    "belum ditindaklanjuti",
    "tidak ditindaklanjuti",
    "gagal dilaksanakan",
)


def is_fact_eligible_unit(unit: dict) -> bool:
    metadata = unit.get("metadata") or {}
    return bool(
        unit.get("status") in {"processed", "partial"}
        and unit.get("text")
        and metadata.get("visual_semantics_status") != "pending_review_or_vision"
        and not metadata.get("template_detection", {}).get("template_only")
    )


class FactExtractionEngine:
    name = "fact_extraction"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        document_family: dict | None = None,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        eligible = [unit for unit in units if is_fact_eligible_unit(unit)]
        visual_semantics_blocked = sum(
            1 for unit in units
            if unit.get("text")
            and (unit.get("metadata") or {}).get("visual_semantics_status")
            == "pending_review_or_vision"
        )
        facts: list[dict] = []
        for unit in eligible:
            visual_reviewed = bool((unit.get("metadata") or {}).get("visual_review"))
            ocr_rescued = bool((unit.get("metadata") or {}).get("ocr_rescue"))
            for sentence_index, claim in enumerate(_claims_from_text(str(unit.get("text") or "")), start=1):
                fact_type, matched_terms = classify_fact_type(claim)
                if fact_type == "unknown" and not _looks_substantive(claim):
                    continue
                period_matches = PERIOD_RE.findall(claim)
                organization = _extract_organization(claim)
                local_evidence_role = derive_evidence_role(claim, fact_type)
                evidence_role = inherit_evidence_role(
                    local_evidence_role,
                    _unit_evidence_role(unit, document_family),
                )
                raw_key = f"{unit.get('unit_key')}:{sentence_index}:{claim}"
                fact_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
                facts.append(
                    {
                        "fact_key": fact_key,
                        "claim": claim,
                        "fact_type": fact_type,
                        "evidence_role": evidence_role,
                        "evidence_role_method": (
                            "document_family_inheritance_v1"
                            if document_family else "deterministic_fact_type_v1"
                        ),
                        "organization": organization,
                        "period": period_matches[-1] if period_matches else None,
                        "confidence": 0.82 if matched_terms else 0.65,
                        "extraction_method": (
                            "human_ocr_rescue_transcription_v1"
                            if ocr_rescued
                            else (
                                "human_visual_review_sentence_v1"
                                if visual_reviewed else "deterministic_sentence_v1"
                            )
                        ),
                        "status": "extracted",
                        "matched_terms": matched_terms,
                        "source": {
                            "unit_id": unit.get("id"),
                            "unit_key": unit.get("unit_key"),
                            "source_location": _source_location_for_quote(unit, claim),
                            "source_quote": claim,
                        },
                    }
                )
        warnings = []
        if visual_semantics_blocked:
            warnings.append(
                f"{visual_semantics_blocked} unit OCR gambar tidak dijadikan fakta "
                "karena makna visualnya belum diverifikasi."
            )
        if not eligible:
            warnings.append("Tidak ada unit teks yang eligible untuk Fact Extraction Engine.")
        elif not facts:
            warnings.append("Unit telah diproses tetapi belum menghasilkan fakta substantif.")
        status = EngineStatus.COMPLETED if eligible else EngineStatus.SKIPPED
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=status,
            input_checksum=identity.sha256,
            input_refs=[f"unit:{unit.get('unit_key')}" for unit in eligible],
            output_refs=[f"fact:{fact['fact_key']}" for fact in facts],
            coverage={
                "required": len(eligible),
                "processed": len(eligible),
                "failed": 0,
            },
            warnings=warnings,
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "fact_count": len(facts),
                "visual_semantics_blocked_count": visual_semantics_blocked,
            },
            output={
                "fact_count": len(facts),
                "fact_type_counts": _fact_type_counts(facts),
                "visual_semantics_blocked_count": visual_semantics_blocked,
            },
        ).finish()
        return facts, result


class StructuredFactExtractionEngine:
    name = "structured_fact_extraction"
    version = PIPELINE_VERSION

    def __init__(self, provider: StructuredModelProvider):
        self.provider = provider

    def run(
        self,
        identity: DocumentIdentity,
        units: list[dict],
        document_family: dict | None = None,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        eligible_units = [unit for unit in units if is_fact_eligible_unit(unit)]
        visual_semantics_blocked = sum(
            1 for unit in units
            if unit.get("text")
            and (unit.get("metadata") or {}).get("visual_semantics_status")
            == "pending_review_or_vision"
        )
        unit_map = {str(unit.get("unit_key")): unit for unit in eligible_units}
        if not eligible_units:
            warning = "Tidak ada unit eligible untuk Structured Fact Extraction Engine."
            if visual_semantics_blocked:
                warning = (
                    f"{visual_semantics_blocked} unit OCR gambar tidak dikirim ke model "
                    "karena makna visualnya belum diverifikasi."
                )
            return [], EngineResult(
                engine_name=self.name,
                engine_version=self.version,
                status=EngineStatus.SKIPPED,
                input_checksum=identity.sha256,
                coverage={"required": 0, "processed": 0, "failed": 0},
                warnings=[warning],
                metrics={
                    "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                    "visual_semantics_blocked_count": visual_semantics_blocked,
                },
                output={
                    "fact_count": 0,
                    "rejected_fact_count": 0,
                    "visual_semantics_blocked_count": visual_semantics_blocked,
                },
            ).finish()
        try:
            response = self.provider.extract_facts(eligible_units)
        except Exception as exc:
            return [], EngineResult(
                engine_name=self.name,
                engine_version=self.version,
                status=EngineStatus.FAILED,
                input_checksum=identity.sha256,
                input_refs=[f"unit:{key}" for key in unit_map],
                coverage={
                    "required": len(eligible_units),
                    "processed": 0,
                    "failed": len(eligible_units),
                },
                warnings=["Structured extraction gagal; pipeline mempertahankan fakta deterministik."],
                metrics={"duration_ms": max(0, round((perf_counter() - started) * 1000))},
                error_message=str(exc)[:500],
            ).finish()
        facts = []
        rejected = 0
        for item in response.facts:
            unit = unit_map.get(item.unit_key)
            if not unit or item.source_quote.lower() not in str(unit.get("text") or "").lower():
                rejected += 1
                continue
            raw_key = f"structured:{item.unit_key}:{item.claim}:{item.source_quote}"
            facts.append(
                {
                    "fact_key": hashlib.sha256(raw_key.encode()).hexdigest()[:24],
                    "claim": item.claim,
                    "fact_type": item.fact_type,
                    "evidence_role": inherit_evidence_role(
                        item.evidence_role,
                        _unit_evidence_role(unit, document_family),
                    ),
                    "evidence_role_method": (
                        "structured_model_document_family_inheritance_v1"
                        if document_family else "structured_model_advisory_v1"
                    ),
                    "organization": item.organization,
                    "period": item.period,
                    "confidence": item.confidence,
                    "extraction_method": "structured_model_v1",
                    "status": "extracted",
                    "source": {
                        "unit_id": unit.get("id"),
                        "unit_key": item.unit_key,
                        "source_location": _source_location_for_quote(unit, item.source_quote),
                        "source_quote": item.source_quote,
                    },
                }
            )
        warnings = list(response.warnings)
        if visual_semantics_blocked:
            warnings.append(
                f"{visual_semantics_blocked} unit OCR gambar tidak dikirim ke model "
                "karena makna visualnya belum diverifikasi."
            )
        if rejected:
            warnings.append(f"{rejected} fakta model ditolak karena source_quote/unit tidak valid.")
        return facts, EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED if not rejected else EngineStatus.PARTIAL,
            input_checksum=identity.sha256,
            input_refs=[f"unit:{key}" for key in unit_map],
            output_refs=[f"fact:{item['fact_key']}" for item in facts],
            coverage={
                "required": len(eligible_units),
                "processed": len(eligible_units),
                "failed": 0,
            },
            warnings=warnings,
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "fact_count": len(facts),
                "rejected_fact_count": rejected,
                "visual_semantics_blocked_count": visual_semantics_blocked,
                **response.usage_metrics,
            },
            output={
                "fact_count": len(facts),
                "rejected_fact_count": rejected,
                "visual_semantics_blocked_count": visual_semantics_blocked,
            },
        ).finish()


def classify_fact_type(claim: str) -> tuple[str, list[str]]:
    lowered = claim.lower()
    scored = []
    for fact_type, keywords in FACT_TYPE_KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword in lowered]
        scored.append((len(hits), fact_type, hits))
    score, fact_type, hits = max(scored, default=(0, "unknown", []))
    return (fact_type, hits[:8]) if score else ("unknown", [])


def derive_evidence_role(claim: str, fact_type: str) -> str:
    """Classify an advisory evidence role without granting grade authority."""
    lowered = claim.casefold()
    if any(pattern in lowered for pattern in CONTRADICTORY_EVIDENCE_PATTERNS):
        return "contradictory"
    if fact_type in {"implementation", "evaluation", "improvement"}:
        return "primary"
    if fact_type in {"policy", "socialization"}:
        return "supporting"
    return "context"


def inherit_evidence_role(fact_role: str, document_role: str) -> str:
    """A fact cannot receive more authority than its parent document."""
    if fact_role == "contradictory":
        return "contradictory"
    fact_order = {"context": 0, "supporting": 1, "primary": 2}
    document_ceiling = {
        "reject": "context",
        "optional": "context",
        "supporting": "supporting",
        "primary": "primary",
    }.get(document_role, "context")
    normalized_fact_role = fact_role if fact_role in fact_order else "context"
    return min(
        (normalized_fact_role, document_ceiling),
        key=lambda role: fact_order[role],
    )


def _unit_evidence_role(unit: dict, document_family: dict | None) -> str:
    metadata = unit.get("metadata") or {}
    return str(
        metadata.get("unit_evidence_role")
        or (document_family or {}).get("evidence_role")
        or "primary"
    )


def _claims_from_text(text: str) -> list[str]:
    claims = []
    seen = set()
    for raw in SENTENCE_SPLIT_RE.split(text):
        # Source quotes are provenance, so they must remain an exact substring
        # of the unit text. Normalizing whitespace or appending punctuation to
        # a truncated sentence makes the otherwise deterministic quote fail
        # the verifier on long PDF paragraphs and spreadsheet rows.
        claim = raw.strip(" -•\t\r\n")
        if len(claim) < 20:
            continue
        if len(claim) > 700:
            bounded = claim[:700]
            whitespace_positions = [
                bounded.rfind(character) for character in (" ", "\t", "\r", "\n")
            ]
            last_boundary = max(whitespace_positions)
            if last_boundary >= 20:
                bounded = bounded[:last_boundary]
            claim = bounded.rstrip()
        normalized = " ".join(claim.split()).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        claims.append(claim)
        if len(claims) >= 2000:
            break
    return claims


def _looks_substantive(claim: str) -> bool:
    lowered = claim.lower()
    action_terms = (
        "telah", "dilakukan", "dilaksanakan", "ditetapkan", "menunjukkan", "terdapat",
        "menghasilkan", "berdasarkan", "disusun", "disampaikan", "diperiksa",
    )
    return any(term in lowered for term in action_terms)


def _extract_organization(claim: str) -> str | None:
    for pattern in ORGANIZATION_PATTERNS:
        match = re.search(pattern, claim, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def _source_location_for_quote(unit: dict, quote: str) -> dict:
    location = dict(unit.get("source_location") or {})
    metadata = unit.get("metadata") or {}
    visual_review = metadata.get("visual_review") or {}
    if visual_review:
        location["visual_review"] = {
            key: visual_review.get(key)
            for key in (
                "decision_id", "source_run_id", "decision", "reviewer_id",
                "reviewed_at", "snapshot_checksum",
            )
        }
        location["source_image_sha256"] = metadata.get("ocr_source_image_sha256")
    ocr_rescue = metadata.get("ocr_rescue") or {}
    if ocr_rescue:
        location["ocr_rescue"] = {
            key: ocr_rescue.get(key)
            for key in (
                "decision_id", "source_run_id", "decision", "reviewer_id",
                "reviewed_at", "snapshot_checksum",
            )
        }
        location["source_image_sha256"] = metadata.get("ocr_source_image_sha256")
    semantic_regions = _matching_semantic_regions(
        metadata.get("semantic_regions") or [],
        quote,
        include_unmatched=bool(visual_review or ocr_rescue),
    )
    if semantic_regions:
        location["semantic_regions"] = semantic_regions
    regions = metadata.get("ocr_regions") or []
    if not regions:
        return location
    quote_tokens = {
        token for token in re.findall(r"[a-z0-9]+", str(quote).lower())
        if len(token) >= 3
    }
    matches = []
    for region in regions:
        region_tokens = {
            token for token in re.findall(r"[a-z0-9]+", str(region.get("text") or "").lower())
            if len(token) >= 3
        }
        overlap = len(quote_tokens & region_tokens)
        if not overlap:
            continue
        matches.append({
            "text": str(region.get("text") or "")[:500],
            "confidence": region.get("confidence"),
            "bbox": region.get("bbox") or {},
            "coordinate_space": region.get("coordinate_space") or "normalized_top_left",
        })
        if len(matches) >= 20:
            break
    if matches:
        location["regions"] = matches
        location["ocr_method"] = metadata.get("ocr_method")
        location["ocr_confidence"] = metadata.get("ocr_confidence")
    return location


def _matching_semantic_regions(
    regions: list[dict],
    quote: str,
    *,
    include_unmatched: bool,
) -> list[dict]:
    quote_tokens = {
        token for token in re.findall(r"[a-z0-9]+", str(quote).lower())
        if len(token) >= 3
    }
    matches = []
    for region in regions:
        label_tokens = {
            token for token in re.findall(
                r"[a-z0-9]+",
                " ".join(
                    str(region.get(key) or "")
                    for key in ("label", "semantic_hint", "region_type")
                ).lower(),
            )
            if len(token) >= 3
        }
        if not include_unmatched and not quote_tokens.intersection(label_tokens):
            continue
        matches.append({
            key: region.get(key)
            for key in (
                "region_type", "semantic_hint", "label", "bbox", "bbox_emu",
                "coordinate_space", "locator", "detection_method",
                "requires_human_confirmation",
            )
            if region.get(key) is not None
        })
        if len(matches) >= 20:
            break
    return matches


def _fact_type_counts(facts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        key = str(fact.get("fact_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts
