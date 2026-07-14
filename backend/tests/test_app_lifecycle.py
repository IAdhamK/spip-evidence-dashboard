from __future__ import annotations

import ast
from pathlib import Path
import unittest

from app.lifecycle import analysis_lifespan


class RecordingManager:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


class AppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_lifespan_starts_and_stops_worker(self) -> None:
        manager = RecordingManager()
        async with analysis_lifespan(manager, True)(None):
            self.assertEqual(manager.events, ["start"])
        self.assertEqual(manager.events, ["start", "stop"])

    async def test_disabled_lifespan_does_not_start_but_still_stops_worker(self) -> None:
        manager = RecordingManager()
        async with analysis_lifespan(manager, False)(None):
            self.assertEqual(manager.events, [])
        self.assertEqual(manager.events, ["stop"])

    async def test_lifespan_stops_worker_when_application_body_fails(self) -> None:
        manager = RecordingManager()
        with self.assertRaisesRegex(RuntimeError, "startup body failed"):
            async with analysis_lifespan(manager, True)(None):
                raise RuntimeError("startup body failed")
        self.assertEqual(manager.events, ["start", "stop"])

    def test_main_uses_lifespan_without_deprecated_on_event(self) -> None:
        main_path = Path(__file__).parents[1] / "app" / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        decorators = [
            decorator
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
        ]
        self.assertFalse(
            any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "on_event"
                for decorator in decorators
            )
        )
        fastapi_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FastAPI"
        ]
        self.assertEqual(len(fastapi_calls), 1)
        self.assertIn("lifespan", {keyword.arg for keyword in fastapi_calls[0].keywords})


if __name__ == "__main__":
    unittest.main()
