from __future__ import annotations

import unittest

from app.analysis.rollout import RolloutGuardEngine


class RolloutGuardTests(unittest.TestCase):
    def test_canary_falls_back_to_development_when_gates_are_closed(self) -> None:
        result = RolloutGuardEngine().evaluate(
            requested_stage="canary",
            canary_percentage=10,
            stable_release_cycles=0,
            promotion={
                "shadow": {"ready": False},
                "canary": {"ready": False},
                "general_release": {"ready": False},
            },
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["effective_stage"], "development")
        self.assertTrue(result["reasons"])

    def test_canary_assignment_is_deterministic_and_percentage_bounded(self) -> None:
        engine = RolloutGuardEngine()
        self.assertFalse(engine.assigned_to_canary("document-a", 0))
        self.assertTrue(engine.assigned_to_canary("document-a", 100))
        self.assertEqual(
            engine.assigned_to_canary("document-a", 25),
            engine.assigned_to_canary("document-a", 25),
        )


if __name__ == "__main__":
    unittest.main()
