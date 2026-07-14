from __future__ import annotations

import re

from app.legacy_document_extraction import (
    XLSX_REFERENCE_EVALUATION_KEYWORDS,
    XLSX_REFERENCE_IMPLEMENTATION_KEYWORDS,
    XLSX_REFERENCE_IMPROVEMENT_KEYWORDS,
    XLSX_REFERENCE_POLICY_KEYWORDS,
    XLSX_REFERENCE_SOCIALIZATION_KEYWORDS,
)
from app.legacy_text_utils import clean_ai_text, keyword_hits, normalize_text


MAX_LINKS_TO_REGISTER = 180


LINK_CACHE_TEXT_LIMIT = 9000


LINK_CACHE_ITEM_TEXT_LIMIT = 1200


RELEVANCE_PRIMARY_THRESHOLD = 80


RELEVANCE_SUPPORTING_THRESHOLD = 70


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


TEMPLATE_DOCUMENT_KEYWORDS = [
    "form", "formulir", "template", "format", "blanko", "kuesioner",
    "form matriks", "matriks peta risiko", "formulir pemantauan",
    "formulir pencatatan", "petunjuk pengisian", "narasi merupakan contoh",
    "merupakan contoh", "diisi pada", "nama unit pemilik risiko", "unit kerja",
    "google forms", "skip to section", "matriks petris",
]


ACTUAL_EVALUATION_KEYWORDS = [
    "laporan hasil evaluasi", "laporan evaluasi", "reviu apip",
    "review apip", "laporan reviu", "laporan monitoring", "laporan pemantauan",
    "rekap evaluasi", "berita acara evaluasi", "notulen evaluasi",
    "notulensi evaluasi", "evaluasi berkala telah", "telah dievaluasi",
    "hasil pemantauan semester", "semester i telah", "semester ii telah",
]


ACTUAL_SOCIALIZATION_KEYWORDS = [
    "undangan sosialisasi", "daftar hadir", "notulensi sosialisasi",
    "notulen sosialisasi", "materi sosialisasi", "rapat sosialisasi",
    "telah disosialisasikan", "sosialisasi telah", "bahan paparan sosialisasi",
]


ACTUAL_IMPLEMENTATION_KEYWORDS = [
    "telah disusun", "telah ditetapkan", "ditetapkan dalam", "peta risiko telah",
    "register risiko telah", "risiko telah diidentifikasi", "risiko telah dianalisis",
    "rencana tindak pengendalian telah", "rtp telah", "telah dilaksanakan",
    "telah diimplementasikan", "daftar risiko", "pemilik risiko:",
]


ACTUAL_IMPROVEMENT_KEYWORDS = [
    "tindak lanjut hasil evaluasi", "matriks tindak lanjut", "bukti perbaikan",
    "laporan tindak lanjut", "perbaikan telah", "telah ditindaklanjuti",
    "ditindaklanjuti dengan", "rencana aksi perbaikan",
    "revisi berdasarkan hasil evaluasi", "keputusan pimpinan atas hasil evaluasi",
    "perubahan proses", "laporan penyelesaian", "hasil evaluasi ditindaklanjuti",
]


STRONG_EVALUATION_PROOF_KEYWORDS = [
    "laporan hasil evaluasi", "laporan evaluasi berkala", "berita acara evaluasi",
    "notulen evaluasi", "notulensi evaluasi", "reviu apip", "review apip",
    "laporan reviu", "laporan monitoring pelaksanaan", "laporan pemantauan pelaksanaan",
    "rekap hasil evaluasi", "hasil evaluasi menunjukkan", "evaluasi berkala telah dilaksanakan",
]


STRONG_IMPROVEMENT_PROOF_KEYWORDS = [
    "tindak lanjut hasil evaluasi", "matriks tindak lanjut", "bukti perbaikan",
    "laporan tindak lanjut", "hasil evaluasi ditindaklanjuti",
    "revisi berdasarkan hasil evaluasi", "keputusan pimpinan atas hasil evaluasi",
    "perubahan proses berdasarkan evaluasi", "laporan penyelesaian tindak lanjut",
]


STRONG_IMPLEMENTATION_PROOF_KEYWORDS = [
    "peta risiko telah ditetapkan", "register risiko telah", "risiko telah diidentifikasi",
    "risiko telah dianalisis", "rencana tindak pengendalian telah", "rtp telah",
    "telah disusun", "telah ditetapkan", "telah dilaksanakan", "telah diimplementasikan",
    "daftar risiko", "pemilik risiko:",
]


