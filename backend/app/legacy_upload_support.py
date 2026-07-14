from __future__ import annotations

import hashlib
import math
import re

from app.evidence_structure import safe_segment
from app.legacy_ai_transport import SmartUploadError
from app.legacy_candidate_ranking import MAX_CANDIDATE_LIMIT


SMART_UPLOAD_ACTIONS = {"upload_primary", "reference_supporting", "reference_optional", "reject"}


def normalize_smart_upload_action(value: str | None) -> str:
    action = str(value or "upload_primary").strip().lower()
    if action not in SMART_UPLOAD_ACTIONS:
        raise SmartUploadError("Jenis aksi smart upload tidak valid.")
    return action


def estimate_token_count(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> dict:
    total = input_tokens + output_tokens
    return {
        "low": round(total * 0.00000014, 7),
        "high": round(total * 0.00000025, 7),
    }


def compute_file_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_duplicate_summary(hash_matches: list[dict], indexed_matches: list[dict]) -> dict:
    exact_matches = list(hash_matches)
    same_name_size_matches = [item for item in indexed_matches if item.get("match_type") == "same_name_size"]
    if exact_matches:
        return {
            "status": "exact",
            "severity": "danger",
            "label": "File Sama Pernah Diupload",
            "message": "Hash file sama dengan riwayat upload pintar yang sudah dikonfirmasi.",
            "blocks_upload": True,
            "matches": normalize_duplicate_matches(exact_matches[:5]),
        }
    if same_name_size_matches:
        return {
            "status": "high",
            "severity": "warning",
            "label": "Kemungkinan Duplikat Tinggi",
            "message": "Nama dan ukuran file sama ditemukan pada hasil sinkronisasi Lumbung File.",
            "blocks_upload": False,
            "matches": normalize_duplicate_matches(same_name_size_matches[:5]),
        }
    if indexed_matches:
        return {
            "status": "possible",
            "severity": "warning",
            "label": "Nama File Pernah Ada",
            "message": "Nama file yang sama ditemukan pada hasil sinkronisasi, tetapi ukuran berbeda atau belum dapat dipastikan.",
            "blocks_upload": False,
            "matches": normalize_duplicate_matches(indexed_matches[:5]),
        }
    return {
        "status": "clear",
        "severity": "info",
        "label": "Belum Terdeteksi Duplikat",
        "message": "Belum ada file dengan hash atau nama yang sama pada indeks aplikasi.",
        "blocks_upload": False,
        "matches": [],
    }


def build_candidate_duplicate_check(candidate: dict, hash_matches: list[dict], indexed_matches: list[dict]) -> dict:
    target_matches = [
        item for item in indexed_matches
        if indexed_match_belongs_to_candidate(item, candidate)
    ]
    if hash_matches:
        return {
            "status": "exact",
            "severity": "danger",
            "label": "File sama pernah diupload",
            "message": "Hash file sama dengan riwayat upload pintar yang sudah dikonfirmasi.",
            "blocks_upload": True,
            "matches": normalize_duplicate_matches(hash_matches[:3]),
        }
    if target_matches:
        same_size = [item for item in target_matches if item.get("match_type") == "same_name_size"]
        return {
            "status": "high" if same_size else "possible",
            "severity": "warning",
            "label": "File serupa ada di tujuan",
            "message": "Folder kandidat ini sudah memiliki file dengan nama yang sama pada hasil sinkronisasi.",
            "blocks_upload": bool(same_size),
            "matches": normalize_duplicate_matches((same_size or target_matches)[:3]),
        }
    if indexed_matches:
        return {
            "status": "possible",
            "severity": "warning",
            "label": "Nama file ada di folder lain",
            "message": "Nama file yang sama ditemukan di Lumbung File, tetapi bukan pada folder kandidat ini.",
            "blocks_upload": False,
            "matches": normalize_duplicate_matches(indexed_matches[:3]),
        }
    return {"status": "clear", "severity": "info", "label": "Aman", "message": "", "blocks_upload": False, "matches": []}


def indexed_match_belongs_to_candidate(match: dict, candidate: dict) -> bool:
    remote_path = str(match.get("remote_path") or "").strip("/")
    folder_path = str(candidate.get("folder_path") or "").strip("/")
    if folder_path and (remote_path == folder_path or remote_path.startswith(f"{folder_path}/")):
        return True
    return match.get("kk_id") == candidate.get("kk_id") and match.get("kode") == candidate.get("kode")


def normalize_duplicate_matches(matches: list[dict]) -> list[dict]:
    normalized = []
    for item in matches:
        candidate = item.get("confirmed_candidate") or {}
        normalized.append(
            {
                "source": item.get("source"),
                "match_type": item.get("match_type"),
                "review_id": item.get("id"),
                "file_name": item.get("file_name") or item.get("name"),
                "kk_id": item.get("kk_id") or candidate.get("kk_id"),
                "kode": item.get("kode") or candidate.get("kode"),
                "detail_kode": candidate.get("detail_kode"),
                "grade": candidate.get("grade"),
                "remote_path": item.get("remote_path") or candidate.get("remote_path") or candidate.get("folder_path"),
                "size_bytes": item.get("size_bytes"),
                "modified_at": item.get("modified_at") or item.get("confirmed_at"),
            }
        )
    return normalized


def attach_duplicate_checks(candidates: list[dict], checks: list[dict]) -> list[dict]:
    enriched = []
    for index, candidate in enumerate(candidates):
        duplicate_check = checks[index] if index < len(checks) else None
        if duplicate_check:
            candidate = {**candidate, "duplicate_check": duplicate_check}
        enriched.append(candidate)
    return enriched


def build_analysis_summary(
    mode_key: str,
    mode_config: dict,
    extraction: dict,
    candidate_count: int,
    final_candidate_count: int,
    candidate_limit: int,
    candidate_pool_count: int,
) -> dict:
    sent_chars = extraction.get("sent_char_count") or len(extraction.get("text") or "")
    estimated_input_tokens = estimate_token_count(extraction.get("text") or "") + 260 + (candidate_count * 180)
    estimated_output_tokens = mode_config["expected_output_tokens"]
    link_counts = extraction.get("linked_evidence_status_counts") or {}
    linked_pending = int(link_counts.get("pending") or 0) + int(link_counts.get("fetching") or 0)
    linked_error = int(link_counts.get("error") or 0) + int(link_counts.get("unsupported") or 0)
    return {
        "mode": mode_key,
        "label": mode_config["label"],
        "description": mode_config["description"],
        "method": extraction.get("method"),
        "sent_char_count": sent_chars,
        "extracted_char_count": extraction.get("extracted_char_count") or sent_chars,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_cost_usd": estimate_cost_usd(estimated_input_tokens, estimated_output_tokens),
        "scanned_pages": extraction.get("scanned_pages"),
        "total_pages": extraction.get("total_pages"),
        "page_strategy": extraction.get("page_strategy"),
        "scanned_sheets": extraction.get("scanned_sheets"),
        "total_sheets": extraction.get("total_sheets"),
        "scanned_rows": extraction.get("scanned_rows"),
        "total_rows": extraction.get("total_rows"),
        "evidence_row_count": extraction.get("evidence_row_count"),
        "hyperlink_count": extraction.get("hyperlink_count"),
        "evidence_link_total": extraction.get("linked_evidence_total") or 0,
        "evidence_link_cached_ok": int(link_counts.get("ok") or 0),
        "evidence_link_pending": linked_pending,
        "evidence_link_error": linked_error,
        "linked_evidence_char_count": extraction.get("linked_evidence_cached_text_char_count") or 0,
        "structural_summary": extraction.get("structural_summary"),
        "scanned_slides": extraction.get("scanned_slides"),
        "total_slides": extraction.get("total_slides"),
        "scanned_text_pages": extraction.get("scanned_text_pages"),
        "text_density_chars_per_page": extraction.get("text_density_chars_per_page"),
        "quality_warning": extraction.get("quality_warning"),
        "page_summaries": (extraction.get("page_summaries") or [])[:6],
        "section_summaries": (extraction.get("section_summaries") or [])[:6],
        "sheet_summaries": (extraction.get("sheet_summaries") or [])[:6],
        "slide_summaries": (extraction.get("slide_summaries") or [])[:6],
        "evidence_rows": (extraction.get("evidence_rows") or [])[:8],
        "evidence_links": (extraction.get("evidence_links") or [])[:10],
        "candidate_scope": "Semua KK3.1-KK3.4, seluruh subunsur, detail parameter, dan grade yang tersedia.",
        "candidate_limit": candidate_limit,
        "candidate_limit_max": MAX_CANDIDATE_LIMIT,
        "candidate_pool_count": candidate_pool_count,
        "candidate_count": candidate_count,
        "final_candidate_count": final_candidate_count,
        "note": "Angka token dan biaya adalah estimasi untuk membantu memilih mode analisis.",
    }


def sanitize_upload_filename(file_name: str) -> str:
    if "." in file_name:
        stem, ext = file_name.rsplit(".", 1)
        safe_stem = safe_segment(stem, max_length=100) or "evidence"
        safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:12]
        return f"{safe_stem}.{safe_ext}" if safe_ext else safe_stem
    return safe_segment(file_name, max_length=112) or "evidence"
