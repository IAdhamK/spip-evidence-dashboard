from __future__ import annotations

from dataclasses import dataclass
import html
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.analysis.security import validate_external_url
from app.config import Settings
from app.database import Database
from app.smart_upload import (
    classify_reference_stage_hits,
    clean_ai_text,
    extract_preview_text,
    normalize_text,
)


MAX_LINK_BYTES = 12 * 1024 * 1024
MAX_CACHE_TEXT_CHARS = 16000
MAX_CACHE_SUMMARY_CHARS = 2200
DEFAULT_JOB_LIMIT = 16
REDIRECT_CODES = {301, 302, 303, 307, 308}
ALLOWED_LINK_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/csv",
    "text/html",
    "application/json",
    "application/xml",
    "text/xml",
}


@dataclass
class CrawlTarget:
    url: str
    file_name: str
    content_type: str | None
    supported: bool = True
    message: str = ""


class EvidenceLinkCrawler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_message = "Belum pernah dijalankan."

    def status(self, db: Database) -> dict:
        with self._lock:
            running = self._running
            last_message = self._last_message
        return {
            "running": running,
            "last_message": last_message,
            "counts": db.evidence_link_cache_counts(),
        }

    def start(self, db: Database, settings: Settings, limit: int = DEFAULT_JOB_LIMIT) -> dict:
        with self._lock:
            if self._running:
                return {
                    "running": True,
                    "started": False,
                    "last_message": self._last_message,
                    "counts": db.evidence_link_cache_counts(),
                }
            self._running = True
            self._last_message = "Crawl link evidence dimulai."
            self._thread = threading.Thread(
                target=self._run,
                args=(db, settings, limit),
                daemon=True,
                name="evidence-link-crawler",
            )
            self._thread.start()
        return {
            "running": True,
            "started": True,
            "last_message": self._last_message,
            "counts": db.evidence_link_cache_counts(),
        }

    def _run(self, db: Database, settings: Settings, limit: int) -> None:
        processed = 0
        try:
            while processed < max(1, limit):
                jobs = db.claim_evidence_link_jobs(limit=min(4, limit - processed))
                if not jobs:
                    break
                for job in jobs:
                    self._process_job(db, settings, job)
                    processed += 1
        finally:
            with self._lock:
                self._running = False
                self._last_message = f"Crawl link evidence selesai: {processed} link diproses."

    def _process_job(self, db: Database, settings: Settings, job: dict) -> None:
        url = str(job.get("url") or "").strip()
        if not url:
            return
        target = build_crawl_target(url)
        if not target.supported:
            db.mark_evidence_link_cache(
                url,
                "unsupported",
                content_type=target.content_type,
                title=target.file_name,
                error_message=target.message,
            )
            return
        valid, validation_message = validate_external_url(
            target.url,
            settings.evidence_link_host_allowlist,
        )
        if not valid:
            db.mark_evidence_link_cache(
                url,
                "unsupported",
                content_type=target.content_type,
                title=target.file_name,
                error_message=validation_message,
            )
            return
        try:
            payload, content_type = fetch_link_payload(target.url, settings, MAX_LINK_BYTES)
            text = extract_link_text(target.file_name, content_type or target.content_type, payload)
            text = normalize_text(text)
            if not text:
                raise ValueError("Isi link tidak terbaca atau link tidak publik.")
            if looks_like_google_block_page(text):
                raise ValueError("Link Google tidak publik atau membutuhkan login.")
            summary = clean_ai_text(text, MAX_CACHE_SUMMARY_CHARS)
            stage_hits = classify_reference_stage_hits(
                normalize_text(
                    " ".join(
                        [
                            str(job.get("source_label") or ""),
                            str(job.get("source_context") or ""),
                            summary,
                            text[:6000],
                        ]
                    )
                )
            )
            db.mark_evidence_link_cache(
                url,
                "ok",
                content_type=content_type or target.content_type,
                title=target.file_name,
                text=clean_ai_text(text, MAX_CACHE_TEXT_CHARS),
                summary=summary,
                stage_hits=stage_hits,
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            db.mark_evidence_link_cache(
                url,
                "error",
                content_type=target.content_type,
                title=target.file_name,
                error_message=short_error_message(exc),
            )


def build_crawl_target(url: str) -> CrawlTarget:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    if "docs.google.com" in host:
        doc_id = google_path_id(path)
        if not doc_id:
            return CrawlTarget(url, "google-link.txt", "text/plain", False, "ID dokumen Google tidak terbaca.")
        if "/document/d/" in path:
            return CrawlTarget(
                f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
                "google-document.txt",
                "text/plain",
            )
        if "/spreadsheets/d/" in path:
            return CrawlTarget(
                f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx",
                "google-sheet.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        if "/presentation/d/" in path:
            return CrawlTarget(
                f"https://docs.google.com/presentation/d/{doc_id}/export/pptx",
                "google-slides.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        if "/forms/" in path:
            return CrawlTarget(url, "google-form.html", "text/html", False, "Google Form belum dibaca otomatis.")
    if "drive.google.com" in host:
        file_id = google_drive_file_id(parsed)
        if file_id:
            return CrawlTarget(
                f"https://drive.google.com/uc?export=download&id={file_id}",
                "google-drive-file",
                None,
            )
        if "/drive/folders/" in path:
            return CrawlTarget(url, "google-drive-folder", None, False, "Folder Google Drive belum dicrawl otomatis.")
    return CrawlTarget(url, file_name_from_url(url), None)


def google_path_id(path: str) -> str:
    match = re.search(r"/d/([^/]+)", path)
    return match.group(1) if match else ""


def google_drive_file_id(parsed) -> str:
    match = re.search(r"/file/d/([^/]+)", parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("id")
    return query_id[0] if query_id else ""


def file_name_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else "linked-evidence"
    return name or "linked-evidence"


def fetch_link_payload(url: str, settings: Settings, max_bytes: int) -> tuple[bytes, str | None]:
    timeout = max(8, min(int(settings.scan_timeout_seconds or 20), 30))
    opener = build_opener(NoRedirectHandler())
    current_url = url
    for redirect_count in range(max(0, settings.analysis_max_redirects) + 1):
        valid, validation_message = validate_external_url(
            current_url,
            settings.evidence_link_host_allowlist,
        )
        if not valid:
            raise ValueError(validation_message)
        request = Request(
            current_url,
            headers={
                "User-Agent": "SPIP-Evidence-Dashboard/1.0",
                "Accept": "*/*",
            },
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type")
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise ValueError("Content-Length link evidence melebihi batas cache awal.")
                valid_content, content_message = validate_link_content_type(content_type)
                if not valid_content:
                    raise ValueError(content_message)
                payload = response.read(max_bytes + 1)
        except HTTPError as exc:
            if exc.code not in REDIRECT_CODES:
                raise
            location = exc.headers.get("Location")
            if not location:
                raise ValueError("Redirect link evidence tidak memiliki lokasi tujuan.") from exc
            if redirect_count >= max(0, settings.analysis_max_redirects):
                raise ValueError("Jumlah redirect link evidence melebihi batas.") from exc
            current_url = urljoin(current_url, location)
            continue
        if len(payload) > max_bytes:
            raise ValueError("Ukuran isi link melebihi batas cache awal.")
        return payload, content_type
    raise ValueError("Link evidence tidak dapat diambil setelah redirect.")


def validate_link_content_type(content_type: str | None) -> tuple[bool, str]:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if not normalized:
        return False, "Content-Type link evidence tidak tersedia."
    if normalized not in ALLOWED_LINK_CONTENT_TYPES:
        return False, f"Content-Type link evidence tidak diizinkan: {normalized}."
    return True, ""


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def extract_link_text(file_name: str, content_type: str | None, payload: bytes) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower() or None
    if content_type == "text/html" or file_name.lower().endswith((".html", ".htm")):
        return strip_html_text(payload)
    extension = infer_extension(file_name, content_type)
    extraction = extract_preview_text(extension, content_type, payload, False, "deep")
    return extraction.get("text") or ""


def infer_extension(file_name: str, content_type: str | None) -> str:
    lowered = file_name.lower()
    if "." in lowered.rsplit("/", 1)[-1]:
        return file_name
    mapping = {
        "application/pdf": "linked-evidence.pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "linked-evidence.docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "linked-evidence.xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "linked-evidence.pptx",
        "text/plain": "linked-evidence.txt",
        "text/csv": "linked-evidence.csv",
    }
    return mapping.get(content_type or "", file_name or "linked-evidence.txt")


def strip_html_text(payload: bytes) -> str:
    raw = payload.decode("utf-8", errors="ignore")
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return normalize_text(html.unescape(raw))


def looks_like_google_block_page(text: str) -> bool:
    lowered = text.lower()
    return (
        "sign in" in lowered
        and "google" in lowered
        and ("drive" in lowered or "docs" in lowered)
    ) or "you need access" in lowered


def short_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    return clean_ai_text(str(exc), 260)
