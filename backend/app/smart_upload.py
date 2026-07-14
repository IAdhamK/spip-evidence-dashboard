from __future__ import annotations

import json

from app.config import Settings
from app.database import Database, normalize_duplicate_file_name
from app.legacy_ai_normalization import (
    BATCH_SUMMARY_MAX_LENGTH,
    NARRATIVE_HEADINGS,
    SUMMARY_MAX_LENGTH,
    ai_message_content,
    build_narrative_batch_analysis,
    clean_ai_paragraph,
    enrich_batch_analysis,
    enrich_evidence_analysis,
    extract_narrative_section,
    format_narrative_context,
    merge_ai_result,
    normalize_batch_analysis,
    normalize_evidence_analysis,
    normalize_placements,
    normalize_string_list,
    safe_int,
    strip_narrative_markup,
    trim_to_sentence,
)
from app.legacy_ai_transport import (
    SmartUploadError,
    call_chat_completion,
    chat_completion_url,
    prepare_chat_body,
    sanitize_http_error_detail,
)
from app.legacy_candidate_ranking import (
    MAX_CANDIDATE_LIMIT,
    MIN_CANDIDATE_LIMIT,
    SDM_CONTEXT_KEYWORDS,
    STOPWORDS,
    CandidateSeed,
    batch_candidate_sort_key,
    collect_batch_candidates,
    contextual_candidate_adjustment,
    local_candidate_sort_key,
    normalize_candidate_limit,
    reason_labels,
    score_candidate,
    tokenize,
)
from app.legacy_document_extraction import (
    ANALYSIS_MODES,
    DEFAULT_ANALYSIS_MODE,
    IMAGE_EXTENSIONS,
    PPTX_EXTENSIONS,
    READ_LIMIT,
    RISK_DOCUMENT_KEYWORDS,
    TEXT_EXTENSIONS,
    URL_RE,
    XLSX_EVIDENCE_COLUMN_KEYWORDS,
    XLSX_EVIDENCE_ROW_KEYWORDS,
    XLSX_REFERENCE_EVALUATION_KEYWORDS,
    XLSX_REFERENCE_IMPLEMENTATION_KEYWORDS,
    XLSX_REFERENCE_IMPROVEMENT_KEYWORDS,
    XLSX_REFERENCE_POLICY_KEYWORDS,
    XLSX_REFERENCE_SOCIALIZATION_KEYWORDS,
    build_xlsx_evidence_block,
    build_xlsx_row_text,
    expand_xlsx_range,
    extract_docx_parts,
    extract_docx_text,
    extract_image_metadata,
    extract_pdf_text,
    extract_plain_text,
    extract_pptx_notes_text,
    extract_pptx_text,
    extract_preview_text,
    extract_urls,
    extract_xlsx_sheet_structured,
    extract_xlsx_text,
    extract_xml_text_nodes,
    is_xlsx_evidence_context,
    normalize_analysis_mode,
    normalize_office_target,
    normalize_pptx_target,
    normalize_relationship_target,
    office_sort_key,
    parse_int,
    read_xlsx_relationships,
    read_xlsx_shared_strings,
    read_xlsx_sheet_hyperlinks,
    read_xlsx_sheet_map,
    selected_pdf_pages,
    split_xlsx_cell_reference,
    xlsx_cell_formula,
    xlsx_cell_text,
    xlsx_column_index,
    xlsx_column_name,
    xlsx_reference_stage_hints,
    xlsx_sheet_relationships_path,
    xml_attr,
)
from app.legacy_recommendation_domain import (
    ACTUAL_EVALUATION_KEYWORDS,
    ACTUAL_IMPLEMENTATION_KEYWORDS,
    ACTUAL_IMPROVEMENT_KEYWORDS,
    ACTUAL_SOCIALIZATION_KEYWORDS,
    EVIDENCE_STAGE_LABELS,
    EVIDENCE_STAGE_RULES,
    FILLED_RISK_MATRIX_KEYWORDS,
    FORMALITY_KEYWORDS,
    FORM_COMPLETION_PLACEHOLDER_PATTERNS,
    FORM_ONLY_INDICATORS,
    GRADE_BY_STAGE,
    GRADE_MATURITY_RULES,
    GRADE_ORDER,
    KK_CONTEXT_RULES,
    LINK_CACHE_ITEM_TEXT_LIMIT,
    LINK_CACHE_TEXT_LIMIT,
    MAX_LINKS_TO_REGISTER,
    PERIOD_KEYWORDS,
    RELEVANCE_PRIMARY_THRESHOLD,
    RELEVANCE_SUPPORTING_THRESHOLD,
    STRONG_EVALUATION_PROOF_KEYWORDS,
    STRONG_IMPLEMENTATION_PROOF_KEYWORDS,
    STRONG_IMPROVEMENT_PROOF_KEYWORDS,
    TEMPLATE_DOCUMENT_KEYWORDS,
    apply_batch_package_gate,
    apply_evidence_analysis_gate,
    apply_reasoning_gate,
    build_cached_evidence_link_text,
    build_document_profile,
    build_evidence_summary_from_context,
    build_extraction_evidence_text,
    build_reasoning_summary,
    candidate_status_for_score,
    classify_evidence_context,
    classify_reference_stage_hits,
    compact_document_profile,
    detect_template_document,
    determine_safe_grade_from_chain,
    evidence_link_stage_hits,
    extract_external_evidence_links,
    grade_match_score,
    grade_sort_key,
    has_actual_evaluation_artifact,
    has_actual_improvement_artifact,
    has_actual_xlsx_evidence_reference,
    hit_score,
    is_meaningful_summary,
    merge_stage_hit_dicts,
    natural_sort_key,
    reasoning_candidate_sort_key,
    regex_hits,
    safe_confidence,
    score_batch_package,
    score_candidate_reasoning,
    unique_hits,
)
from app.legacy_text_utils import clean_ai_text, has_any_keyword, keyword_hits, normalize_text
from app.legacy_upload_support import (
    SMART_UPLOAD_ACTIONS,
    attach_duplicate_checks,
    build_analysis_summary,
    build_candidate_duplicate_check,
    build_duplicate_summary,
    compute_file_sha256,
    estimate_cost_usd,
    estimate_token_count,
    indexed_match_belongs_to_candidate,
    normalize_duplicate_matches,
    normalize_smart_upload_action,
    sanitize_upload_filename,
)
from app.webdav_client import PublicShareWebDavClient, public_folder_link

