from __future__ import annotations

import unittest

from app.analysis.contracts import DocumentIdentity
from app.analysis.facts import FactExtractionEngine


class FactExtractionProvenanceTests(unittest.TestCase):
    def test_long_claim_keeps_exact_source_quote_after_bounding(self) -> None:
        text = (
            "Kebijakan  pengendalian\tinternal telah ditetapkan "
            + "rencana tindak pengendalian " * 40
        )
        identity = DocumentIdentity(
            file_name="long-evidence.txt",
            content_type="text/plain",
            size_bytes=len(text.encode("utf-8")),
            sha256="a" * 64,
            file_kind="text",
        )
        units = [
            {
                "id": 1,
                "unit_key": "line-1",
                "status": "processed",
                "text": text,
                "source_location": {"line_start": 1, "line_end": 1},
                "metadata": {},
            }
        ]

        facts, _result = FactExtractionEngine().run(identity, units)

        self.assertTrue(facts)
        source_quote = facts[0]["source"]["source_quote"]
        self.assertLessEqual(len(source_quote), 700)
        self.assertIn(source_quote, text)
        self.assertFalse(source_quote.endswith("."))
