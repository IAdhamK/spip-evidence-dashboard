from __future__ import annotations

from dataclasses import dataclass
import re

from app.legacy_document_extraction import RISK_DOCUMENT_KEYWORDS
from app.legacy_recommendation_domain import (
    GRADE_ORDER,
    KK_CONTEXT_RULES,
    grade_sort_key,
    natural_sort_key,
)
from app.legacy_text_utils import has_any_keyword, keyword_hits, normalize_text


STOPWORDS = {
    "ada", "atau", "atas", "bagi", "bahwa", "dalam", "dan", "dapat",
    "dengan", "di", "ini", "itu", "ke", "kepada", "oleh", "pada", "para",
    "telah", "untuk", "yang",
}


MIN_CANDIDATE_LIMIT = 1


MAX_CANDIDATE_LIMIT = 100


SDM_CONTEXT_KEYWORDS = [
    "sdm", "pegawai", "kompetensi", "keterampilan", "diklat", "pelatihan",
    "pembinaan sdm", "pengembangan kompetensi",
]


@dataclass(frozen=True)
class CandidateSeed:
    kk_id: str
    kode: str
    detail_kode: str
    grade: str
    subunsur_name: str
    unsur: str
    uraian: str
    kriteria: str
    penjelasan: str
    cara_pengujian: str | None
    folder_path: str
    public_url: str | None
    corpus: str


def collect_batch_candidates(results: list[dict], candidate_limit: int) -> list[dict]:
    grouped: dict[tuple[str, str, str, str], dict] = {}
    for file_index, result in enumerate(results):
        for candidate in result.get("candidates") or []:
            key = (candidate.get("kk_id"), candidate.get("kode"), candidate.get("detail_kode"), candidate.get("grade"))
            if not all(key):
                continue
            current = grouped.get(key)
            if not current:
                grouped[key] = {**candidate, "file_indexes": [file_index], "batch_confidence": candidate.get("confidence") or 0}
            else:
                current["file_indexes"].append(file_index)
                current["batch_confidence"] = max(current.get("batch_confidence") or 0, candidate.get("confidence") or 0)
    ranked = sorted(grouped.values(), key=batch_candidate_sort_key)
    return ranked[: max(1, min(MAX_CANDIDATE_LIMIT, candidate_limit))]


def normalize_candidate_limit(value: int | None, default: int) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(MIN_CANDIDATE_LIMIT, min(MAX_CANDIDATE_LIMIT, limit))


def tokenize(value: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", value.lower())
    return {token for token in tokens if len(token) > 2 and token not in STOPWORDS}


def score_candidate(query_tokens: set[str], corpus_tokens: set[str], seed: CandidateSeed, file_name: str) -> float:
    overlap = query_tokens & corpus_tokens
    if not overlap:
        return 0.0
    base = len(overlap) / max(5, len(query_tokens))
    bonus = 0.0
    lowered_name = file_name.lower()
    if seed.kk_id.lower().replace(".", "") in lowered_name.replace(".", ""):
        bonus += 0.12
    if seed.kode in lowered_name:
        bonus += 0.12
    if seed.grade.lower() in lowered_name:
        bonus += 0.06
    if tokenize(seed.subunsur_name) & query_tokens:
        bonus += 0.10
    if tokenize(seed.uraian) & query_tokens:
        bonus += 0.12
    return min(1.0, base + bonus)


def contextual_candidate_adjustment(
    seed: CandidateSeed,
    file_name: str,
    preview_text: str,
    classification: dict | None,
) -> float:
    source_text = normalize_text(f"{file_name} {preview_text}")
    risk_hits = keyword_hits(source_text, RISK_DOCUMENT_KEYWORDS)
    if not risk_hits:
        return 0.0

    adjustment = 0.0
    seed_text = normalize_text(
        f"{seed.kk_id} {seed.kode} {seed.subunsur_name} {seed.unsur} {seed.uraian} {seed.kriteria} {seed.penjelasan}"
    ).lower()
    source_has_sdm = has_any_keyword(source_text, SDM_CONTEXT_KEYWORDS)

    if seed.kk_id == "KK3.1":
        adjustment += 0.08
    elif not has_any_keyword(source_text, KK_CONTEXT_RULES.get(seed.kk_id, {}).get("keywords", [])):
        adjustment -= 0.04

    if "penilaian risiko" in seed.unsur.lower() or seed.kode in {"2.1", "2.2"}:
        adjustment += 0.18
    if seed.kode == "2.1":
        adjustment += 0.08
    if seed.kode == "2.2":
        adjustment += 0.14
    if any(term in seed_text for term in ("peta risiko", "matriks risiko", "register risiko", "rencana tindak pengendalian", "risiko residual")):
        adjustment += 0.10

    safe_grade = (classification or {}).get("safe_grade_ceiling")
    if safe_grade and seed.grade == safe_grade:
        adjustment += 0.10
    elif safe_grade:
        target_level = GRADE_ORDER.get(seed.grade, 0)
        safe_level = GRADE_ORDER.get(safe_grade, 0)
        if target_level > safe_level:
            adjustment -= 0.10
        elif target_level < safe_level:
            adjustment -= 0.03

    if seed.kode == "1.6" and not source_has_sdm:
        adjustment -= 0.30

    return adjustment


def local_candidate_sort_key(item: dict) -> tuple:
    return (
        -(item.get("confidence") or 0),
        str(item.get("kk_id") or ""),
        natural_sort_key(item.get("kode")),
        natural_sort_key(item.get("detail_kode")),
        grade_sort_key(item.get("grade")),
    )


def batch_candidate_sort_key(item: dict) -> tuple:
    return (
        -len(item.get("file_indexes") or []),
        -(item.get("reasoning_score") or 0),
        -(item.get("batch_confidence") or 0),
        str(item.get("kk_id") or ""),
        natural_sort_key(item.get("kode")),
        natural_sort_key(item.get("detail_kode")),
        grade_sort_key(item.get("grade")),
    )


def reason_labels(overlap: list[str], seed: CandidateSeed) -> list[str]:
    labels = []
    subunsur_overlap = sorted(set(overlap) & tokenize(seed.subunsur_name))
    uraian_overlap = sorted(set(overlap) & tokenize(seed.uraian))
    kriteria_overlap = sorted(set(overlap) & tokenize(seed.kriteria))
    if subunsur_overlap:
        labels.append(f"Subunsur cocok: {', '.join(subunsur_overlap[:4])}")
    if uraian_overlap:
        labels.append(f"Parameter cocok: {', '.join(uraian_overlap[:4])}")
    if kriteria_overlap:
        labels.append(f"Kriteria cocok: {', '.join(kriteria_overlap[:4])}")
    if not labels:
        labels.append(f"Istilah cocok: {', '.join(overlap[:5])}")
    return labels[:3]