class SmartUploadService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    def recommend(
        self,
        file_name: str,
        content_type: str | None,
        payload: bytes,
        skip_ai_message: str | None = None,
        analysis_mode: str = DEFAULT_ANALYSIS_MODE,
        candidate_limit: int | None = None,
    ) -> dict:
        mode_key, mode_config = normalize_analysis_mode(analysis_mode)
        effective_candidate_limit = normalize_candidate_limit(candidate_limit, mode_config["candidate_limit"])
        extraction = extract_preview_text(file_name, content_type, payload, self.settings.ai_send_full_document, mode_key)
        link_crawl = self._prepare_evidence_link_cache(extraction, mode_config)
        preclassification = classify_evidence_context(file_name, extraction["text"], extraction)
        candidates = self._local_candidates(file_name, extraction["text"], effective_candidate_limit, preclassification)
        prompt_candidate_count = len(candidates)
        candidate_pool_count = self._candidate_pool_count()
        if skip_ai_message:
            ai_result = {"status": "skipped", "message": skip_ai_message}
        else:
            ai_result = self._rerank_with_ai(file_name, content_type, len(payload), extraction["text"], candidates, extraction, preclassification)
        if ai_result["status"] == "ok":
            candidates = merge_ai_result(candidates, ai_result.get("candidates", []))
        elif self.settings.smart_upload_require_ai:
            candidates = []
            ai_result = {
                **ai_result,
                "message": (
                    (ai_result.get("message") or "AI DeepSeek V4 belum berhasil merespons.")
                    + " Mode ini mewajibkan eksekusi melalui API DeepSeek V4, sehingga rekomendasi lokal tidak ditampilkan."
                ),
            }

        gate_result = apply_reasoning_gate(candidates, preclassification)
        candidates = gate_result["candidates"]
        file_sha256 = compute_file_sha256(payload)
        duplicate_check = self._build_duplicate_check(file_name, len(payload), file_sha256, candidates)
        candidates = attach_duplicate_checks(candidates, duplicate_check.get("candidate_checks", []))
        evidence_analysis = enrich_evidence_analysis(ai_result.get("evidence_analysis"), candidates)
        evidence_analysis = apply_evidence_analysis_gate(evidence_analysis, candidates, preclassification)
        link_counts = extraction.get("linked_evidence_status_counts") or {}
        linked_pending = int(link_counts.get("pending") or 0) + int(link_counts.get("fetching") or 0)
        linked_error = int(link_counts.get("error") or 0) + int(link_counts.get("unsupported") or 0)

        review_id = self.db.record_smart_upload_review(
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(payload),
            file_sha256=file_sha256,
            preview_text=extraction["text"],
            candidates=candidates,
            ai_status=ai_result["status"],
            ai_message=ai_result.get("message"),
            payload=payload,
        )

        return {
            "review_id": review_id,
            "mode": "recommendation_only",
            "file": {"name": file_name, "content_type": content_type, "size_bytes": len(payload)},
            "preview_text": extraction["text"],
            "analysis": build_analysis_summary(mode_key, mode_config, extraction, prompt_candidate_count, len(candidates), effective_candidate_limit, candidate_pool_count),
            "link_crawl": link_crawl,
            "extraction": {
                "status": extraction["status"],
                "method": extraction["method"],
                "message": extraction.get("message"),
                "scanned_pages": extraction.get("scanned_pages"),
                "total_pages": extraction.get("total_pages"),
                "scanned_sheets": extraction.get("scanned_sheets"),
                "total_sheets": extraction.get("total_sheets"),
                "scanned_slides": extraction.get("scanned_slides"),
                "total_slides": extraction.get("total_slides"),
                "total_rows": extraction.get("total_rows"),
                "scanned_rows": extraction.get("scanned_rows"),
                "evidence_row_count": extraction.get("evidence_row_count"),
                "hyperlink_count": extraction.get("hyperlink_count"),
                "structural_summary": extraction.get("structural_summary"),
                "evidence_rows": extraction.get("evidence_rows"),
                "evidence_links": extraction.get("evidence_links"),
                "page_summaries": extraction.get("page_summaries"),
                "section_summaries": extraction.get("section_summaries"),
                "sheet_summaries": extraction.get("sheet_summaries"),
                "slide_summaries": extraction.get("slide_summaries"),
                "scanned_text_pages": extraction.get("scanned_text_pages"),
                "text_density_chars_per_page": extraction.get("text_density_chars_per_page"),
                "quality_warning": extraction.get("quality_warning"),
                "extracted_char_count": extraction.get("extracted_char_count"),
                "sent_char_count": extraction.get("sent_char_count"),
                "linked_evidence_total": extraction.get("linked_evidence_total"),
                "linked_evidence_cached_ok": int(link_counts.get("ok") or 0),
                "linked_evidence_pending": linked_pending,
                "linked_evidence_error": linked_error,
                "linked_evidence_cached_text_char_count": extraction.get("linked_evidence_cached_text_char_count"),
            },
            "candidates": candidates,
            "duplicate_check": duplicate_check["summary"],
            "reasoning_gate": gate_result["summary"],
            "evidence_analysis": evidence_analysis,
            "ai_interpretation": ai_result.get("interpretation_text"),
            "ai": {
                "status": ai_result["status"],
                "message": ai_result.get("message"),
                "provider": self.settings.ai_provider,
                "model": self.settings.deepseek_model,
            },
            "upload": {
                "allow_real_upload": self.settings.smart_upload_allow_real_upload,
                "require_confirmation": self.settings.smart_upload_require_confirmation,
                "actions_enabled": True,
                "action_types": ["upload_primary", "reference_supporting", "reference_optional", "reject"],
            },
        }

    def _prepare_evidence_link_cache(self, extraction: dict, mode_config: dict) -> dict:
        external_links = extract_external_evidence_links(extraction)
        unique_links = []
        seen = set()
        for link in external_links:
            url = str(link.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            label = clean_ai_text(str(link.get("label") or ""), 260)
            context = clean_ai_text(
                " ".join(str(link.get(key) or "") for key in ("sheet", "cell", "context")),
                420,
            )
            unique_links.append({"url": url, "label": label, "context": context})
            if len(unique_links) >= MAX_LINKS_TO_REGISTER:
                break

        counts = {"ok": 0, "pending": 0, "fetching": 0, "error": 0, "unsupported": 0}
        if not unique_links:
            extraction["linked_evidence_cache"] = []
            extraction["linked_evidence_total"] = 0
            extraction["linked_evidence_status_counts"] = counts
            extraction["linked_evidence_cached_text_char_count"] = 0
            return {
                "total": 0,
                "cached_ok": 0,
                "pending": 0,
                "fetching": 0,
                "error": 0,
                "unsupported": 0,
                "needs_crawl": False,
                "truncated": 0,
                "items": [],
            }

        for link in unique_links:
            self.db.upsert_evidence_link_cache(link["url"], link["label"], link["context"])
        cached_rows = self.db.evidence_link_cache_many([link["url"] for link in unique_links])

        private_items = []
        public_items = []
        for link in unique_links:
            row = cached_rows.get(link["url"]) or {}
            status = str(row.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
            item = {
                "url": link["url"],
                "label": link["label"],
                "context": link["context"],
                "status": status,
                "title": row.get("title") or "",
                "summary": row.get("summary") or "",
                "error_message": row.get("error_message") or "",
                "stage_hits": row.get("stage_hits") or {},
                "text": row.get("extracted_text") or "",
            }
            private_items.append(item)
            public_items.append({k: v for k, v in item.items() if k not in {"url", "text"}})

        cached_text = build_cached_evidence_link_text(private_items, LINK_CACHE_TEXT_LIMIT)
        if cached_text:
            base_text = extraction.get("text") or ""
            prompt_limit = int(mode_config.get("prompt_char_limit") or len(base_text) or LINK_CACHE_TEXT_LIMIT)
            combined_limit = max(
                prompt_limit,
                min(prompt_limit + LINK_CACHE_TEXT_LIMIT, len(base_text) + len(cached_text) + 1),
            )
            extraction["text"] = clean_ai_text(normalize_text(f"{base_text} {cached_text}"), combined_limit)
            extraction["sent_char_count"] = len(extraction["text"])

        extraction["linked_evidence_cache"] = private_items
        extraction["linked_evidence_total"] = len(unique_links)
        extraction["linked_evidence_status_counts"] = counts
        extraction["linked_evidence_cached_text_char_count"] = len(cached_text)
        pending_count = counts.get("pending", 0) + counts.get("fetching", 0) + counts.get("error", 0)
        return {
            "total": len(unique_links),
            "cached_ok": counts.get("ok", 0),
            "pending": counts.get("pending", 0),
            "fetching": counts.get("fetching", 0),
            "error": counts.get("error", 0),
            "unsupported": counts.get("unsupported", 0),
            "needs_crawl": bool(pending_count),
            "truncated": max(0, len(external_links) - len(unique_links)),
            "items": public_items[:8],
        }

    def confirm_upload(self, review_id: int, candidate_index: int) -> dict:
        return self.perform_action(review_id, candidate_index, "upload_primary")

    def perform_action(self, review_id: int, candidate_index: int | None, action_type: str) -> dict:
        action = normalize_smart_upload_action(action_type)
        review = self.db.smart_upload_review(review_id)
        if not review:
            raise SmartUploadError("Review upload tidak ditemukan.")

        if action == "reject":
            message = "Rekomendasi ditolak dan dicatat untuk kurasi manual."
            action_id = self.db.record_smart_upload_action(review_id, action, None, None, message)
            self.db.mark_smart_upload_confirmed(review_id, {"action_type": action}, "rejected", message)
            return {"review_id": review_id, "action_id": action_id, "status": "rejected", "message": message}

        candidates = json.loads(review.get("candidates_json") or "[]")
        if candidate_index is None or candidate_index < 0 or candidate_index >= len(candidates):
            raise SmartUploadError("Pilihan kandidat tidak valid.")
        candidate = candidates[candidate_index]
        folder_url = candidate.get("public_url")
        if not folder_url and self.settings.has_share_token:
            folder_url = public_folder_link(self.settings.lumbung_host, self.settings.lumbung_share_token, candidate["folder_path"])

        if action in {"reference_supporting", "reference_optional"}:
            role = "pendukung" if action == "reference_supporting" else "opsional"
            message = f"Rujukan {role} dicatat untuk {candidate['kk_id']} / {candidate['kode']} / {candidate['detail_kode']} Grade {candidate['grade']}."
            referenced_candidate = {**candidate, "action_type": action, "reference_role": role, "public_url": folder_url}
            action_id = self.db.record_smart_upload_action(review_id, action, candidate_index, referenced_candidate, message)
            self.db.mark_smart_upload_confirmed(review_id, referenced_candidate, action, message)
            return {
                "review_id": review_id,
                "action_id": action_id,
                "status": action,
                "message": message,
                "candidate": referenced_candidate,
            }

        if review.get("upload_status") in {"uploaded", "uploaded_primary"}:
            raise SmartUploadError("Review ini sudah pernah dikonfirmasi upload utama.")
        if not self.settings.smart_upload_allow_real_upload:
            raise SmartUploadError("Upload sungguhan masih dikunci oleh SMART_UPLOAD_ALLOW_REAL_UPLOAD=false.")
        if not self.settings.has_share_token:
            raise SmartUploadError("LUMBUNG_SHARE_TOKEN belum tersedia untuk upload WebDAV.")

        payload = review.get("file_bytes")
        if not payload:
            raise SmartUploadError("File pending tidak tersedia di database review.")

        file_name = sanitize_upload_filename(review["file_name"])
        previous_hash_matches = self.db.smart_upload_hash_matches(review.get("file_sha256"), current_review_id=review_id)
        if previous_hash_matches:
            first_match = previous_hash_matches[0]
            raise SmartUploadError(
                "File yang sama sudah pernah diupload melalui Upload Pintar "
                f"pada review #{first_match.get('id')}. Cek riwayat/folder tujuan sebelum mengunggah ulang."
            )

        client = PublicShareWebDavClient(
            self.settings.lumbung_host,
            self.settings.lumbung_share_token,
            self.settings.scan_timeout_seconds,
        )
        target_duplicate = self._live_target_duplicate_check(
            client,
            candidate["folder_path"],
            file_name,
            int(review.get("size_bytes") or len(payload)),
        )
        if target_duplicate.get("blocks_upload"):
            raise SmartUploadError(target_duplicate["message"])

        remote_path = client.upload_file(
            candidate["folder_path"],
            file_name,
            bytes(payload),
            review.get("content_type"),
        )
        upload_message = f"Upload utama berhasil ke {remote_path}"
        confirmed_candidate = {
            **candidate,
            "action_type": action,
            "uploaded_file_name": file_name,
            "remote_path": remote_path,
            "public_url": folder_url,
        }
        action_id = self.db.record_smart_upload_action(review_id, action, candidate_index, confirmed_candidate, upload_message)
        self.db.mark_smart_upload_confirmed(review_id, confirmed_candidate, "uploaded_primary", upload_message)
        return {
            "review_id": review_id,
            "action_id": action_id,
            "status": "uploaded_primary",
            "message": upload_message,
            "candidate": confirmed_candidate,
        }

    def interpret_batch(self, results: list[dict], analysis_mode: str, candidate_limit: int | None) -> dict:
        mode_key, mode_config = normalize_analysis_mode(analysis_mode)
        effective_candidate_limit = normalize_candidate_limit(candidate_limit, mode_config["candidate_limit"])
        return self._interpret_batch_with_ai(results, mode_key, mode_config, effective_candidate_limit)

    def _interpret_batch_with_ai(self, results: list[dict], mode_key: str, mode_config: dict, candidate_limit: int) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia di environment DEV."}
        batch_candidates = collect_batch_candidates(results, candidate_limit)
        if not batch_candidates:
            return {"status": "skipped", "message": "Belum ada kandidat file untuk dianalisis sebagai paket evidence."}
        file_summaries = [
            {
                "file_index": index,
                "name": item.get("file", {}).get("name"),
                "content_type": item.get("file", {}).get("content_type"),
                "preview_text": clean_ai_text(item.get("preview_text"), 900 if mode_key == "full" else 650),
                "top_candidates": [
                    {
                        "kk_id": candidate.get("kk_id"),
                        "kode": candidate.get("kode"),
                        "detail_kode": candidate.get("detail_kode"),
                        "grade": candidate.get("grade"),
                        "subunsur_name": candidate.get("subunsur_name"),
                    }
                    for candidate in (item.get("candidates") or [])[:3]
                ],
            }
            for index, item in enumerate(results)
        ]
        prompt_payload = {
            "analysis_mode": mode_key,
            "instruction": "Analisis semua file sebagai satu paket evidence. Pilih penempatan utama dan pendukung dari batch_candidates saja.",
            "files": file_summaries,
            "batch_candidates": [
                {
                    "index": index,
                    "file_indexes": item.get("file_indexes"),
                    "kk_id": item.get("kk_id"),
                    "kode": item.get("kode"),
                    "detail_kode": item.get("detail_kode"),
                    "grade": item.get("grade"),
                    "subunsur_name": item.get("subunsur_name"),
                    "uraian": clean_ai_text(item.get("uraian"), 180),
                    "kriteria": clean_ai_text(item.get("kriteria"), 180),
                }
                for index, item in enumerate(batch_candidates)
            ],
        }
        narrative = interpret_batch_narrative(self.settings, prompt_payload)
        if narrative["status"] != "ok":
            return narrative
        analysis = build_narrative_batch_analysis(narrative["interpretation_text"], batch_candidates)
        analysis = enrich_batch_analysis(analysis, batch_candidates)
        analysis = apply_batch_package_gate(analysis, results)
        return {
            "status": "ok",
            "message": "Analisis AI paket selesai.",
            "analysis": analysis,
        }

    def test_ai_connection(self) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia."}
        body = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "max_tokens": 60,
            "messages": [
                {"role": "system", "content": "Balas singkat dalam Bahasa Indonesia. Jangan gunakan JSON."},
                {"role": "user", "content": "Tulis satu kalimat bahwa endpoint AI merespons."},
            ],
        }
        result = call_chat_completion(self.settings, body)
        if result["status"] != "ok":
            if self.settings.smart_upload_require_ai:
                return {
                    **result,
                    "message": (
                        (result.get("message") or "AI DeepSeek V4 belum berhasil merespons.")
                        + " Mode AI wajib aktif; aplikasi tidak akan memakai rekomendasi lokal sebagai pengganti."
                    ),
                }
            return result
        try:
            content = clean_ai_text(result["payload"]["choices"][0]["message"]["content"], 180)
            return {"status": "ok", "message": "AI endpoint merespons.", "response": {"text": content}}
        except (KeyError, IndexError, TypeError) as exc:
            return {"status": "error", "message": f"AI merespons tetapi konten tidak terbaca: {exc}"}

    def _local_candidates(self, file_name: str, preview_text: str, candidate_limit: int | None = None, classification: dict | None = None) -> list[dict]:
        query_tokens = tokenize(f"{file_name} {preview_text}") or tokenize(file_name)
        scored: list[dict] = []
        for seed in self._candidate_seeds():
            corpus_tokens = tokenize(seed.corpus)
            overlap = sorted(query_tokens & corpus_tokens)
            if not overlap:
                continue
            score = score_candidate(query_tokens, corpus_tokens, seed, file_name)
            score = max(0.0, min(1.0, score + contextual_candidate_adjustment(seed, file_name, preview_text, classification)))
            if score <= 0:
                continue
            scored.append(
                {
                    "kk_id": seed.kk_id,
                    "kode": seed.kode,
                    "detail_kode": seed.detail_kode,
                    "grade": seed.grade,
                    "subunsur_name": seed.subunsur_name,
                    "unsur": seed.unsur,
                    "uraian": seed.uraian,
                    "kriteria": seed.kriteria,
                    "penjelasan": seed.penjelasan,
                    "cara_pengujian": seed.cara_pengujian,
                    "folder_path": seed.folder_path,
                    "public_url": seed.public_url,
                    "confidence": min(0.95, round(score, 3)),
                    "reasons": reason_labels(overlap, seed),
                    "matched_terms": overlap[:12],
                    "source": "knowledge_base",
                }
            )
        scored.sort(key=local_candidate_sort_key)
        limit = candidate_limit or self.settings.ai_max_candidates
        return scored[: max(1, limit)]

    def _candidate_pool_count(self) -> int:
        return len(self._candidate_seeds())

    def _build_duplicate_check(self, file_name: str, size_bytes: int, file_sha256: str, candidates: list[dict]) -> dict:
        hash_matches = self.db.smart_upload_hash_matches(file_sha256)
        indexed_matches = self.db.indexed_file_duplicate_matches(file_name, size_bytes)
        candidate_checks = [
            build_candidate_duplicate_check(candidate, hash_matches, indexed_matches)
            for candidate in candidates
        ]
        return {
            "summary": build_duplicate_summary(hash_matches, indexed_matches),
            "candidate_checks": candidate_checks,
        }

    def _live_target_duplicate_check(
        self,
        client: PublicShareWebDavClient,
        folder_path: str,
        file_name: str,
        size_bytes: int,
    ) -> dict:
        existing_items = client.list_folder(folder_path)
        normalized = normalize_duplicate_file_name(file_name)
        matches = []
        for item in existing_items:
            if item.is_folder:
                continue
            if normalize_duplicate_file_name(item.name) != normalized:
                continue
            matches.append(
                {
                    "source": "lumbung_live",
                    "match_type": "same_name_size" if item.size_bytes == size_bytes else "same_name",
                    "name": item.name,
                    "size_bytes": item.size_bytes,
                    "mime_type": item.mime_type,
                    "modified_at": item.modified_at,
                }
            )
        if not matches:
            return {"status": "clear", "blocks_upload": False, "matches": []}
        return {
            "status": "high",
            "blocks_upload": True,
            "matches": matches[:5],
            "message": (
                f"Folder tujuan sudah memiliki file bernama '{file_name}'. "
                "Upload dibatalkan agar file lama tidak tertimpa. Buka folder tujuan dan cek dulu."
            ),
        }

    def _candidate_seeds(self) -> list[CandidateSeed]:
        seeds: list[CandidateSeed] = []
        for folder in self.db.folders():
            parameters = self.db.parameters(folder["kk_id"], folder["kode"])
            slots = self.db.evidence_slots(folder["kk_id"], folder["kode"])
            slot_map = {(slot["detail_kode"], slot["grade"]): slot for slot in slots}
            for parameter in parameters:
                for grade in parameter.get("grades", []):
                    grade_value = str(grade.get("grade") or "").strip().upper()
                    if not grade_value:
                        continue
                    slot = slot_map.get((parameter["detail_kode"], grade_value))
                    if not slot:
                        continue
                    corpus = " ".join(
                        [
                            folder["kk_id"], folder["kk_title"], folder["kode"], folder["subunsur_name"],
                            folder["unsur"], folder["evidence_hint"], parameter.get("detail_kode") or "",
                            parameter.get("uraian") or "", grade.get("kriteria") or "",
                            grade.get("penjelasan") or "", grade.get("cara_pengujian") or parameter.get("cara_pengujian") or "",
                        ]
                    )
                    seeds.append(
                        CandidateSeed(
                            kk_id=folder["kk_id"],
                            kode=folder["kode"],
                            detail_kode=parameter["detail_kode"],
                            grade=grade_value,
                            subunsur_name=folder["subunsur_name"],
                            unsur=folder["unsur"],
                            uraian=parameter.get("uraian") or "",
                            kriteria=grade.get("kriteria") or "",
                            penjelasan=grade.get("penjelasan") or "",
                            cara_pengujian=grade.get("cara_pengujian") or parameter.get("cara_pengujian"),
                            folder_path=slot["folder_path"],
                            public_url=slot.get("public_url"),
                            corpus=corpus,
                        )
                    )
        return seeds

    def _rerank_with_ai(
        self,
        file_name: str,
        content_type: str | None,
        size_bytes: int,
        preview_text: str,
        candidates: list[dict],
        extraction: dict | None = None,
        preclassification: dict | None = None,
    ) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia di environment DEV."}
        if not candidates:
            return {"status": "skipped", "message": "Tidak ada kandidat lokal untuk direrank oleh AI."}

        preclassification = preclassification or classify_evidence_context(file_name, preview_text, extraction)
        document_profile = (
            preclassification.get("document_profile")
            or build_document_profile(extraction, file_name, preclassification.get("template_guard") or {})
        )
        prompt_payload = {
            "file": {
                "name": file_name,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "preview_text": preview_text,
                "document_profile": document_profile,
            },
            "document_profile": document_profile,
            "preclassification": preclassification,
            "reasoning_rules": {
                "grade_maturity": GRADE_MATURITY_RULES,
                "grade_chain": "E kebijakan; D kebijakan+sosialisasi; C kebijakan+implementasi; B kebijakan+implementasi+evaluasi berkala; A kebijakan+implementasi+evaluasi+perbaikan organisasi",
                "primary_threshold": RELEVANCE_PRIMARY_THRESHOLD,
                "do_not_overgrade": True,
                "template_guard": (
                    "Header, nama sheet, kolom, instruksi, contoh pengisian, dan format matriks tidak boleh dihitung sebagai "
                    "bukti evaluasi/perbaikan. Grade B/A butuh bukti aktivitas evaluasi dan tindak lanjut nyata."
                ),
            },
            "candidates": [
                {
                    "index": index,
                    "kk_id": item["kk_id"],
                    "kode": item["kode"],
                    "detail_kode": item["detail_kode"],
                    "grade": item["grade"],
                    "subunsur_name": item["subunsur_name"],
                    "uraian": item["uraian"],
                    "kriteria": item["kriteria"][:360],
                    "penjelasan": item["penjelasan"][:360],
                }
                for index, item in enumerate(candidates)
            ],
        }
        return interpret_ai_narrative(self.settings, prompt_payload)
































































def interpret_batch_narrative(settings: Settings, prompt_payload: dict) -> dict:
    compact_payload = {
        "analysis_mode": prompt_payload.get("analysis_mode"),
        "instruction": "Analisis semua file sebagai satu paket evidence. Jangan output JSON.",
        "files": [
            {
                "file_index": item.get("file_index"),
                "name": item.get("name"),
                "content_type": item.get("content_type"),
                "preview_text": clean_ai_text(item.get("preview_text"), 900),
                "top_candidates": item.get("top_candidates") or [],
            }
            for item in (prompt_payload.get("files") or [])[:10]
        ],
        "grade_maturity": GRADE_MATURITY_RULES,
        "batch_candidates": [
            {
                "index": item.get("index"),
                "file_indexes": item.get("file_indexes"),
                "kk_id": item.get("kk_id"),
                "kode": item.get("kode"),
                "detail_kode": item.get("detail_kode"),
                "grade": item.get("grade"),
                "subunsur_name": item.get("subunsur_name"),
                "uraian": clean_ai_text(item.get("uraian"), 180),
            }
            for item in (prompt_payload.get("batch_candidates") or [])[:16]
        ],
    }
    body = {
        "model": settings.deepseek_model,
        "temperature": 0,
        "max_tokens": 1200,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Anda adalah evidence analyst SPIP. Analisis kumpulan file sebagai satu paket evidence. "
                    "Jangan balas JSON, jangan menulis kode, dan jangan membuat struktur data. "
                    "Gunakan maturity gate: E kebijakan, D sosialisasi, C implementasi, B evaluasi berkala, A perbaikan organisasi. "
                    "Jika preclassification.document_kind=form_template atau file terlihat sebagai form/template/blanko/contoh pengisian, jangan menyimpulkan evaluasi/perbaikan telah terjadi. "
                    "Kata evaluasi, pemantauan, updating, perbaikan, semester, rencana, atau tindak lanjut pada judul sheet, kolom, instruksi, atau format hanya menjelaskan desain form. "
                    "Untuk dokumen form/template, maksimum Grade C jika isi implementasi benar-benar terisi; jika hanya format kosong, jangan jadikan kandidat utama. "
                    "Tuliskan bagian: Kesimpulan Paket Evidence, Grade Aman Paket, Penempatan Utama, Penempatan Pendukung, Yang Kurang, Strategi Upload. "
                    "Bagian Kesimpulan Paket Evidence wajib berupa satu paragraf utuh 3-5 kalimat, tanpa bullet, dan tidak berhenti di tengah kalimat. "
                    "Jangan membuat KK atau grade di luar kandidat yang tersedia."
                ),
            },
            {"role": "user", "content": format_narrative_context(compact_payload)},
        ],
    }
    result = call_chat_completion(settings, body)
    if result["status"] != "ok":
        return result
    try:
        content = clean_ai_text(ai_message_content(result), 3200)
    except (KeyError, IndexError, TypeError) as exc:
        return {"status": "error", "message": f"Interpretasi AI paket kosong: {exc}"}
    if not content:
        return {"status": "error", "message": "Interpretasi AI paket kosong."}
    return {"status": "ok", "interpretation_text": content}


































