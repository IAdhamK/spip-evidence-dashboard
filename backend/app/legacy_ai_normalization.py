from __future__ import annotations

import re

from app.legacy_recommendation_domain import is_meaningful_summary, safe_confidence
from app.legacy_text_utils import clean_ai_text, normalize_text


SUMMARY_MAX_LENGTH = 1800


BATCH_SUMMARY_MAX_LENGTH = 2200


def normalize_batch_analysis(value: dict | None) -> dict:
    if not isinstance(value, dict):
        value = {}
    placements = value.get("placements") if isinstance(value.get("placements"), dict) else {}
    return {
        "package_type": clean_ai_text(value.get("package_type") or value.get("evidence_type"), 120),
        "summary": clean_ai_paragraph(value.get("summary"), BATCH_SUMMARY_MAX_LENGTH),
        "main_conclusion": clean_ai_paragraph(value.get("main_conclusion") or value.get("conclusion"), BATCH_SUMMARY_MAX_LENGTH),
        "upload_strategy": clean_ai_text(value.get("upload_strategy"), 360),
        "missing_evidence": normalize_string_list(value.get("missing_evidence"), 6, 120),
        "placements": {
            "primary": normalize_placements(placements.get("primary") or value.get("primary")),
            "supporting": normalize_placements(placements.get("supporting") or value.get("supporting")),
            "weak": normalize_placements(placements.get("weak") or value.get("weak")),
        },
    }


def enrich_batch_analysis(analysis: dict, batch_candidates: list[dict]) -> dict:
    by_index = {index: candidate for index, candidate in enumerate(batch_candidates)}
    for group in analysis.get("placements", {}).values():
        for item in group:
            candidate = by_index.get(item.get("index"))
            if not candidate:
                continue
            for key in ("kk_id", "kode", "detail_kode", "grade", "subunsur_name", "uraian", "folder_path", "public_url"):
                item[key] = candidate.get(key)
            item["file_indexes"] = candidate.get("file_indexes") or []
            item["reasoning_score"] = candidate.get("reasoning_score")
            item["candidate_status"] = candidate.get("candidate_status")
            item["primary_allowed"] = candidate.get("primary_allowed")
    return analysis


def build_narrative_batch_analysis(narrative_text: str, batch_candidates: list[dict]) -> dict:
    indexed = list(enumerate(batch_candidates))
    primary = [(index, item) for index, item in indexed if item.get("primary_allowed")][:2]
    supporting = [
        (index, item)
        for index, item in indexed
        if (index, item) not in primary and item.get("candidate_status") == "Kandidat Pendukung"
    ][:5]
    used = {index for index, _ in [*primary, *supporting]}
    weak = [(index, item) for index, item in indexed if index not in used][:3]

    def placement(index: int, candidate: dict, role: str) -> dict:
        score = candidate.get("reasoning_score")
        confidence = round(max(0.1, min(0.98, float(score or 0) / 100)), 3)
        return {
            "index": index,
            "role": role,
            "reason": "Dipilih oleh reasoning gate deterministik setelah interpretasi naratif AI.",
            "confidence": confidence,
        }

    return {
        "package_type": "Paket Evidence",
        "summary": extract_narrative_section(narrative_text, ("kesimpulan paket evidence", "kesimpulan evidence"), BATCH_SUMMARY_MAX_LENGTH),
        "main_conclusion": extract_narrative_section(narrative_text, ("kesimpulan paket evidence", "kesimpulan evidence"), BATCH_SUMMARY_MAX_LENGTH),
        "narrative": clean_ai_text(strip_narrative_markup(narrative_text), 2600),
        "upload_strategy": "Gunakan kandidat utama untuk upload. Catat rujukan pendukung bila evidence yang sama relevan lintas KK.",
        "missing_evidence": [],
        "placements": {
            "primary": [placement(index, item, "utama") for index, item in primary],
            "supporting": [placement(index, item, "pendukung") for index, item in supporting],
            "weak": [placement(index, item, "opsional") for index, item in weak],
        },
    }


def ai_message_content(result: dict) -> str:
    content = result["payload"]["choices"][0]["message"].get("content")
    if isinstance(content, str) and content.strip():
        return content
    reasoning = result["payload"]["choices"][0]["message"].get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    raise KeyError("content")


