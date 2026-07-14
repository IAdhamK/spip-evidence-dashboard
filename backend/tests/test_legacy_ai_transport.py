from __future__ import annotations

import json
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from app.config import Settings
from app.legacy_ai_transport import (
    SmartUploadError,
    call_chat_completion,
    chat_completion_url,
    prepare_chat_body,
    sanitize_http_error_detail,
)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


class LegacyAiTransportTests(unittest.TestCase):
    def test_chat_url_normalizes_base_and_path(self) -> None:
        settings = Settings(
            _env_file=None,
            deepseek_base_url="https://ai.sumopod.com/v1/",
            deepseek_chat_path="chat/completions",
        )
        self.assertEqual(
            chat_completion_url(settings),
            "https://ai.sumopod.com/v1/chat/completions",
        )
        direct = settings.model_copy(update={
            "deepseek_base_url": "https://gateway.example/chat/completions",
        })
        self.assertEqual(
            chat_completion_url(direct),
            "https://gateway.example/chat/completions",
        )

    def test_thinking_body_removes_incompatible_sampling_fields(self) -> None:
        settings = Settings(_env_file=None, deepseek_thinking_mode="enabled")
        source = {
            "model": "deepseek-v4-pro",
            "temperature": 0.2,
            "top_p": 0.9,
            "messages": [],
        }
        prepared = prepare_chat_body(settings, source)
        self.assertNotIn("temperature", prepared)
        self.assertNotIn("top_p", prepared)
        self.assertEqual(prepared["thinking"], {"type": "enabled"})
        self.assertEqual(prepared["reasoning_effort"], "high")
        self.assertFalse(prepared["stream"])
        self.assertIn("temperature", source)

    def test_http_error_sanitizer_redacts_credentials_and_html(self) -> None:
        raw = "<html>Authentication Error Received API Key = sk-secretABC, Key Hash (Token) = ABC123</html>"
        sanitized = sanitize_http_error_detail(raw)
        self.assertIn("Authentication Error dari Sumopod", sanitized)
        self.assertNotIn("secretABC", sanitized)
        self.assertNotIn("ABC123", sanitized)
        self.assertNotIn("<html>", sanitized)

    def test_chat_call_uses_sumopod_contract_and_returns_json(self) -> None:
        settings = Settings(
            _env_file=None,
            sumopod_api_key="test-key-not-production",
            deepseek_model="deepseek-v4-pro",
            deepseek_thinking_mode="disabled",
        )
        response_payload = {"choices": [{"message": {"content": "ok"}}]}
        with patch(
            "app.legacy_ai_transport.urlopen",
            return_value=_Response(json.dumps(response_payload).encode("utf-8")),
        ) as mocked:
            result = call_chat_completion(
                settings,
                {"model": settings.deepseek_model, "messages": []},
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["payload"], response_payload)
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://ai.sumopod.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key-not-production")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["model"], "deepseek-v4-pro")
        self.assertEqual(sent["thinking"], {"type": "disabled"})

    def test_smart_upload_keeps_legacy_error_import_compatible(self) -> None:
        from app.smart_upload import SmartUploadError as CompatibilityError

        self.assertIs(CompatibilityError, SmartUploadError)

    def test_network_transport_dependencies_are_isolated_from_smart_upload_domain(self) -> None:
        smart_upload_path = Path(__file__).resolve().parents[1] / "app" / "smart_upload.py"
        tree = ast.parse(smart_upload_path.read_text(encoding="utf-8"))
        urllib_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("urllib"):
                urllib_imports.append(node.module)
            if isinstance(node, ast.Import):
                urllib_imports.extend(
                    alias.name for alias in node.names if alias.name.startswith("urllib")
                )
        self.assertEqual(urllib_imports, [])


if __name__ == "__main__":
    unittest.main()