def interpret_ai_narrative(settings: Settings, prompt_payload: dict) -> dict:
    file_payload = prompt_payload.get("file") or {}
    preclassification = prompt_payload.get("preclassification") or {}
    document_profile = compact_document_profile(
        file_payload.get("document_profile")
        or prompt_payload.get("document_profile")
        or preclassification.get("document_profile")
        or {}
    )
    compact_payload = {
        "file": {
            "name": file_payload.get("name"),
            "content_type": file_payload.get("content_type"),
            "size_bytes": file_payload.get("size_bytes"),
            "preview_text": clean_ai_text(file_payload.get("preview_text"), 6000),
            "document_profile": document_profile,
        },
        "document_profile": document_profile,
        "preclassification": preclassification,
        "grade_maturity": GRADE_MATURITY_RULES,
        "candidate_summary": [
            {
                "index": item.get("index"),
                "kk_id": item.get("kk_id"),
                "kode": item.get("kode"),
                "detail_kode": item.get("detail_kode"),
                "grade": item.get("grade"),
                "subunsur_name": item.get("subunsur_name"),
                "uraian": clean_ai_text(item.get("uraian"), 180),
            }
            for item in (prompt_payload.get("candidates") or [])[:12]
        ],
    }
    body = {
        "model": settings.deepseek_model,
        "temperature": 0,
        "max_tokens": 1400,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Anda adalah evidence analyst SPIP. Berikan interpretasi naratif ringkas dalam Bahasa Indonesia. "
                    "Jangan balas JSON, jangan menulis kode, dan jangan membuat struktur data. "
                    "Gunakan maturity gate umum: E=baru ada kebijakan; D=kebijakan telah disosialisasikan; "
                    "C=kebijakan telah diimplementasikan; B=kebijakan dan pelaksanaan sudah dievaluasi berkala; "
                    "A=hasil evaluasi sudah dijadikan bahan perbaikan organisasi. "
                    "Kata kunci domain hanya membantu membaca konteks, bukan menaikkan grade. "
                    "Gunakan document_profile, page_summaries, section_summaries, sheet_summaries, slide_summaries, dan jumlah halaman/sheet/slide sebagai konteks cakupan pembacaan. "
                    "Untuk Excel, document_profile.evidence_rows dan document_profile.evidence_links adalah konteks prioritas tertinggi karena berisi baris/kolom evidence seperti Dokumentasi/Evidence RTP beserta hyperlink Google Docs/Drive/Sheets yang terbaca dari workbook. "
                    "Jika evidence_rows/evidence_links menyebut undangan, notulen, notulensi, daftar hadir, atau sosialisasi, jangan menyimpulkan sosialisasi tidak ada; sebut sebagai indikasi bukti sosialisasi dan jelaskan bahwa isi link eksternal belum diverifikasi kecuali dibuka oleh sistem. "
                    "Jika evidence_rows/evidence_links menyebut laporan triwulan, semester, instrumen monev, monitoring, pemantauan, evaluasi, reviu, atau review, jangan menyimpulkan evaluasi tidak ada; sebut sebagai indikasi bukti evaluasi/monitoring dan tetap nilai kekuatannya. "
                    "Jika evidence_rows/evidence_links menyebut peta risiko, matriks, RTP, register risiko, progres RTP, atau rencana tindak pengendalian, baca itu sebagai indikasi implementasi, bukan sekadar kata kunci. "
                    "Hyperlink eksternal harus diperlakukan sebagai referensi evidence yang tertulis di dokumen; jangan mengklaim isi hyperlink telah diverifikasi bila kontennya tidak tersedia di konteks. "
                    "Untuk Excel dan PowerPoint, nama sheet, nama slide, header kolom, daftar isi, dan instruksi pengisian hanya konteks; bukti grade harus berasal dari isi terisi atau dokumen aktivitas aktual. "
                    "Untuk PDF/DOCX, bedakan narasi kebijakan, laporan pelaksanaan, laporan evaluasi, dan tindak lanjut perbaikan; jangan menyamakan rencana dengan bukti hasil. "
                    "Jika preclassification.document_kind=form_template atau file terlihat sebagai form/template/blanko/contoh pengisian, jangan menyimpulkan evaluasi/perbaikan telah terjadi. "
                    "Kata evaluasi, pemantauan, updating, perbaikan, semester, rencana, atau tindak lanjut pada judul sheet, kolom, instruksi, atau format hanya menjelaskan desain form. "
                    "Untuk dokumen form/template, maksimum Grade C jika isi implementasi benar-benar terisi; jika hanya format kosong, jangan jadikan kandidat utama. "
                    "Grade B hanya boleh disebut aman bila ada bukti laporan/berita acara/notulen/reviu evaluasi aktual. "
                    "Grade A hanya boleh disebut aman bila ada tindak lanjut hasil evaluasi yang eksplisit menjadi perbaikan organisasi. "
                    "Tuliskan bagian: Kesimpulan Evidence, Grade Aman, Penempatan Utama, Penempatan Pendukung, Yang Kurang. "
                    "Bagian Kesimpulan Evidence wajib berupa satu paragraf utuh 3-5 kalimat, tanpa bullet, dan tidak berhenti di tengah kalimat. "
                    "Jangan membuat KK atau grade di luar kandidat yang tersedia."
                ),
            },
            {"role": "user", "content": format_narrative_context(compact_payload)},
        ],
    }
    result = call_chat_completion(settings, body)
    if result["status"] != "ok":
        return result
    try:
        content = clean_ai_text(ai_message_content(result), 4200)
    except (KeyError, IndexError, TypeError) as exc:
        return {"status": "error", "message": f"Interpretasi AI kosong: {exc}"}
    if not content:
        return {"status": "error", "message": "Interpretasi AI kosong."}
    summary = extract_narrative_section(content, ("kesimpulan evidence",), SUMMARY_MAX_LENGTH)
    return {
        "status": "ok",
        "message": "Analisis AI selesai.",
        "candidates": [],
        "interpretation_text": content,
        "evidence_analysis": {
            "evidence_type": "Evidence SPIP",
            "summary": summary,
            "grade_reason": "",
            "missing_evidence": [],
            "upgrade_requirements": [],
            "placements": {"primary": [], "supporting": [], "weak": []},
        },
    }