FILLED_RISK_MATRIX_KEYWORDS = [
    "pemilik risiko:", "pernyataan risiko", "risiko residual", "keputusan mitigasi",
    "rencana tindak pengendalian", "level risiko", "skala risiko", "pengendalian yang ada",
    "uraian risiko", "penyebab risiko", "dampak risiko", "prioritas risiko",
    "mitigasi risiko", "register risiko", "kategori risiko", "nilai risiko",
    "selera risiko", "peta risiko strategis", "peta risiko operasional",
]


FORM_ONLY_INDICATORS = [
    "petunjuk pengisian", "narasi merupakan contoh", "google forms", "skip to section",
    "mark only one oval", "nama*", "nip*", "jabatan*", "dropdown", "pilih salah satu",
    "contoh pengisian", "unit pemilik risiko", "email*", "required question",
]


FORM_COMPLETION_PLACEHOLDER_PATTERNS = [
    r"[.\u2026]{3,}",
    r"\bxxx\b",
    r"tahun\s*[.\u2026]{2,}",
    r":\s*[.\u2026]{2,}",
    r"narasi merupakan contoh",
    r"hanya .* ter tagging",
    r"diisi pada",
    r"skip to section",
]


def regex_hits(text: str, patterns: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for pattern in patterns:
        if re.search(pattern, lowered) and pattern not in hits:
            hits.append(pattern)
    return hits


def unique_hits(*groups: list[str], limit: int = 12) -> list[str]:
    hits: list[str] = []
    for group in groups:
        for item in group or []:
            if item not in hits:
                hits.append(item)
            if len(hits) >= limit:
                return hits
    return hits


def hit_score(hits: list[str], denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, len(hits or []) / denominator)


def detect_template_document(source_text: str, file_name: str) -> dict:
    template_hits = keyword_hits(source_text, TEMPLATE_DOCUMENT_KEYWORDS)
    placeholder_hits = regex_hits(source_text, FORM_COMPLETION_PLACEHOLDER_PATTERNS)
    form_only_hits = keyword_hits(source_text, FORM_ONLY_INDICATORS)
    actual_socialization_hits = keyword_hits(source_text, ACTUAL_SOCIALIZATION_KEYWORDS)
    actual_implementation_hits = keyword_hits(source_text, ACTUAL_IMPLEMENTATION_KEYWORDS)
    actual_evaluation_hits = keyword_hits(source_text, ACTUAL_EVALUATION_KEYWORDS)
    actual_improvement_hits = keyword_hits(source_text, ACTUAL_IMPROVEMENT_KEYWORDS)
    filled_risk_matrix_hits = keyword_hits(source_text, FILLED_RISK_MATRIX_KEYWORDS)
    actuality_hits = unique_hits(
        actual_socialization_hits,
        actual_implementation_hits,
        actual_evaluation_hits,
        actual_improvement_hits,
    )
    lowered_name = file_name.lower()
    template_name_markers = ("form", "formulir", "template", "matriks", "kuesioner")
    name_says_template = any(
        marker in lowered_name
        for marker in template_name_markers
    )
    form_matrix_like = name_says_template or bool(template_hits or form_only_hits)
    template_score = min(
        1.0,
        (
            len(template_hits)
            + len(placeholder_hits)
            + len(form_only_hits)
            + (2 if name_says_template else 0)
        )
        / 8,
    )
    filled_matrix_bonus = min(2, len(filled_risk_matrix_hits) // 4)
    if form_matrix_like and (template_hits or placeholder_hits or form_only_hits):
        filled_matrix_bonus = min(1, filled_matrix_bonus)
    actual_signal_score = min(
        1.0,
        (len(actuality_hits) + filled_matrix_bonus)
        / 10,
    )
    is_template = bool(template_hits or placeholder_hits or form_only_hits or name_says_template)
    has_strong_actual_artifact = bool(
        keyword_hits(source_text, STRONG_EVALUATION_PROOF_KEYWORDS)
        or keyword_hits(source_text, STRONG_IMPROVEMENT_PROOF_KEYWORDS)
    )
    is_blank_or_instructional = is_template and (
        bool(placeholder_hits)
        or bool(form_only_hits)
        or (name_says_template and actual_signal_score < 0.35)
        or (
            form_matrix_like
            and not has_strong_actual_artifact
            and actual_signal_score < 0.55
        )
    )
    return {
        "is_template": is_template,
        "is_blank_or_instructional": is_blank_or_instructional,
        "form_matrix_like": form_matrix_like,
        "template_score": round(template_score, 3),
        "actuality_score": round(actual_signal_score, 3),
        "template_hits": template_hits[:8],
        "placeholder_hits": placeholder_hits[:8],
        "form_only_hits": form_only_hits[:8],
        "actual_socialization_hits": actual_socialization_hits[:8],
        "actual_implementation_hits": actual_implementation_hits[:8],
        "actual_evaluation_hits": actual_evaluation_hits[:8],
        "actual_improvement_hits": actual_improvement_hits[:8],
        "filled_risk_matrix_hits": filled_risk_matrix_hits[:8],
        "actuality_hits": actuality_hits[:8],
    }


def build_document_profile(extraction: dict | None, file_name: str, template_guard: dict) -> dict:
    extraction = extraction or {}
    method = extraction.get("method") or ("." + file_name.rsplit(".", 1)[-1] if "." in file_name else "metadata")
    if template_guard.get("is_blank_or_instructional"):
        document_kind = "form_template"
        document_kind_label = "Form/Template atau instrumen pengisian"
    elif template_guard.get("is_template"):
        document_kind = "structured_matrix"
        document_kind_label = "Form/Matriks terstruktur"
    else:
        document_kind = "evidence_document"
        document_kind_label = "Dokumen evidence"
    return {
        "method": method,
        "document_kind": document_kind,
        "document_kind_label": document_kind_label,
        "total_pages": extraction.get("total_pages"),
        "scanned_pages": extraction.get("scanned_pages"),
        "total_sheets": extraction.get("total_sheets"),
        "scanned_sheets": extraction.get("scanned_sheets"),
        "total_slides": extraction.get("total_slides"),
        "scanned_slides": extraction.get("scanned_slides"),
        "total_rows": extraction.get("total_rows"),
        "scanned_rows": extraction.get("scanned_rows"),
        "evidence_row_count": extraction.get("evidence_row_count"),
        "hyperlink_count": extraction.get("hyperlink_count"),
        "structural_summary": extraction.get("structural_summary"),
        "page_summaries": (extraction.get("page_summaries") or [])[:8],
        "section_summaries": (extraction.get("section_summaries") or [])[:8],
        "sheet_summaries": (extraction.get("sheet_summaries") or [])[:8],
        "slide_summaries": (extraction.get("slide_summaries") or [])[:8],
        "evidence_rows": (extraction.get("evidence_rows") or [])[:12],
        "evidence_links": (extraction.get("evidence_links") or [])[:16],
        "template_score": template_guard.get("template_score"),
        "actuality_score": template_guard.get("actuality_score"),
    }


def compact_document_profile(profile: dict | None) -> dict:
    profile = profile or {}
    return {
        "method": profile.get("method"),
        "document_kind": profile.get("document_kind"),
        "document_kind_label": profile.get("document_kind_label"),
        "total_pages": profile.get("total_pages"),
        "scanned_pages": profile.get("scanned_pages"),
        "total_sheets": profile.get("total_sheets"),
        "scanned_sheets": profile.get("scanned_sheets"),
        "total_slides": profile.get("total_slides"),
        "scanned_slides": profile.get("scanned_slides"),
        "total_rows": profile.get("total_rows"),
        "scanned_rows": profile.get("scanned_rows"),
        "evidence_row_count": profile.get("evidence_row_count"),
        "hyperlink_count": profile.get("hyperlink_count"),
        "structural_summary": profile.get("structural_summary"),
        "page_summaries": (profile.get("page_summaries") or [])[:6],
        "section_summaries": (profile.get("section_summaries") or [])[:6],
        "sheet_summaries": (profile.get("sheet_summaries") or [])[:6],
        "slide_summaries": (profile.get("slide_summaries") or [])[:6],
        "evidence_rows": (profile.get("evidence_rows") or [])[:8],
        "evidence_links": (profile.get("evidence_links") or [])[:10],
        "template_score": profile.get("template_score"),
        "actuality_score": profile.get("actuality_score"),
    }


def build_extraction_evidence_text(extraction: dict | None) -> str:
    extraction = extraction or {}
    rows = extraction.get("evidence_rows") or []
    links = extraction.get("evidence_links") or []
    values: list[str] = []
    for row in rows[:140]:
        if not isinstance(row, dict):
            continue
        values.append(str(row.get("text") or row.get("sample") or ""))
        hints = row.get("stage_hints") or []
        if hints:
            values.append(" ".join(str(item) for item in hints))
    for link in links[:180]:
        if not isinstance(link, dict):
            continue
        values.append(
            " ".join(
                str(link.get(key) or "")
                for key in ("sheet", "cell", "label", "url", "context")
            )
        )
        hints = link.get("stage_hints") or []
        if hints:
            values.append(" ".join(str(item) for item in hints))
    for item in (extraction.get("linked_evidence_cache") or [])[:60]:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        values.append(" ".join(str(item.get(key) or "") for key in ("label", "context", "title", "summary")))
        if item.get("text"):
            values.append(clean_ai_text(str(item.get("text") or ""), LINK_CACHE_ITEM_TEXT_LIMIT))
    return normalize_text(" ".join(values))


def classify_reference_stage_hits(reference_text: str) -> dict[str, list[str]]:
    return {
        "kebijakan": keyword_hits(reference_text, XLSX_REFERENCE_POLICY_KEYWORDS)[:8],
        "sosialisasi": keyword_hits(reference_text, XLSX_REFERENCE_SOCIALIZATION_KEYWORDS)[:8],
        "implementasi": keyword_hits(reference_text, XLSX_REFERENCE_IMPLEMENTATION_KEYWORDS)[:8],
        "evaluasi": keyword_hits(reference_text, XLSX_REFERENCE_EVALUATION_KEYWORDS)[:8],
        "perbaikan": keyword_hits(reference_text, XLSX_REFERENCE_IMPROVEMENT_KEYWORDS)[:8],
    }


def extract_external_evidence_links(extraction: dict | None) -> list[dict]:
    links = []
    for link in (extraction or {}).get("evidence_links") or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            links.append(link)
    return links


def build_cached_evidence_link_text(items: list[dict], limit: int = LINK_CACHE_TEXT_LIMIT) -> str:
    chunks = []
    for item in items[:24]:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        chunk = normalize_text(
            " ".join(str(item.get(key) or "") for key in ("label", "context", "title", "summary", "text"))
        )
        if chunk:
            chunks.append(chunk)
    return clean_ai_text(" ".join(chunks), limit)


def merge_stage_hit_dicts(base: dict | None, extra: dict | None, limit: int = 8) -> dict[str, list[str]]:
    merged = {str(key): list(value or []) for key, value in (base or {}).items()}
    for stage, hits in (extra or {}).items():
        if isinstance(hits, str):
            hits = [hits]
        merged[str(stage)] = unique_hits(
            merged.get(str(stage), []),
            [str(item) for item in hits if str(item).strip()],
            limit=limit,
        )
    return merged


def evidence_link_stage_hits(extraction: dict | None) -> dict[str, list[str]]:
    links = extract_external_evidence_links(extraction)
    text = normalize_text(
        " ".join(
            " ".join(
                str(link.get(key) or "")
                for key in ("sheet", "cell", "label", "url", "context")
            )
            for link in links
        )
    )
    hits = classify_reference_stage_hits(text)
    for link in links:
        for hint in link.get("stage_hints") or []:
            normalized_hint = str(hint).strip().lower()
            if normalized_hint in hits:
                hits[normalized_hint] = unique_hits(hits[normalized_hint], [normalized_hint], limit=8)
    for item in (extraction or {}).get("linked_evidence_cache") or []:
        if not isinstance(item, dict) or item.get("status") != "ok":
            continue
        cached_text = normalize_text(
            " ".join(str(item.get(key) or "") for key in ("label", "context", "title", "summary", "text"))
        )
        hits = merge_stage_hit_dicts(hits, classify_reference_stage_hits(cached_text))
        hits = merge_stage_hit_dicts(hits, item.get("stage_hits") or {})
    return hits


def has_actual_xlsx_evidence_reference(extraction: dict | None, template_guard: dict) -> bool:
    external_links = extract_external_evidence_links(extraction)
    if external_links:
        return True
    if template_guard.get("is_blank_or_instructional"):
        return False
    actual_hits = unique_hits(
        template_guard.get("actual_socialization_hits") or [],
        template_guard.get("actual_implementation_hits") or [],
        template_guard.get("actual_evaluation_hits") or [],
        template_guard.get("actual_improvement_hits") or [],
        template_guard.get("filled_risk_matrix_hits") or [],
        limit=24,
    )
    return bool(actual_hits)


def classify_evidence_context(file_name: str, preview_text: str, extraction: dict | None = None) -> dict:
    evidence_reference_text = build_extraction_evidence_text(extraction)
    source_text = normalize_text(f"{file_name} {preview_text} {evidence_reference_text}")
    template_guard = detect_template_document(source_text, file_name)
    document_profile = build_document_profile(extraction, file_name, template_guard)
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

    broad_stage_hits = {}
    for stage_name, rule in EVIDENCE_STAGE_RULES.items():
        broad_stage_hits[stage_name] = keyword_hits(source_text, rule["keywords"])[:8]

    reference_stage_hits = classify_reference_stage_hits(evidence_reference_text)
    link_stage_hits = evidence_link_stage_hits(extraction)
    external_evidence_links = extract_external_evidence_links(extraction)
    has_actual_evidence_reference = has_actual_xlsx_evidence_reference(extraction, template_guard)
    reference_only_template = bool(
        template_guard.get("is_blank_or_instructional")
        and not has_actual_evidence_reference
    )

    policy_hits = unique_hits(broad_stage_hits.get("kebijakan", []), reference_stage_hits.get("kebijakan", []), limit=8)
    if template_guard.get("is_blank_or_instructional") and not policy_hits:
        policy_hits = []
    if reference_only_template and not policy_hits and template_guard.get("form_matrix_like"):
        policy_hits = ["form/matriks acuan"]
    socialization_hits = unique_hits(
        template_guard.get("actual_socialization_hits") or [],
        link_stage_hits.get("sosialisasi", []) if external_evidence_links else [],
        reference_stage_hits.get("sosialisasi", []) if not reference_only_template else [],
        limit=8,
    )
    implementation_hits = unique_hits(
        template_guard.get("actual_implementation_hits") or [],
        link_stage_hits.get("implementasi", []) if external_evidence_links else [],
        reference_stage_hits.get("implementasi", []) if not reference_only_template else [],
        limit=8,
    )
    if template_guard.get("is_blank_or_instructional"):
        implementation_hits = unique_hits(implementation_hits)
    else:
        implementation_hits = unique_hits(
            implementation_hits,
            template_guard.get("filled_risk_matrix_hits") or [],
            broad_stage_hits.get("implementasi", []),
            reference_stage_hits.get("implementasi", []),
        )
    evaluation_hits = unique_hits(
        template_guard.get("actual_evaluation_hits") or [],
        link_stage_hits.get("evaluasi", []) if external_evidence_links else [],
        reference_stage_hits.get("evaluasi", []) if not reference_only_template else [],
        limit=8,
    )
    improvement_hits = unique_hits(
        template_guard.get("actual_improvement_hits") or [],
        link_stage_hits.get("perbaikan", []) if external_evidence_links else [],
        reference_stage_hits.get("perbaikan", []) if not reference_only_template else [],
        limit=8,
    )
    template_like = bool(template_guard.get("is_template"))
    strong_implementation_hits = keyword_hits(source_text, STRONG_IMPLEMENTATION_PROOF_KEYWORDS)
    strong_evaluation_hits = keyword_hits(source_text, STRONG_EVALUATION_PROOF_KEYWORDS)
    strong_improvement_hits = keyword_hits(source_text, STRONG_IMPROVEMENT_PROOF_KEYWORDS)
    if template_like:
        actuality_score = float(template_guard.get("actuality_score") or 0)
        ref_eval_hits = unique_hits(
            link_stage_hits.get("evaluasi", []) if external_evidence_links else [],
            reference_stage_hits.get("evaluasi", []) if not reference_only_template else [],
            limit=8,
        )
        ref_improvement_hits = unique_hits(
            link_stage_hits.get("perbaikan", []) if external_evidence_links else [],
            reference_stage_hits.get("perbaikan", []) if not reference_only_template else [],
            limit=8,
        )
        if reference_only_template:
            socialization_hits = []
            implementation_hits = []
            evaluation_hits = []
            improvement_hits = []
        elif strong_implementation_hits or reference_stage_hits.get("implementasi") or link_stage_hits.get("implementasi"):
            implementation_hits = unique_hits(implementation_hits, strong_implementation_hits, reference_stage_hits.get("implementasi", []), limit=8)
        if (
            strong_evaluation_hits
            and actuality_score >= 0.55
            and has_actual_evaluation_artifact(source_text, file_name, template_guard)
        ) or ref_eval_hits:
            evaluation_hits = unique_hits(strong_evaluation_hits, ref_eval_hits, evaluation_hits, limit=8)
        else:
            evaluation_hits = []
        if (
            (
                strong_evaluation_hits
                and strong_improvement_hits
                and actuality_score >= 0.7
                and has_actual_evaluation_artifact(source_text, file_name, template_guard)
                and has_actual_improvement_artifact(source_text, file_name, template_guard)
            )
            or (ref_eval_hits and ref_improvement_hits)
        ):
            improvement_hits = unique_hits(strong_improvement_hits, ref_improvement_hits, improvement_hits, limit=8)
        else:
            improvement_hits = []
    template_guard["has_external_evidence_links"] = bool(external_evidence_links)
    template_guard["external_evidence_link_count"] = len(external_evidence_links)
    template_guard["has_actual_evidence_reference"] = has_actual_evidence_reference
    template_guard["reference_only_template"] = reference_only_template
    stage_hits = {
        "kebijakan": policy_hits[:8],
        "sosialisasi": socialization_hits[:8],
        "implementasi": implementation_hits[:8],
        "evaluasi": evaluation_hits[:8],
        "perbaikan": improvement_hits[:8],
    }
    evidence_types = [stage_name for stage_name in EVIDENCE_STAGE_RULES if stage_hits.get(stage_name)]
    detected_stage = 0
    for stage_name, hits in broad_stage_hits.items():
        if hits:
            detected_stage = max(detected_stage, GRADE_ORDER[EVIDENCE_STAGE_RULES[stage_name]["grade"]])
    evidence_type = evidence_types[-1] if evidence_types else "tidak_terklasifikasi"
    chain = {stage_name: bool(stage_hits.get(stage_name)) for stage_name in EVIDENCE_STAGE_RULES}
    safe_grade = determine_safe_grade_from_chain(chain)
    safe_stage = GRADE_ORDER.get(safe_grade or "", 0)
    formality_hits = keyword_hits(source_text, FORMALITY_KEYWORDS)
    period_hits = keyword_hits(source_text, PERIOD_KEYWORDS)
    formality_score = min(1.0, 0.35 + (len(formality_hits) * 0.16)) if formality_hits else 0.35
    period_score = min(1.0, 0.55 + (len(period_hits) * 0.18)) if period_hits else 0.45
    warnings = []
    if template_guard.get("is_template"):
        warnings.append(
            "Dokumen terdeteksi sebagai form/template/instrumen. Istilah evaluasi, pemantauan, updating, atau perbaikan di header/petunjuk tidak dihitung sebagai bukti Grade B/A."
        )
        warnings.append(
            "Grade B/A tetap membutuhkan laporan evaluasi berkala dan bukti tindak lanjut/perbaikan yang benar-benar terjadi."
        )
        if (
            broad_stage_hits.get("evaluasi")
            and not strong_evaluation_hits
            and not link_stage_hits.get("evaluasi")
        ):
            warnings.append("Indikasi evaluasi pada form/template tidak cukup kuat karena belum ada bukti laporan atau berita acara evaluasi.")
        if (
            broad_stage_hits.get("perbaikan")
            and not strong_improvement_hits
            and not link_stage_hits.get("perbaikan")
        ):
            warnings.append("Indikasi perbaikan pada form/template tidak cukup kuat karena belum ada bukti tindak lanjut hasil evaluasi.")
    if template_guard.get("is_blank_or_instructional") and reference_only_template:
        warnings.append("Form/instrumen terlihat belum cukup membuktikan aktivitas nyata; kandidat utama perlu dibatasi sampai ada isi pelaksanaan yang kuat.")
    if reference_only_template:
        warnings.append(
            "Form/matriks acuan belum memiliki tautan evidence atau progres aktual yang terbaca; sistem tidak menaikkan grade dari istilah pada header/petunjuk."
        )
    elif external_evidence_links:
        warnings.append(
            f"Terdapat {len(external_evidence_links)} tautan evidence/RTP yang dibaca sebagai bukti aktual dari workbook."
        )
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
        "document_kind": document_profile["document_kind"],
        "document_kind_label": document_profile["document_kind_label"],
        "document_profile": document_profile,
        "template_guard": template_guard,
        "detected_stage_level": detected_stage,
        "stage_level": safe_stage,
        "safe_grade_ceiling": safe_grade,
        "grade_maturity_rules": GRADE_MATURITY_RULES,
        "stage_hits": stage_hits,
        "broad_stage_hits": broad_stage_hits,
        "reference_stage_hits": reference_stage_hits,
        "link_stage_hits": link_stage_hits,
        "evidence_reference_char_count": len(evidence_reference_text),
        "evidence_reference_count": len((extraction or {}).get("evidence_rows") or []),
        "external_evidence_link_count": len(external_evidence_links),
        "strong_stage_hits": {
            "implementasi": strong_implementation_hits[:8],
            "evaluasi": strong_evaluation_hits[:8],
            "perbaikan": strong_improvement_hits[:8],
        },
        "chain": chain,
        "formality_score": round(formality_score, 3),
        "formality_hits": formality_hits[:8],
        "period_score": round(period_score, 3),
        "period_hits": period_hits[:8],
        "warnings": warnings,
    }


def has_actual_evaluation_artifact(source_text: str, file_name: str, template_guard: dict) -> bool:
    if template_guard.get("is_blank_or_instructional"):
        return False
    title_zone = normalize_text(f"{file_name} {source_text[:2200]}").lower()
    if template_guard.get("is_template"):
        strict_artifact_markers = (
            "laporan hasil evaluasi",
            "laporan evaluasi berkala",
            "berita acara evaluasi",
            "notulen evaluasi",
            "notulensi evaluasi",
            "reviu apip",
            "review apip",
            "laporan reviu",
            "rekap hasil evaluasi",
            "evaluasi berkala telah dilaksanakan",
        )
        return any(marker in title_zone for marker in strict_artifact_markers)
    artifact_markers = (
        "laporan hasil evaluasi",
        "laporan evaluasi",
        "laporan reviu",
        "reviu apip",
        "review apip",
        "berita acara evaluasi",
        "notulen evaluasi",
        "notulensi evaluasi",
        "rekap hasil evaluasi",
        "laporan monitoring",
        "laporan pemantauan",
        "hasil pemantauan",
    )
    if not any(marker in title_zone for marker in artifact_markers):
        return False
    supporting_markers = ("nomor", "tanggal", "ditandatangani", "hasil", "telah", "semester", "triwulan", "berkala")
    return any(marker in title_zone for marker in supporting_markers)


def has_actual_improvement_artifact(source_text: str, file_name: str, template_guard: dict) -> bool:
    if template_guard.get("is_blank_or_instructional"):
        return False
    title_zone = normalize_text(f"{file_name} {source_text[:2600]}").lower()
    if template_guard.get("is_template"):
        strict_artifact_markers = (
            "tindak lanjut hasil evaluasi",
            "hasil evaluasi ditindaklanjuti",
            "revisi berdasarkan hasil evaluasi",
            "keputusan pimpinan atas hasil evaluasi",
            "perubahan proses berdasarkan evaluasi",
            "laporan penyelesaian tindak lanjut",
        )
        if not any(marker in title_zone for marker in strict_artifact_markers):
            return False
        return any(marker in title_zone for marker in ("hasil evaluasi", "reviu", "review", "evaluasi berkala"))
    artifact_markers = (
        "tindak lanjut hasil evaluasi",
        "hasil evaluasi ditindaklanjuti",
        "matriks tindak lanjut",
        "laporan tindak lanjut",
        "bukti perbaikan",
        "rencana aksi perbaikan",
        "revisi berdasarkan hasil evaluasi",
        "keputusan pimpinan atas hasil evaluasi",
        "laporan penyelesaian tindak lanjut",
    )
    if not any(marker in title_zone for marker in artifact_markers):
        return False
    evaluation_anchor = ("hasil evaluasi", "laporan evaluasi", "reviu", "review", "pemantauan", "monitoring")
    return any(marker in title_zone for marker in evaluation_anchor)


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
        if scorecard.get("primary_blocked"):
            item["candidate_status"] = "Perlu Reviu Manual"
        item["primary_allowed"] = scorecard["score"] > RELEVANCE_PRIMARY_THRESHOLD and not scorecard.get("primary_blocked")
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
    primary_blocked = False
    chain = classification.get("chain") or {}
    stage_hits = classification.get("stage_hits") or {}
    template_guard = classification.get("template_guard") or {}
    document_kind = classification.get("document_kind")
    template_like = bool(template_guard.get("is_template")) or document_kind in {"form_template", "structured_matrix"}

    maturity_block_reasons = []
    if target_level >= GRADE_ORDER["D"] and not (chain.get("kebijakan") and chain.get("sosialisasi")):
        maturity_block_reasons.append("Grade D membutuhkan kebijakan yang sudah disosialisasikan.")
    if target_level >= GRADE_ORDER["C"] and not (chain.get("kebijakan") and chain.get("implementasi")):
        maturity_block_reasons.append("Grade C membutuhkan kebijakan yang sudah diimplementasikan.")
    if target_level >= GRADE_ORDER["B"] and not (chain.get("kebijakan") and chain.get("implementasi") and chain.get("evaluasi")):
        maturity_block_reasons.append("Grade B membutuhkan kebijakan/pelaksanaan yang sudah dievaluasi berkala.")
    if target_level >= GRADE_ORDER["A"] and not (
        chain.get("kebijakan") and chain.get("implementasi") and chain.get("evaluasi") and chain.get("perbaikan")
    ):
        maturity_block_reasons.append("Grade A membutuhkan hasil evaluasi yang sudah menjadi bahan perbaikan organisasi.")

    if target_level and target_level > supported_level:
        primary_blocked = True
        score = min(score, RELEVANCE_SUPPORTING_THRESHOLD - 1)
        warnings.append(f"Target Grade {target_grade} melebihi grade aman {safe_grade or '-'} berdasarkan rantai bukti.")

    if maturity_block_reasons:
        primary_blocked = True
        score = min(score, RELEVANCE_SUPPORTING_THRESHOLD - 1)
        warnings.extend(maturity_block_reasons)

    is_unfilled_template = (
        classification.get("document_kind") == "form_template"
        and not stage_hits.get("implementasi")
        and not stage_hits.get("evaluasi")
        and not stage_hits.get("perbaikan")
    )
    if is_unfilled_template:
        primary_blocked = True
        score = min(score, RELEVANCE_SUPPORTING_THRESHOLD - 1)
        warnings.append("Form/template yang belum terbukti terisi sebagai pelaksanaan nyata tidak dapat menjadi kandidat utama.")
    if template_like:
        if target_level >= GRADE_ORDER["B"] and not stage_hits.get("evaluasi"):
            primary_blocked = True
            score = min(score, RELEVANCE_SUPPORTING_THRESHOLD - 1)
            warnings.append("Header/sheet/kolom form tidak membuktikan evaluasi berkala; perlu laporan atau berita acara evaluasi.")
        if target_level >= GRADE_ORDER["A"] and not stage_hits.get("perbaikan"):
            primary_blocked = True
            score = min(score, RELEVANCE_SUPPORTING_THRESHOLD - 1)
            warnings.append("Header/sheet/kolom form tidak membuktikan perbaikan organisasi; perlu tindak lanjut hasil evaluasi.")
    if score <= RELEVANCE_PRIMARY_THRESHOLD:
        warnings.append("Skor belum melewati ambang kandidat utama >80%.")
    return {
        "score": score,
        "primary_blocked": primary_blocked,
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


def is_meaningful_summary(value: object) -> bool:
    text = normalize_text(str(value or ""))
    if len(text) < 60:
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]", text):
        return False
    word_count = len(re.findall(r"\w+", text))
    return word_count >= 8


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
    if classification.get("document_kind") == "form_template":
        grade_text = f" Grade aman sementara dibatasi pada {safe_grade}." if safe_grade else " Grade aman belum dapat ditetapkan karena dokumen masih berupa format/acuan."
        return (
            "Evidence ini terdeteksi sebagai form, template, atau instrumen kerja, bukan laporan pelaksanaan atau evaluasi final. "
            "Dokumen dapat dipakai sebagai acuan format atau pendukung implementasi bila sudah terisi, tetapi istilah pemantauan, updating, evaluasi, rencana tindak, atau perbaikan di dalam header dan petunjuk tidak otomatis membuktikan Grade B atau Grade A. "
            f"{grade_text} Untuk naik ke Grade B/A tetap diperlukan laporan evaluasi berkala dan bukti tindak lanjut/perbaikan yang benar-benar terjadi.{target}"
        )
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


def safe_confidence(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(max(0, min(1, float(value))), 3)


def natural_sort_key(value: object) -> tuple:
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple(int(part) if part.isdigit() else part for part in parts)


def grade_sort_key(grade: object) -> int:
    return {"C": 0, "E": 1, "D": 2, "B": 3, "A": 4}.get(str(grade or "").upper(), 9)


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