def normalize_evidence_analysis(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return None
    placements = value.get("placements") if isinstance(value.get("placements"), dict) else {}
    normalized = {
        "evidence_type": clean_ai_text(value.get("evidence_type"), 100),
        "summary": clean_ai_paragraph(value.get("summary") or value.get("kesimpulan_evidence"), SUMMARY_MAX_LENGTH),
        "grade_reason": clean_ai_text(value.get("grade_reason") or value.get("alasan_grade"), 300),
        "missing_evidence": normalize_string_list(value.get("missing_evidence") or value.get("kekurangan_evidence"), 6, 120),
        "upgrade_requirements": normalize_string_list(value.get("upgrade_requirements") or value.get("syarat_naik_grade"), 6, 120),
        "placements": {
            "primary": normalize_placements(placements.get("primary") or value.get("primary_placements") or value.get("penempatan_utama")),
            "supporting": normalize_placements(placements.get("supporting") or value.get("supporting_placements") or value.get("penempatan_pendukung")),
            "weak": normalize_placements(placements.get("weak") or value.get("weak_placements") or value.get("penempatan_lemah")),
        },
    }
    if not any([normalized["summary"], normalized["evidence_type"], *normalized["placements"].values()]):
        return None
    return normalized


def normalize_placements(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    items = []
    for raw in value[:6]:
        if not isinstance(raw, dict):
            continue
        items.append({
            "index": safe_int(raw.get("index")),
            "kk_id": clean_ai_text(raw.get("kk_id"), 24),
            "kode": clean_ai_text(raw.get("kode"), 24),
            "detail_kode": clean_ai_text(raw.get("detail_kode"), 24),
            "grade": clean_ai_text(raw.get("grade"), 8).upper(),
            "role": clean_ai_text(raw.get("role"), 80),
            "reason": clean_ai_text(raw.get("reason"), 180),
            "confidence": safe_confidence(raw.get("confidence")),
        })
    return items


NARRATIVE_HEADINGS = (
    "kesimpulan evidence",
    "kesimpulan paket evidence",
    "grade aman",
    "penempatan utama",
    "penempatan pendukung",
    "yang kurang",
    "strategi upload",
)


def strip_narrative_markup(value: object) -> str:
    text = normalize_text(str(value or ""))
    text = text.replace("**", "")
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"\s+-\s+", " ", text)
    return normalize_text(text)


def trim_to_sentence(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    chunk = text[:max_length].rstrip()
    sentence_ends = [match.end() for match in re.finditer(r"[.!?](?:\s|$)", chunk)]
    if sentence_ends:
        last_end = sentence_ends[-1]
        if last_end >= int(max_length * 0.55):
            return chunk[:last_end].strip()
    return chunk.rsplit(" ", 1)[0].rstrip(" ,;:-") + "."


def clean_ai_paragraph(value: object, max_length: int) -> str:
    text = strip_narrative_markup(value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return trim_to_sentence(text, max_length)


def extract_narrative_section(value: object, heading_names: tuple[str, ...], max_length: int) -> str:
    text = str(value or "")
    if not text.strip():
        return ""
    heading_pattern = "|".join(re.escape(item) for item in NARRATIVE_HEADINGS)
    matches = list(re.finditer(rf"(?:\*\*)?({heading_pattern})(?:\*\*)?\s*:?\s*", text, flags=re.IGNORECASE))
    wanted = {item.lower() for item in heading_names}
    for index, match in enumerate(matches):
        heading = match.group(1).lower()
        if heading not in wanted:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = strip_narrative_markup(text[start:end])
        if is_meaningful_summary(section):
            return clean_ai_paragraph(section, max_length)
    cleaned = strip_narrative_markup(text)
    for heading in NARRATIVE_HEADINGS:
        cleaned = re.sub(rf"\b{re.escape(heading)}\b\s*:?", "", cleaned, flags=re.IGNORECASE)
    if is_meaningful_summary(cleaned):
        return clean_ai_paragraph(cleaned, max_length)
    return ""


def normalize_string_list(value: object, limit: int, max_length: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [cleaned for item in value[:limit] if (cleaned := clean_ai_text(item, max_length))]


def safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_narrative_context(value: object, level: int = 0) -> str:
    indent = "  " * level
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = str(key).replace("_", " ").title()
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}{label}:")
                lines.append(format_narrative_context(item, level + 1))
            else:
                lines.append(f"{indent}{label}: {clean_ai_text(item, 3600)}")
        return "\n".join(line for line in lines if line is not None)
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, (dict, list)):
                lines.append(f"{indent}- Item {index}:")
                lines.append(format_narrative_context(item, level + 1))
            else:
                lines.append(f"{indent}- {clean_ai_text(item, 3600)}")
        return "\n".join(lines)
    return f"{indent}{clean_ai_text(value, 3600)}"


def enrich_evidence_analysis(analysis: dict | None, candidates: list[dict]) -> dict | None:
    normalized = normalize_evidence_analysis(analysis)
    if not normalized:
        return None
    by_index = {index: candidate for index, candidate in enumerate(candidates)}
    for index, candidate in enumerate(candidates):
        source_index = candidate.get("ai_source_index")
        if isinstance(source_index, int):
            by_index.setdefault(source_index, candidate)
    for group in normalized["placements"].values():
        for item in group:
            candidate = by_index.get(item.get("index"))
            if not candidate:
                continue
            for key in ("kk_id", "kode", "detail_kode", "grade"):
                if not item.get(key):
                    item[key] = candidate.get(key)
            item["subunsur_name"] = candidate.get("subunsur_name")
            item["uraian"] = candidate.get("uraian")
            item["folder_path"] = candidate.get("folder_path")
            item["public_url"] = candidate.get("public_url")
            item["reasoning_score"] = candidate.get("reasoning_score")
            item["candidate_status"] = candidate.get("candidate_status")
            item["primary_allowed"] = candidate.get("primary_allowed")
            item["gate_warnings"] = candidate.get("gate_warnings") or []
            item["duplicate_check"] = candidate.get("duplicate_check")
    return normalized


def merge_ai_result(local_candidates: list[dict], ai_candidates: list[dict]) -> list[dict]:
    if not ai_candidates:
        return local_candidates
    ranked = []
    used_indexes = set()
    for item in ai_candidates:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(local_candidates) or index in used_indexes:
            continue
        candidate = dict(local_candidates[index])
        candidate["ai_source_index"] = index
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)):
            candidate["confidence"] = max(candidate["confidence"], min(0.98, float(confidence)))
        reason = str(item.get("reason") or "").strip()
        if reason:
            candidate["reasons"] = [f"AI: {reason}", *candidate.get("reasons", [])[:2]]
        candidate["source"] = "knowledge_base_ai_rerank"
        ranked.append(candidate)
        used_indexes.add(index)

    for index, candidate in enumerate(local_candidates):
        if index not in used_indexes:
            ranked.append(candidate)
    return ranked[: len(local_candidates)]
