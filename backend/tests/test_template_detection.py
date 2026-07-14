from __future__ import annotations

import unittest

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.facts import FactExtractionEngine
from app.analysis.template_detection import TemplateCompletenessEngine


class TemplateCompletenessTests(unittest.TestCase):
    def test_template_instructions_do_not_become_activity_facts(self) -> None:
        identity = DocumentIdentity("template.txt", "text/plain", 0, "sha", "text")
        units = [{
            "id": 1,
            "unit_key": "text-1",
            "unit_type": "text_chunk",
            "ordinal": 1,
            "status": "processed",
            "text": "Template laporan. Petunjuk pengisian: nama unit [nama unit] dan tanggal ........",
            "source_location": {"line_start": 1, "line_end": 1},
            "metadata": {},
            "warnings": [],
        }]
        classified, ledger, result = TemplateCompletenessEngine().run(identity, units)
        self.assertEqual(result.status, EngineStatus.COMPLETED)
        self.assertEqual(ledger["template_only_units"], 1)
        facts, _ = FactExtractionEngine().run(identity, classified)
        self.assertEqual(facts, [])

    def test_dated_completed_activity_is_not_rejected_as_template(self) -> None:
        identity = DocumentIdentity("report.txt", "text/plain", 0, "sha", "text")
        units = [{
            "id": 1,
            "unit_key": "text-1",
            "status": "processed",
            "text": "Format laporan telah diisi. Kegiatan telah dilaksanakan pada tahun 2026 dan hasil evaluasi ditandatangani.",
            "source_location": {"line_start": 1},
            "metadata": {},
            "warnings": [],
        }]
        classified, ledger, _ = TemplateCompletenessEngine().run(identity, units)
        self.assertEqual(ledger["template_only_units"], 0)
        self.assertFalse(classified[0]["metadata"]["template_detection"]["template_only"])

    def test_single_explicit_instruction_is_template_only_without_activity(self) -> None:
        identity = DocumentIdentity("instructions.txt", "text/plain", 0, "sha", "text")
        units = [{
            "id": 1,
            "unit_key": "text-1",
            "status": "processed",
            "text": "Petunjuk pengisian: isi nama kegiatan, periode, dan hasil evaluasi pada kolom yang tersedia.",
            "source_location": {"line_start": 1},
            "metadata": {},
            "warnings": [],
        }]
        classified, ledger, _ = TemplateCompletenessEngine().run(identity, units)
        self.assertEqual(ledger["template_only_units"], 1)
        facts, _ = FactExtractionEngine().run(identity, classified)
        self.assertEqual(facts, [])


if __name__ == "__main__":
    unittest.main()
