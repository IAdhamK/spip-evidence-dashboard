from __future__ import annotations

from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import unquote, urlparse


SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "peta risiko": ("petris", "register risiko"),
    "manajemen risiko": ("mr", "rtp", "mitigasi", "petris"),
    "pelaksanaan anggaran": ("ikpa", "rpd", "dipa"),
    "koordinasi keuangan": ("kppn", "djpb", "kemenkeu"),
    "monitoring": ("monev", "evaluasi", "tindak lanjut"),
}

FILE_TYPE_EXTENSIONS: dict[str, set[str]] = {
    "pdf": {"pdf"},
    "word": {"doc", "docx", "odt"},
    "spreadsheet": {"xls", "xlsx", "xlsm", "ods", "csv"},
    "presentation": {"ppt", "pptx", "odp"},
    "image": {"png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp"},
    "text": {"txt", "md", "rtf"},
}


def normalize_search_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    return " ".join(text.split())


def search_plan(query: str) -> dict:
    normalized_query = normalize_search_text(query)
    direct_terms = tuple(term for term in normalized_query.split() if term)
    expanded_terms: list[str] = []
    for phrase, aliases in SEARCH_ALIASES.items():
        if phrase in normalized_query:
            expanded_terms.extend(aliases)
    return {
        "query": str(query or "").strip(),
        "normalized_query": normalized_query,
        "direct_terms": direct_terms,
        "expanded_terms": tuple(dict.fromkeys(expanded_terms)),
    }


def file_category(extension: str) -> str:
    normalized = str(extension or "").strip().lower().lstrip(".")
    for category, extensions in FILE_TYPE_EXTENSIONS.items():
        if normalized in extensions:
            return category
    return "other"


def _relative_remote_path(href: str, fallback_name: str) -> str:
    path = unquote(urlparse(str(href or "")).path).strip()
    marker = "/public.php/dav/files/"
    if marker in path:
        remainder = path.split(marker, 1)[1].lstrip("/")
        if "/" in remainder:
            return remainder.split("/", 1)[1].strip("/")
    return str(fallback_name or "").strip("/")


def _metadata(record: dict) -> dict:
    stored_name = str(record.get("name") or "").strip("/")
    remote_path = _relative_remote_path(str(record.get("href") or ""), stored_name)
    base_name = PurePosixPath(remote_path or stored_name).name
    extension = PurePosixPath(base_name).suffix.lower().lstrip(".")
    parent_path = str(PurePosixPath(remote_path).parent)
    if parent_path == ".":
        parent_path = str(record.get("folder_path") or "").strip("/")
    detail_match = re.search(r"(?:^|/)(\d+\.\d+\.\d+)\b", remote_path)
    grade_match = re.search(r"(?:^|/)Grade\s+([A-E])(?:/|$)", remote_path, re.IGNORECASE)
    return {
        "base_name": base_name,
        "remote_path": remote_path,
        "location_path": f"/{parent_path.strip('/')}" if parent_path else "/",
        "detail_kode": detail_match.group(1) if detail_match else None,
        "grade": grade_match.group(1).upper() if grade_match else None,
        "extension": extension,
        "file_type": file_category(extension),
    }


def _score_record(record: dict, plan: dict) -> tuple[int, list[str], str] | None:
    metadata = _metadata(record)
    normalized_base = normalize_search_text(metadata["base_name"])
    normalized_path = normalize_search_text(metadata["remote_path"])
    query = plan["normalized_query"]
    direct_terms = plan["direct_terms"]
    expanded_terms = plan["expanded_terms"]
    matched_terms: list[str] = []
    score = 0
    match_type = ""

    if normalized_base == query:
        score += 180
        match_type = "exact_name"
    elif query and query in normalized_base:
        score += 130
        match_type = "file_name"
    elif query and query in normalized_path:
        score += 85
        match_type = "location"

    all_direct_in_base = bool(direct_terms) and all(term in normalized_base for term in direct_terms)
    all_direct_in_path = bool(direct_terms) and all(term in normalized_path for term in direct_terms)
    if all_direct_in_base:
        score += 90
        match_type = match_type or "file_name"
        matched_terms.extend(direct_terms)
    elif all_direct_in_path:
        score += 45
        match_type = match_type or "location"
        matched_terms.extend(direct_terms)

    expanded_matches = [term for term in expanded_terms if normalize_search_text(term) in normalized_path]
    if expanded_matches:
        base_matches = [term for term in expanded_matches if normalize_search_text(term) in normalized_base]
        score += 55 if base_matches else 25
        match_type = match_type or "related_keyword"
        matched_terms.extend(base_matches or expanded_matches)

    if score <= 0:
        return None
    if metadata["grade"]:
        score += 2
    return score, list(dict.fromkeys(matched_terms)), match_type


def search_file_records(
    records: list[dict],
    query: str,
    *,
    file_type: str | None = None,
    grade: str | None = None,
    limit: int = 50,
) -> dict:
    plan = search_plan(query)
    normalized_type = str(file_type or "").strip().lower()
    normalized_grade = str(grade or "").strip().upper()
    ranked: list[dict] = []

    for record in records:
        metadata = _metadata(record)
        if normalized_type and normalized_type != "all" and metadata["file_type"] != normalized_type:
            continue
        if normalized_grade and normalized_grade != "ALL" and metadata["grade"] != normalized_grade:
            continue
        scored = _score_record(record, plan)
        if not scored:
            continue
        score, matched_terms, match_type = scored
        ranked.append({
            **record,
            **metadata,
            "search_score": score,
            "match_type": match_type,
            "matched_terms": matched_terms,
        })

    ranked.sort(
        key=lambda item: (
            -int(item["search_score"]),
            str(item.get("base_name") or "").casefold(),
            str(item.get("remote_path") or "").casefold(),
        )
    )
    safe_limit = min(max(int(limit or 50), 1), 100)
    return {
        "query": plan["query"],
        "expanded_terms": list(plan["expanded_terms"]),
        "total": len(ranked),
        "limit": safe_limit,
        "results": ranked[:safe_limit],
    }
