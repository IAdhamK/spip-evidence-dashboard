from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app.config import Settings
from app.database import Database
from app.evidence_structure import safe_segment
from app.webdav_client import PublicShareWebDavClient, public_folder_link


STOPWORDS = {
    "ada", "atau", "atas", "bagi", "bahwa", "dalam", "dan", "dapat",
    "dengan", "di", "ini", "itu", "ke", "kepada", "oleh", "pada", "para",
    "telah", "untuk", "yang",
}

TEXT_EXTENSIONS = {".csv", ".htm", ".html", ".json", ".md", ".rtf", ".text", ".txt", ".xml"}
PREVIEW_LIMIT = 1800
READ_LIMIT = 24000


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

    def recommend(self, file_name: str, content_type: str | None, payload: bytes) -> dict:
        extraction = extract_preview_text(file_name, content_type, payload, self.settings.ai_send_full_document)
        candidates = self._local_candidates(file_name, extraction["text"])
        ai_result = self._rerank_with_ai(file_name, content_type, len(payload), extraction["text"], candidates)
        if ai_result["status"] == "ok":
            candidates = merge_ai_result(candidates, ai_result.get("candidates", []))

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
            "extraction": {
                "status": extraction["status"],
                "method": extraction["method"],
                "message": extraction.get("message"),
            },
            "candidates": candidates,
            "ai": {
                "status": ai_result["status"],
                "message": ai_result.get("message"),
                "provider": self.settings.ai_provider,
                "model": self.settings.deepseek_model,
            },
            "upload": {
                "allow_real_upload": self.settings.smart_upload_allow_real_upload,
                "require_confirmation": self.settings.smart_upload_require_confirmation,
            },
        }

    def confirm_upload(self, review_id: int, candidate_index: int) -> dict:
        if not self.settings.smart_upload_allow_real_upload:
            raise SmartUploadError("Upload sungguhan masih dikunci oleh SMART_UPLOAD_ALLOW_REAL_UPLOAD=false.")
        if not self.settings.has_share_token:
            raise SmartUploadError("LUMBUNG_SHARE_TOKEN belum tersedia untuk upload WebDAV.")

        review = self.db.smart_upload_review(review_id)
        if not review:
            raise SmartUploadError("Review upload tidak ditemukan.")
        if review.get("upload_status") == "uploaded":
            raise SmartUploadError("Review ini sudah pernah dikonfirmasi upload.")

        candidates = json.loads(review.get("candidates_json") or "[]")
        if candidate_index < 0 or candidate_index >= len(candidates):
            raise SmartUploadError("Pilihan kandidat tidak valid.")
        candidate = candidates[candidate_index]
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
        folder_url = public_folder_link(self.settings.lumbung_host, self.settings.lumbung_share_token, candidate["folder_path"])
        upload_message = f"Uploaded to {remote_path}"
        confirmed_candidate = {**candidate, "uploaded_file_name": file_name, "remote_path": remote_path, "public_url": folder_url}
        self.db.mark_smart_upload_confirmed(review_id, confirmed_candidate, "uploaded", upload_message)
        return {
            "review_id": review_id,
            "status": "uploaded",
            "message": upload_message,
            "candidate": confirmed_candidate,
        }

    def test_ai_connection(self) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia."}
        body = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Balas JSON murni."},
                {"role": "user", "content": "{\"ping\":true}. Balas {\"ok\":true}."},
            ],
        }
        result = call_chat_completion(self.settings, body)
        if result["status"] != "ok":
            return result
        try:
            content = result["payload"]["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            return {"status": "ok", "message": "AI endpoint merespons.", "response": parsed}
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            return {"status": "error", "message": f"AI merespons tetapi format tidak sesuai: {exc}"}

    def _local_candidates(self, file_name: str, preview_text: str) -> list[dict]:
        query_tokens = tokenize(f"{file_name} {preview_text}") or tokenize(file_name)
        scored: list[dict] = []
        for seed in self._candidate_seeds():
            corpus_tokens = tokenize(seed.corpus)
            overlap = sorted(query_tokens & corpus_tokens)
            if not overlap:
                continue
            score = score_candidate(query_tokens, corpus_tokens, seed, file_name)
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
        scored.sort(key=lambda item: item["confidence"], reverse=True)
        return scored[: max(1, self.settings.ai_max_candidates)]

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
    ) -> dict:
        if not self.settings.ai_reasoning_enabled:
            return {"status": "skipped", "message": "AI reasoning belum diaktifkan."}
        if not self.settings.has_ai_key:
            return {"status": "skipped", "message": "API key AI belum tersedia di environment DEV."}
        if not candidates:
            return {"status": "skipped", "message": "Tidak ada kandidat lokal untuk direrank oleh AI."}

        prompt_payload = {
            "file": {"name": file_name, "content_type": content_type, "size_bytes": size_bytes, "preview_text": preview_text[:PREVIEW_LIMIT]},
            "candidates": [
                {
                    "index": index,
                    "kk_id": item["kk_id"],
                    "kode": item["kode"],
                    "detail_kode": item["detail_kode"],
                    "grade": item["grade"],
                    "subunsur_name": item["subunsur_name"],
                    "uraian": item["uraian"],
                    "kriteria": item["kriteria"][:600],
                    "penjelasan": item["penjelasan"][:600],
                }
                for index, item in enumerate(candidates)
            ],
        }
        body = {
            "model": self.settings.deepseek_model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Anda adalah asisten kurasi evidence SPIP. Pilih ulang kandidat hanya dari daftar yang diberikan. "
                        "Jangan membuat KK, subunsur, detail, atau grade baru. Balas JSON murni dengan bentuk "
                        "{\"candidates\":[{\"index\":0,\"confidence\":0.8,\"reason\":\"...\"}],\"message\":\"...\"}."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
        }
        result = call_chat_completion(self.settings, body)
        if result["status"] != "ok":
            return result
        try:
            content = result["payload"]["choices"][0]["message"]["content"]
            parsed = parse_json_object(content)
            return {
                "status": "ok",
                "message": parsed.get("message") or "AI selesai mererank kandidat lokal.",
                "candidates": parsed.get("candidates") or [],
            }
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            return {"status": "error", "message": f"Respons AI tidak sesuai format JSON: {exc}"}


