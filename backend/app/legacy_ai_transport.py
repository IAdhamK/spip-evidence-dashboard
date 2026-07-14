from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Settings


class SmartUploadError(RuntimeError):
    pass


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


def sanitize_http_error_detail(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("DOCTYPE html", "").replace('html lang="en"', "")
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-...", text)
    text = re.sub(r"Received API Key\s*=\s*[^,} ]+", "Received API Key = sk-...", text)
    text = re.sub(r"Key Hash \(Token\)\s*=\s*[A-Za-z0-9]+", "Key Hash (Token) = [redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Gateway mengembalikan respons kosong."
    if "Internal Server Error" in text:
        return "Internal Server Error dari gateway AI."
    if "Authentication Error" in text or "Invalid proxy server token" in text:
        return (
            "Authentication Error dari Sumopod: API key/token ditolak. "
            "Pastikan key disalin langsung dari tombol copy Sumopod dan masih aktif."
        )
    return text[:180]


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
