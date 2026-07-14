from __future__ import annotations

from dataclasses import dataclass
import math
from io import BytesIO
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.config import Settings
from app.database import Database
from app.evidence_structure import canonical_folder_path
from app.evidence_structure import safe_segment
from app.webdav_client import PublicShareWebDavClient, canonical_public_folder_url, public_folder_link


STOPWORDS = {
    "ada", "atau", "atas", "bagi", "bahwa", "dalam", "dan", "dapat",
    "dengan", "di", "ini", "itu", "ke", "kepada", "oleh", "pada", "para",
    "telah", "untuk", "yang",
}

TEXT_EXTENSIONS = {".csv", ".htm", ".html", ".json", ".md", ".rtf", ".text", ".txt", ".xml"}
ANALYSIS_MODES = {
    "fast": {
        "label": "Mode Cepat",
        "description": "Nama file dan cuplikan awal untuk screening cepat dengan biaya rendah.",
        "prompt_char_limit": 1000,
        "read_limit": 24000,
        "pdf_page_limit": 5,
        "pdf_strategy": "awal",
        "xlsx_sheet_limit": 4,
        "candidate_limit": 3,
        "expected_output_tokens": 700,
    },
    "deep": {
        "label": "Mode Mendalam",
        "description": "Cuplikan awal, tengah, akhir, dan halaman kunci untuk akurasi lebih baik.",
        "prompt_char_limit": 3500,
        "read_limit": 80000,
        "pdf_page_limit": 12,
        "pdf_strategy": "sampel",
        "xlsx_sheet_limit": 8,
        "candidate_limit": 6,
        "expected_output_tokens": 900,
    },
    "full": {
        "label": "Mode Penuh",
        "description": "Ekstraksi terpanjang dalam satu request AI. Belum memakai chunk bertahap.",
        "prompt_char_limit": 8000,
        "read_limit": 180000,
        "pdf_page_limit": 40,
        "pdf_strategy": "berurutan",
        "xlsx_sheet_limit": 12,
        "candidate_limit": 8,
        "expected_output_tokens": 1200,
    },
}
DEFAULT_ANALYSIS_MODE = "fast"
MIN_CANDIDATE_LIMIT = 1
MAX_CANDIDATE_LIMIT = 100
READ_LIMIT = 24000
SMART_UPLOAD_ACTIONS = {"upload_primary", "reference_supporting", "reference_optional", "reject"}
RELEVANCE_PRIMARY_THRESHOLD = 80
RELEVANCE_SUPPORTING_THRESHOLD = 70
SUMMARY_MAX_LENGTH = 1800
BATCH_SUMMARY_MAX_LENGTH = 2200
GRADE_ORDER = {"E": 1, "D": 2, "C": 3, "B": 4, "A": 5}
GRADE_BY_STAGE = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A"}
EVIDENCE_STAGE_LABELS = {
    "kebijakan": "Kebijakan",
    "sosialisasi": "Sosialisasi",
    "implementasi": "Implementasi",
    "evaluasi": "Evaluasi Berkala",
    "perbaikan": "Perbaikan Organisasi",
}
GRADE_MATURITY_RULES = {
    "E": "baru ada kebijakan",
    "D": "kebijakan telah disosialisasikan",
    "C": "kebijakan telah diimplementasikan",
    "B": "kebijakan dan pelaksanaan sudah dievaluasi berkala",
    "A": "hasil evaluasi sudah dijadikan bahan perbaikan organisasi",
}
KK_CONTEXT_RULES = {
    "KK3.1": {
        "label": "Kesekretariatan/general/Ditjen PDP",
        "keywords": [
            "sekretariat", "setditjen", "ditjen pdp", "direktorat jenderal", "spip", "manajemen risiko", "mr",
            "peta risiko", "register risiko", "rtp", "renstra", "iku", "sop", "sk tim", "komite", "upr",
            "koordinasi", "notulensi", "nota dinas", "laporan kinerja", "apip", "bpkp",
        ],
    },
    "KK3.2": {
        "label": "Keuangan Ditjen PDP",
        "keywords": [
            "keuangan", "anggaran", "rka", "rka-k/l", "dipa", "pok", "pagu", "realisasi", "spm", "sp2d",
            "belanja", "kontrak", "pembayaran", "laporan keuangan", "rekonsiliasi", "bap pagu", "reviu anggaran",
            "pertanggungjawaban", "otorisasi", "verifikasi",
        ],
    },
    "KK3.3": {
        "label": "Aset/BMN Ditjen PDP",
        "keywords": [
            "aset", "bmn", "barang milik negara", "inventaris", "inventarisasi", "kib", "simak", "stock opname",
            "daftar barang", "pemeliharaan", "kendaraan", "gedung", "peralatan", "penghapusan", "pemindahtanganan",
            "rekonsiliasi bmn", "laporan bmn", "pengamanan aset",
        ],
    },
    "KK3.4": {
        "label": "Ketaatan Peraturan",
        "keywords": [
            "ketaatan", "kepatuhan", "peraturan", "perundang", "regulasi", "hukum", "undang-undang", "uu",
            "pp", "perpres", "permen", "permendesa", "ketentuan", "standar", "norma", "sanksi", "audit kepatuhan",
        ],
    },
}
EVIDENCE_STAGE_RULES = {
    "kebijakan": {
        "grade": "E",
        "keywords": [
            "sk", "keputusan", "kepdirjen", "keputusan direktur jenderal", "ditetapkan",
            "penetapan", "sop", "pedoman", "kebijakan", "panduan", "standar",
            "surat edaran", "peraturan", "prosedur",
        ],
    },
    "sosialisasi": {
        "grade": "D",
        "keywords": ["sosialisasi", "undangan", "daftar hadir", "bahan paparan", "paparan", "notulensi sosialisasi", "dokumentasi", "disampaikan"],
    },
    "implementasi": {
        "grade": "C",
        "keywords": [
            "pelaksanaan", "dilaksanakan", "implementasi", "matriks", "matriks risiko",
            "peta risiko", "register risiko", "profil risiko", "rtp",
            "rencana tindak pengendalian", "risiko residual", "mitigasi",
            "risiko strategis", "risiko operasional", "terisi", "output",
            "hasil kegiatan", "berita acara", "laporan kegiatan",
        ],
    },
    "evaluasi": {
        "grade": "B",
        "keywords": ["evaluasi", "monitoring", "pemantauan", "reviu", "review", "semester", "triwulan", "berkala", "apip", "bpkp", "rekap evaluasi"],
    },
    "perbaikan": {
        "grade": "A",
        "keywords": ["tindak lanjut", "perbaikan", "revisi", "penyempurnaan", "continuous improvement", "rencana aksi", "ditindaklanjuti", "perubahan proses", "keputusan pimpinan"],
    },
}
FORMALITY_KEYWORDS = [
    "nomor", "tanggal", "ditandatangani", "ttd", "nip", "nota dinas", "surat",
    "laporan", "keputusan", "kepdirjen", "ditetapkan", "penetapan", "berita acara", "lampiran",
]
PERIOD_KEYWORDS = ["2025", "2026", "semester", "triwulan", "ta 2025", "ta 2026", "tahun 2025", "tahun 2026"]
RISK_DOCUMENT_KEYWORDS = [
    "peta risiko", "matriks risiko", "register risiko", "profil risiko", "rtp",
    "rencana tindak pengendalian", "risiko residual", "mitigasi",
    "risiko strategis", "risiko operasional", "manajemen risiko",
]
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
        preclassification = classify_evidence_context(file_name, extraction["text"])
        candidates = self._local_candidates(file_name, extraction["text"], effective_candidate_limit, preclassification)
        prompt_candidate_count = len(candidates)
        candidate_pool_count = self._candidate_pool_count()
        if skip_ai_message:
            ai_result = {"status": "skipped", "message": skip_ai_message}
        else:
            ai_result = self._rerank_with_ai(file_name, content_type, len(payload), extraction["text"], candidates)
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
        evidence_analysis = enrich_evidence_analysis(ai_result.get("evidence_analysis"), candidates)
        evidence_analysis = apply_evidence_analysis_gate(evidence_analysis, candidates, preclassification)

        review_id = self.db.record_smart_upload_review(
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(payload),
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
            "extraction": {
                "status": extraction["status"],
                "method": extraction["method"],
                "message": extraction.get("message"),
                "scanned_pages": extraction.get("scanned_pages"),
                "total_pages": extraction.get("total_pages"),
                "scanned_sheets": extraction.get("scanned_sheets"),
                "total_sheets": extraction.get("total_sheets"),
                "scanned_text_pages": extraction.get("scanned_text_pages"),
                "text_density_chars_per_page": extraction.get("text_density_chars_per_page"),
                "quality_warning": extraction.get("quality_warning"),
                "extracted_char_count": extraction.get("extracted_char_count"),
                "sent_char_count": extraction.get("sent_char_count"),
            },
            "candidates": candidates,
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
        candidate = {
            **candidate,
            "folder_path": canonical_folder_path(candidate["folder_path"]),
        }
        folder_url = canonical_public_folder_url(candidate.get("public_url"), candidate["folder_path"])
        if self.settings.has_share_token:
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
        client = PublicShareWebDavClient(
            self.settings.lumbung_host,
            self.settings.lumbung_share_token,
            self.settings.scan_timeout_seconds,
        )
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
                    canonical_slot_path = canonical_folder_path(slot["folder_path"])
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
                            folder_path=canonical_slot_path,
                            public_url=(
                                public_folder_link(
                                    self.settings.lumbung_host,
                                    self.settings.lumbung_share_token,
                                    canonical_slot_path,
                                )
                                if self.settings.has_share_token
                                else canonical_public_folder_url(slot.get("public_url"), canonical_slot_path)
                            ),
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
    ) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia di environment DEV."}
        if not candidates:
            return {"status": "skipped", "message": "Tidak ada kandidat lokal untuk direrank oleh AI."}

        preclassification = classify_evidence_context(file_name, preview_text)
        prompt_payload = {
            "file": {"name": file_name, "content_type": content_type, "size_bytes": size_bytes, "preview_text": preview_text},
            "preclassification": preclassification,
            "reasoning_rules": {
                "grade_maturity": GRADE_MATURITY_RULES,
                "grade_chain": "E kebijakan; D kebijakan+sosialisasi; C kebijakan+implementasi; B kebijakan+implementasi+evaluasi berkala; A kebijakan+implementasi+evaluasi+perbaikan organisasi",
                "primary_threshold": RELEVANCE_PRIMARY_THRESHOLD,
                "do_not_overgrade": True,
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





def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for keyword in keywords:
        if keyword.lower() in lowered and keyword not in hits:
            hits.append(keyword)
    return hits


def classify_evidence_context(file_name: str, preview_text: str) -> dict:
    source_text = normalize_text(f"{file_name} {preview_text}")
    kk_scores = {}
    kk_hits = {}
    for kk_id, rule in KK_CONTEXT_RULES.items():
        hits = keyword_hits(source_text, rule["keywords"])
        score = min(1.0, len(hits) / 5)
        kk_scores[kk_id] = round(score, 3)
        kk_hits[kk_id] = hits[:8]
    best_kk = max(kk_scores, key=lambda item: kk_scores[item]) if kk_scores else None
    if best_kk and kk_scores.get(best_kk, 0) <= 0:
        best_kk = None

    stage_hits = {}
    evidence_types = []
    detected_stage = 0
    for stage_name, rule in EVIDENCE_STAGE_RULES.items():
        hits = keyword_hits(source_text, rule["keywords"])
        stage_hits[stage_name] = hits[:8]
        if hits:
            evidence_types.append(stage_name)
            detected_stage = max(detected_stage, GRADE_ORDER[rule["grade"]])
    evidence_type = evidence_types[-1] if evidence_types else "tidak_terklasifikasi"
    chain = {stage_name: bool(stage_hits.get(stage_name)) for stage_name in EVIDENCE_STAGE_RULES}
    safe_grade = determine_safe_grade_from_chain(chain)
    safe_stage = GRADE_ORDER.get(safe_grade or "", 0)
    formality_hits = keyword_hits(source_text, FORMALITY_KEYWORDS)
    period_hits = keyword_hits(source_text, PERIOD_KEYWORDS)
    formality_score = min(1.0, 0.35 + (len(formality_hits) * 0.16)) if formality_hits else 0.35
    period_score = min(1.0, 0.55 + (len(period_hits) * 0.18)) if period_hits else 0.45
    warnings = []
    if chain.get("sosialisasi") and not chain.get("kebijakan"):
        warnings.append("Ada indikasi sosialisasi, tetapi kebijakan dasar belum terbaca.")
    if chain.get("implementasi") and not chain.get("kebijakan"):
        warnings.append("Ada indikasi implementasi, tetapi kebijakan dasar belum terbaca.")
    if chain.get("evaluasi") and not (chain.get("kebijakan") and chain.get("implementasi")):
        warnings.append("Ada indikasi evaluasi, tetapi kebijakan dan/atau implementasi belum kuat.")
    if chain.get("perbaikan") and not chain.get("evaluasi"):
        warnings.append("Ada indikasi perbaikan, tetapi hubungan dengan evaluasi belum kuat.")
    if evidence_types and not safe_grade:
        warnings.append("Jenis evidence terbaca, tetapi rantai bukti minimum untuk grade belum lengkap.")
    return {
        "best_kk": best_kk,
        "best_kk_label": KK_CONTEXT_RULES.get(best_kk, {}).get("label") if best_kk else "Belum jelas",
        "kk_scores": kk_scores,
        "kk_hits": kk_hits,
        "evidence_type": evidence_type,
        "evidence_type_label": EVIDENCE_STAGE_LABELS.get(evidence_type, "Belum terklasifikasi"),
        "evidence_types": evidence_types,
        "detected_stage_level": detected_stage,
        "stage_level": safe_stage,
        "safe_grade_ceiling": safe_grade,
        "grade_maturity_rules": GRADE_MATURITY_RULES,
        "stage_hits": stage_hits,
        "chain": chain,
        "formality_score": round(formality_score, 3),
        "formality_hits": formality_hits[:8],
        "period_score": round(period_score, 3),
        "period_hits": period_hits[:8],
        "warnings": warnings,
    }


def determine_safe_grade_from_chain(chain: dict) -> str | None:
    has_policy = bool(chain.get("kebijakan"))
    has_socialization = bool(chain.get("sosialisasi"))
    has_implementation = bool(chain.get("implementasi"))
    has_evaluation = bool(chain.get("evaluasi"))
    has_improvement = bool(chain.get("perbaikan"))
    safe_grade = None
    if has_policy:
        safe_grade = "E"
    if has_policy and has_socialization:
        safe_grade = "D"
    if has_policy and has_implementation:
        safe_grade = "C"
    if has_policy and has_implementation and has_evaluation:
        safe_grade = "B"
    if has_policy and has_implementation and has_evaluation and has_improvement:
        safe_grade = "A"
    return safe_grade


def apply_reasoning_gate(candidates: list[dict], classification: dict) -> dict:
    gated = []
    for candidate in candidates:
        item = dict(candidate)
        scorecard = score_candidate_reasoning(item, classification)
        item["reasoning_score"] = scorecard["score"]
        item["reasoning_scorecard"] = scorecard
        item["candidate_status"] = candidate_status_for_score(scorecard["score"])
        item["primary_allowed"] = scorecard["score"] > RELEVANCE_PRIMARY_THRESHOLD
        item["safe_grade_ceiling"] = classification.get("safe_grade_ceiling")
        item["evidence_type"] = classification.get("evidence_type_label")
        item["gate_warnings"] = scorecard.get("warnings", [])
        reasons = list(item.get("reasons") or [])
        reasons.insert(0, f"Gate: {item['candidate_status']} ({scorecard['score']}%)")
        item["reasons"] = reasons[:4]
        gated.append(item)
    gated.sort(key=reasoning_candidate_sort_key)
    return {
        "candidates": gated,
        "summary": build_reasoning_summary(classification, gated),
    }


def score_candidate_reasoning(candidate: dict, classification: dict) -> dict:
    kk_scores = classification.get("kk_scores") or {}
    best_kk = classification.get("best_kk")
    candidate_kk = candidate.get("kk_id")
    kk_score = kk_scores.get(candidate_kk, 0)
    if best_kk and candidate_kk == best_kk:
        kk_score = max(kk_score, 0.95)
    elif kk_score <= 0 and not best_kk:
        kk_score = 0.45
    subunsur_score = safe_confidence(candidate.get("confidence")) or 0.0
    target_grade = str(candidate.get("grade") or "").upper()
    target_level = GRADE_ORDER.get(target_grade, 0)
    supported_level = int(classification.get("stage_level") or 0)
    grade_score = grade_match_score(target_level, supported_level)
    formality_score = safe_confidence(classification.get("formality_score")) or 0.35
    period_score = safe_confidence(classification.get("period_score")) or 0.45
    score = round((kk_score * 30) + (subunsur_score * 25) + (grade_score * 25) + (formality_score * 15) + (period_score * 5), 1)
    warnings = list(classification.get("warnings") or [])
    safe_grade = classification.get("safe_grade_ceiling")
    if target_level and supported_level and target_level > supported_level:
        warnings.append(f"Target Grade {target_grade} melebihi grade aman {safe_grade or '-'} berdasarkan rantai bukti.")
    if score <= RELEVANCE_PRIMARY_THRESHOLD:
        warnings.append("Skor belum melewati ambang kandidat utama >80%.")
    return {
        "score": score,
        "threshold_primary": RELEVANCE_PRIMARY_THRESHOLD,
        "threshold_supporting": RELEVANCE_SUPPORTING_THRESHOLD,
        "kk_context": round(kk_score * 30, 1),
        "subunsur_match": round(subunsur_score * 25, 1),
        "grade_match": round(grade_score * 25, 1),
        "evidence_strength": round(formality_score * 15, 1),
        "period_match": round(period_score * 5, 1),
        "warnings": warnings[:5],
    }


def grade_match_score(target_level: int, supported_level: int) -> float:
    if target_level <= 0:
        return 0.45
    if supported_level <= 0:
        return 0.4
    if target_level == supported_level:
        return 1.0
    if target_level < supported_level:
        return max(0.72, 1.0 - ((supported_level - target_level) * 0.07))
    if target_level == supported_level + 1:
        return 0.45
    return 0.18


def candidate_status_for_score(score: float) -> str:
    if score > RELEVANCE_PRIMARY_THRESHOLD:
        return "Kandidat Utama"
    if score >= RELEVANCE_SUPPORTING_THRESHOLD:
        return "Kandidat Pendukung"
    if score >= 55:
        return "Perlu Reviu Manual"
    return "Tidak Direkomendasikan"


def build_reasoning_summary(classification: dict, candidates: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    for candidate in candidates:
        status = candidate.get("candidate_status") or "Belum Dinilai"
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "version": "V2.5 Reasoning Gate",
        "primary_threshold": RELEVANCE_PRIMARY_THRESHOLD,
        "supporting_threshold": RELEVANCE_SUPPORTING_THRESHOLD,
        "classification": classification,
        "status_counts": status_counts,
        "top_score": candidates[0].get("reasoning_score") if candidates else None,
        "top_status": candidates[0].get("candidate_status") if candidates else None,
        "grade_rules": GRADE_MATURITY_RULES,
        "message": "Kandidat utama hanya diberikan jika skor akhir melewati 80% dan grade tidak melampaui maturity gate E-D-C-B-A.",
    }


def apply_evidence_analysis_gate(analysis: dict | None, candidates: list[dict], classification: dict) -> dict | None:
    if not analysis:
        return analysis
    if not is_meaningful_summary(analysis.get("summary")):
        analysis["summary"] = build_evidence_summary_from_context(classification, candidates)
    analysis["reasoning_gate"] = {
        "evidence_type": classification.get("evidence_type_label"),
        "safe_grade_ceiling": classification.get("safe_grade_ceiling"),
        "chain": classification.get("chain"),
        "warnings": classification.get("warnings") or [],
    }
    allowed_primary_indexes = {index for index, candidate in enumerate(candidates) if candidate.get("primary_allowed")}
    supporting_indexes = {index for index, candidate in enumerate(candidates) if candidate.get("candidate_status") == "Kandidat Pendukung"}
    placements = analysis.get("placements") or {}
    demoted = []
    for item in list(placements.get("primary") or []):
        index = item.get("index")
        if index not in allowed_primary_indexes:
            item["role"] = "pendukung" if index in supporting_indexes else "perlu reviu"
            item["reason"] = clean_ai_text(f"{item.get('reason')} Gate V2.5: belum memenuhi kandidat utama >80%.", 180)
            demoted.append(item)
    placements["primary"] = [item for item in placements.get("primary") or [] if item.get("index") in allowed_primary_indexes]
    if demoted:
        placements["supporting"] = [*demoted, *(placements.get("supporting") or [])]
        gate_warnings = analysis["reasoning_gate"].setdefault("warnings", [])
        gate_warnings.append("Sebagian penempatan utama AI diturunkan karena skor gate tidak melewati 80%.")
    analysis["placements"] = placements
    return analysis


def apply_batch_package_gate(analysis: dict, results: list[dict]) -> dict:
    package_gate = score_batch_package(results)
    analysis["package_gate"] = package_gate
    missing = list(analysis.get("missing_evidence") or [])
    for item in package_gate.get("missing_chain") or []:
        if item not in missing:
            missing.append(item)
    analysis["missing_evidence"] = missing[:8]
    analysis["safe_grade"] = package_gate.get("safe_grade")
    return analysis


def score_batch_package(results: list[dict]) -> dict:
    chain = {stage_name: False for stage_name in EVIDENCE_STAGE_RULES}
    scores = []
    primary_count = 0
    for result in results:
        classification = (result.get("reasoning_gate") or {}).get("classification") or {}
        for stage_name, present in (classification.get("chain") or {}).items():
            if present and stage_name in chain:
                chain[stage_name] = True
        for candidate in result.get("candidates") or []:
            if candidate.get("reasoning_score") is not None:
                scores.append(float(candidate.get("reasoning_score") or 0))
            if candidate.get("primary_allowed"):
                primary_count += 1
    safe_level = 0
    if chain.get("kebijakan"):
        safe_level = 1
    if chain.get("kebijakan") and chain.get("sosialisasi"):
        safe_level = max(safe_level, 2)
    if chain.get("kebijakan") and chain.get("implementasi"):
        safe_level = max(safe_level, 3)
    if chain.get("kebijakan") and chain.get("implementasi") and chain.get("evaluasi"):
        safe_level = max(safe_level, 4)
    if chain.get("kebijakan") and chain.get("implementasi") and chain.get("evaluasi") and chain.get("perbaikan"):
        safe_level = max(safe_level, 5)
    average_score = round(sum(scores) / len(scores), 1) if scores else 0
    chain_score = round((sum(1 for present in chain.values() if present) / len(chain)) * 100, 1)
    package_score = round((average_score * 0.65) + (chain_score * 0.35), 1)
    missing_chain = []
    if not chain.get("kebijakan"):
        missing_chain.append("File kebijakan/SOP/SK belum terbaca dalam paket.")
    if safe_level < 3 and not chain.get("implementasi"):
        missing_chain.append("Bukti implementasi belum cukup kuat untuk mendukung Grade C ke atas.")
    if safe_level < 4 and not chain.get("evaluasi"):
        missing_chain.append("Bukti evaluasi berkala belum cukup kuat untuk mendukung Grade B ke atas.")
    if safe_level < 5 and not chain.get("perbaikan"):
        missing_chain.append("Bukti tindak lanjut/perbaikan berbasis evaluasi belum cukup kuat untuk Grade A.")
    return {
        "score": package_score,
        "average_candidate_score": average_score,
        "chain_score": chain_score,
        "safe_grade": GRADE_BY_STAGE.get(safe_level),
        "chain": chain,
        "primary_candidate_count": primary_count,
        "status": candidate_status_for_score(package_score),
        "missing_chain": missing_chain,
        "message": "Skor paket menggabungkan rata-rata skor kandidat dan kelengkapan rantai kebijakan-sosialisasi-implementasi-evaluasi-perbaikan.",
    }

def normalize_smart_upload_action(value: str | None) -> str:
    action = str(value or "upload_primary").strip().lower()
    if action not in SMART_UPLOAD_ACTIONS:
        raise SmartUploadError("Jenis aksi smart upload tidak valid.")
    return action


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


def clean_ai_text(value: object, max_length: int) -> str:
    text = normalize_text(str(value or ""))
    return text[:max_length]


def is_meaningful_summary(value: object) -> bool:
    text = normalize_text(str(value or ""))
    if len(text) < 60:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]", text):
        return False
    word_count = len(re.findall(r"\w+", text))
    return word_count >= 8


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


def build_evidence_summary_from_context(classification: dict, candidates: list[dict]) -> str:
    evidence_label = classification.get("evidence_type_label") or "Evidence"
    kk_label = classification.get("best_kk_label") or "konteks KK belum jelas"
    safe_grade = classification.get("safe_grade_ceiling")
    chain = classification.get("chain") or {}
    present_labels = [
        EVIDENCE_STAGE_LABELS.get(stage, stage)
        for stage, present in chain.items()
        if present
    ]
    top = candidates[0] if candidates else {}
    target = ""
    if top:
        target = f" Kandidat terkuat mengarah ke {top.get('kk_id', 'KK')} / {top.get('kode', '-')} / {top.get('detail_kode', '-')} Grade {top.get('grade', '-')}."
    if present_labels:
        chain_text = ", ".join(present_labels)
        grade_text = f" Grade aman sementara {safe_grade}." if safe_grade else " Grade aman belum dapat ditetapkan karena rantai bukti belum lengkap."
        return (
            f"Evidence terbaca sebagai {evidence_label.lower()} pada {kk_label}. "
            f"Rantai bukti yang terdeteksi: {chain_text}.{grade_text}{target}"
        )
    return (
        f"Evidence sudah terbaca, tetapi sistem belum menemukan rantai bukti maturity yang cukup jelas. "
        f"Perlu kurasi substansi dokumen sebelum dipakai sebagai kandidat utama.{target}"
    )


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


def safe_confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(max(0, min(1, float(value))), 3)


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


def interpret_ai_narrative(settings: Settings, prompt_payload: dict) -> dict:
    compact_payload = {
        "file": {
            "name": prompt_payload.get("file", {}).get("name"),
            "content_type": prompt_payload.get("file", {}).get("content_type"),
            "size_bytes": prompt_payload.get("file", {}).get("size_bytes"),
            "preview_text": clean_ai_text(prompt_payload.get("file", {}).get("preview_text"), 3200),
        },
        "preclassification": prompt_payload.get("preclassification") or {},
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
        "max_tokens": 1100,
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


class SmartUploadError(RuntimeError):
    pass


def call_chat_completion(settings: Settings, body: dict) -> dict:
    request = Request(
        chat_completion_url(settings),
        data=json.dumps(prepare_chat_body(settings, body)).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.resolved_ai_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SPIP-Evidence-Dashboard/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.ai_timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {"status": "ok", "message": "AI endpoint merespons.", "payload": json.loads(raw)}
    except HTTPError as exc:
        detail = sanitize_http_error_detail(exc.read().decode("utf-8", errors="replace"))
        status = "unavailable" if exc.code >= 500 else "error"
        return {"status": status, "message": f"AI belum tersedia dari gateway: HTTP {exc.code}. {detail}"}
    except URLError as exc:
        return {"status": "error", "message": f"AI gagal tersambung: {exc.reason}"}
    except TimeoutError:
        return {"status": "error", "message": f"AI timeout setelah {settings.ai_timeout_seconds} detik."}
    except json.JSONDecodeError as exc:
        return {"status": "error", "message": f"Respons AI bukan JSON: {exc}"}


def chat_completion_url(settings: Settings) -> str:
    base_url = settings.deepseek_base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    path = (settings.deepseek_chat_path or "/chat/completions").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def prepare_chat_body(settings: Settings, body: dict) -> dict:
    prepared = dict(body)
    prepared.setdefault("stream", False)
    thinking_mode = (settings.deepseek_thinking_mode or "").strip().lower()
    if thinking_mode in {"enabled", "disabled"}:
        prepared["thinking"] = {"type": thinking_mode}
    if thinking_mode == "enabled":
        prepared.setdefault("reasoning_effort", "high")
        for key in ("temperature", "top_p", "presence_penalty", "frequency_penalty"):
            prepared.pop(key, None)
    return prepared


def extract_preview_text(file_name: str, content_type: str | None, payload: bytes, allow_full_document: bool, analysis_mode: str = DEFAULT_ANALYSIS_MODE) -> dict:
    lowered = file_name.lower()
    extension = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    mode_key, mode_config = normalize_analysis_mode(analysis_mode)
    try:
        if extension == ".pdf" or content_type == "application/pdf":
            return extract_pdf_text(payload, mode_config)
        if extension == ".docx":
            return extract_docx_text(payload, mode_config)
        if extension == ".xlsx":
            return extract_xlsx_text(payload, mode_config)
        if extension in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
            return extract_plain_text(payload, allow_full_document, mode_config)
    except Exception as exc:  # keep upload analysis resilient for malformed files
        return {"status": "partial", "method": extension.lstrip(".") or "unknown", "text": "", "message": f"Ekstraksi gagal: {exc}"}
    return {"status": "unsupported", "method": "metadata_only", "text": "", "message": "Tipe file belum didukung untuk ekstraksi teks penuh."}


def extract_plain_text(payload: bytes, allow_full_document: bool, mode_config: dict) -> dict:
    read_limit = len(payload) if allow_full_document else min(len(payload), mode_config["read_limit"])
    decoded = normalize_text(payload[:read_limit].decode("utf-8", errors="ignore"))
    sent_text = decoded[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok",
        "method": "plain_text",
        "text": sent_text,
        "message": None,
        "extracted_char_count": len(decoded),
        "sent_char_count": len(sent_text),
    }


def extract_pdf_text(payload: bytes, mode_config: dict) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "unsupported", "method": "pdf", "text": "", "message": "Dependency pypdf belum terpasang."}
    reader = PdfReader(BytesIO(payload))
    total_pages = len(reader.pages)
    page_indexes = selected_pdf_pages(total_pages, mode_config)
    parts = []
    scanned_count = 0
    for index in page_indexes:
        parts.append(reader.pages[index].extract_text() or "")
        scanned_count += 1
        if sum(len(part) for part in parts) > mode_config["read_limit"]:
            break
    text = normalize_text(" ".join(parts))
    sent_text = text[: mode_config["prompt_char_limit"]]
    non_empty_pages = sum(1 for part in parts if normalize_text(part))
    density = round(len(text) / max(1, scanned_count), 1)
    quality_warning = None
    if total_pages >= 10 and (len(text) < 1200 or density < 80):
        quality_warning = (
            "Text layer PDF sangat rendah. Hasil rekomendasi memakai teks yang berhasil diekstrak; "
            "aktifkan OCR untuk membaca isi scan/gambar secara penuh."
        )
    return {
        "status": "ok" if text else "partial",
        "method": "pdf",
        "text": sent_text,
        "message": None if text else "PDF terbaca, tetapi teks tidak ditemukan.",
        "total_pages": total_pages,
        "scanned_pages": scanned_count,
        "scanned_text_pages": non_empty_pages,
        "text_density_chars_per_page": density,
        "page_strategy": mode_config["pdf_strategy"],
        "quality_warning": quality_warning,
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_docx_text(payload: bytes, mode_config: dict) -> dict:
    with ZipFile(BytesIO(payload)) as archive:
        xml_data = archive.read("word/document.xml")
    root = ET.fromstring(xml_data)
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    text = normalize_text(" ".join(texts))[: mode_config["read_limit"]]
    sent_text = text[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok" if text else "partial",
        "method": "docx",
        "text": sent_text,
        "message": None if text else "DOCX terbaca, tetapi teks tidak ditemukan.",
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }


def extract_xlsx_text(payload: bytes, mode_config: dict) -> dict:
    values = []
    total_sheets = 0
    scanned_sheets = 0
    try:
        with ZipFile(BytesIO(payload)) as archive:
            shared_strings = read_xlsx_shared_strings(archive)
            sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            total_sheets = len(sheet_names)
            for sheet_name in sheet_names[: mode_config["xlsx_sheet_limit"]]:
                scanned_sheets += 1
                root = ET.fromstring(archive.read(sheet_name))
                for cell in root.iter():
                    if not cell.tag.endswith("}c"):
                        continue
                    cell_type = cell.attrib.get("t")
                    value = ""
                    if cell_type == "inlineStr":
                        value = " ".join(child.text or "" for child in cell.iter() if child.tag.endswith("}t"))
                    else:
                        raw = next((child.text for child in cell if child.tag.endswith("}v")), None)
                        if raw is None:
                            continue
                        if cell_type == "s" and raw.isdigit() and int(raw) < len(shared_strings):
                            value = shared_strings[int(raw)]
                        else:
                            value = raw
                    if value:
                        values.append(value)
                    if sum(len(item) for item in values) > mode_config["read_limit"]:
                        break
                if sum(len(item) for item in values) > mode_config["read_limit"]:
                    break
    except BadZipFile as exc:
        raise ValueError("XLSX bukan arsip zip valid") from exc
    text = normalize_text(" ".join(values))
    sent_text = text[: mode_config["prompt_char_limit"]]
    return {
        "status": "ok" if text else "partial",
        "method": "xlsx",
        "text": sent_text,
        "message": None if text else "XLSX terbaca, tetapi teks tidak ditemukan.",
        "total_sheets": total_sheets,
        "scanned_sheets": scanned_sheets,
        "extracted_char_count": len(text),
        "sent_char_count": len(sent_text),
    }



def normalize_analysis_mode(value: str | None) -> tuple[str, dict]:
    key = str(value or DEFAULT_ANALYSIS_MODE).strip().lower()
    if key not in ANALYSIS_MODES:
        key = DEFAULT_ANALYSIS_MODE
    return key, ANALYSIS_MODES[key]


def normalize_candidate_limit(value: int | None, default: int) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(MIN_CANDIDATE_LIMIT, min(MAX_CANDIDATE_LIMIT, limit))


def selected_pdf_pages(total_pages: int, mode_config: dict) -> list[int]:
    if total_pages <= 0:
        return []
    limit = min(total_pages, mode_config["pdf_page_limit"])
    strategy = mode_config["pdf_strategy"]
    if strategy == "awal" or total_pages <= limit:
        return list(range(limit))
    if strategy == "berurutan":
        return list(range(limit))

    selected = set(range(min(3, total_pages)))
    tail_start = max(0, total_pages - 3)
    selected.update(range(tail_start, total_pages))
    middle = total_pages // 2
    middle_window = range(max(0, middle - 2), min(total_pages, middle + 2))
    selected.update(middle_window)
    keyword_budget = max(0, limit - len(selected))
    if keyword_budget:
        step = max(1, total_pages // max(1, keyword_budget + 1))
        for index in range(step, total_pages, step):
            selected.add(index)
            if len(selected) >= limit:
                break
    return sorted(selected)[:limit]


def estimate_token_count(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4))


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> dict:
    total = input_tokens + output_tokens
    return {
        "low": round(total * 0.00000014, 7),
        "high": round(total * 0.00000025, 7),
    }


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
        "scanned_text_pages": extraction.get("scanned_text_pages"),
        "text_density_chars_per_page": extraction.get("text_density_chars_per_page"),
        "quality_warning": extraction.get("quality_warning"),
        "candidate_scope": "Semua KK3.1-KK3.4, seluruh subunsur, detail parameter, dan grade yang tersedia.",
        "candidate_limit": candidate_limit,
        "candidate_limit_max": MAX_CANDIDATE_LIMIT,
        "candidate_pool_count": candidate_pool_count,
        "candidate_count": candidate_count,
        "final_candidate_count": final_candidate_count,
        "note": "Angka token dan biaya adalah estimasi untuk membantu memilih mode analisis.",
    }


def read_xlsx_shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings = []
    for item in root:
        if not item.tag.endswith("}si"):
            continue
        strings.append(" ".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")))
    return strings


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def sanitize_upload_filename(file_name: str) -> str:
    if "." in file_name:
        stem, ext = file_name.rsplit(".", 1)
        safe_stem = safe_segment(stem, max_length=100) or "evidence"
        safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:12]
        return f"{safe_stem}.{safe_ext}" if safe_ext else safe_stem
    return safe_segment(file_name, max_length=112) or "evidence"


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



def has_any_keyword(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


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


def natural_sort_key(value: object) -> tuple:
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple(int(part) if part.isdigit() else part for part in parts)


def grade_sort_key(grade: object) -> int:
    return {"C": 0, "E": 1, "D": 2, "B": 3, "A": 4}.get(str(grade or "").upper(), 9)


def local_candidate_sort_key(item: dict) -> tuple:
    return (
        -(item.get("confidence") or 0),
        str(item.get("kk_id") or ""),
        natural_sort_key(item.get("kode")),
        natural_sort_key(item.get("detail_kode")),
        grade_sort_key(item.get("grade")),
    )


def reasoning_candidate_sort_key(item: dict) -> tuple:
    return (
        0 if item.get("primary_allowed") else 1,
        -(item.get("reasoning_score") or 0),
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


def sanitize_http_error_detail(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = normalize_text(text)
    text = text.replace("DOCTYPE html", "").replace('html lang="en"', "")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-...", text)
    text = re.sub(r"Received API Key\s*=\s*[^,} ]+", "Received API Key = sk-...", text)
    text = re.sub(r"Key Hash \(Token\)\s*=\s*[A-Za-z0-9]+", "Key Hash (Token) = [redacted]", text)
    text = normalize_text(text)
    if not text:
        return "Gateway mengembalikan respons kosong."
    if "Internal Server Error" in text:
        return "Internal Server Error dari gateway AI."
    if "Authentication Error" in text or "Invalid proxy server token" in text:
        return "Authentication Error dari Sumopod: API key/token ditolak. Pastikan key disalin langsung dari tombol copy Sumopod dan masih aktif."
    return text[:180]