class SmartUploadError(RuntimeError):
    pass


def call_chat_completion(settings: Settings, body: dict) -> dict:
    request = Request(
        settings.deepseek_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.deepseek_api_key}",
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
        return {"status": "error", "message": "AI timeout."}
    except json.JSONDecodeError as exc:
        return {"status": "error", "message": f"Respons AI bukan JSON: {exc}"}


def extract_preview_text(file_name: str, content_type: str | None, payload: bytes, allow_full_document: bool) -> dict:
    lowered = file_name.lower()
    extension = "." + lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    try:
        if extension == ".pdf" or content_type == "application/pdf":
            return extract_pdf_text(payload)
        if extension == ".docx":
            return extract_docx_text(payload)
        if extension == ".xlsx":
            return extract_xlsx_text(payload)
        if extension in TEXT_EXTENSIONS or (content_type or "").startswith("text/"):
            return extract_plain_text(payload, allow_full_document)
    except Exception as exc:  # keep upload analysis resilient for malformed files
        return {"status": "partial", "method": extension.lstrip(".") or "unknown", "text": "", "message": f"Ekstraksi gagal: {exc}"}
    return {"status": "unsupported", "method": "metadata_only", "text": "", "message": "Tipe file belum didukung untuk ekstraksi teks penuh."}


def extract_plain_text(payload: bytes, allow_full_document: bool) -> dict:
    limit = len(payload) if allow_full_document else min(len(payload), READ_LIMIT)
    decoded = normalize_text(payload[:limit].decode("utf-8", errors="ignore"))
    return {"status": "ok", "method": "plain_text", "text": decoded[:PREVIEW_LIMIT], "message": None}


def extract_pdf_text(payload: bytes) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "unsupported", "method": "pdf", "text": "", "message": "Dependency pypdf belum terpasang."}
    reader = PdfReader(BytesIO(payload))
    parts = []
    for page in reader.pages[:5]:
        parts.append(page.extract_text() or "")
        if sum(len(part) for part in parts) > READ_LIMIT:
            break
    text = normalize_text(" ".join(parts))
    return {"status": "ok" if text else "partial", "method": "pdf", "text": text[:PREVIEW_LIMIT], "message": None if text else "PDF terbaca, tetapi teks tidak ditemukan."}


def extract_docx_text(payload: bytes) -> dict:
    with ZipFile(BytesIO(payload)) as archive:
        xml_data = archive.read("word/document.xml")
    root = ET.fromstring(xml_data)
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
    text = normalize_text(" ".join(texts))
    return {"status": "ok" if text else "partial", "method": "docx", "text": text[:PREVIEW_LIMIT], "message": None if text else "DOCX terbaca, tetapi teks tidak ditemukan."}


def extract_xlsx_text(payload: bytes) -> dict:
    values = []
    try:
        with ZipFile(BytesIO(payload)) as archive:
            shared_strings = read_xlsx_shared_strings(archive)
            sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            for sheet_name in sheet_names[:4]:
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
                    if sum(len(item) for item in values) > READ_LIMIT:
                        break
                if sum(len(item) for item in values) > READ_LIMIT:
                    break
    except BadZipFile as exc:
        raise ValueError("XLSX bukan arsip zip valid") from exc
    text = normalize_text(" ".join(values))
    return {"status": "ok" if text else "partial", "method": "xlsx", "text": text[:PREVIEW_LIMIT], "message": None if text else "XLSX terbaca, tetapi teks tidak ditemukan."}


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
    text = normalize_text(text)
    if not text:
        return "Gateway mengembalikan respons kosong. Rekomendasi lokal tetap dipakai."
    if "Internal Server Error" in text:
        return "Internal Server Error dari gateway AI. Rekomendasi lokal tetap dipakai."
    return text[:180]


def parse_json_object(value: str) -> dict:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)
