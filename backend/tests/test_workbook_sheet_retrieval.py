from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.domain.retrieval import ParameterRetrievalEngine
from app.analysis.repository import AnalysisRepository
from app.analysis.workbook_sheet_retrieval import SheetRetrievalFallback
from app.database import Database


def unit(name: str, index: int) -> dict:
    return {
        "id": index,
        "unit_key": f"sheet-{index}",
        "unit_type": "sheet",
        "ordinal": index,
        "heading_path": [name],
        "source_location": {"sheet": name, "sheet_index": index},
        "metadata": {"name": name, "state": "visible"},
        "status": "processed",
    }


def fact(fact_id: int, name: str, claim: str) -> dict:
    return {
        "id": fact_id,
        "fact_key": f"fact-{fact_id}",
        "claim": claim,
        "fact_type": "implementation",
        "evidence_role": "primary",
        "period": "2026",
        "organization": "Unit Pengujian",
        "status": "extracted",
        "sources": [{
            "unit_key": f"sheet-{fact_id}",
            "source_location": {
                "sheet": name,
                "sheet_index": fact_id,
                "cell": "A1",
            },
        }],
    }


def unresolved_workbook(items: list[tuple[str, int]]) -> dict:
    return {
        "schema_version": "workbook-multi-evidence-v1",
        "status": "ready",
        "applicable": True,
        "is_multi_evidence": False,
        "sheet_count": len(items),
        "substantive_sheet_count": len(items),
        "unique_substantive_sheet_count": len(items),
        "sheet_results": [
            {
                "sheet_key": f"sheet:{name.casefold().replace(' ', '-')}",
                "unit_key": f"sheet-{fact_id}",
                "sheet_name": name,
                "sheet_index": fact_id,
                "ordinal": fact_id,
                "hidden": False,
                "status": "needs_sheet_retrieval",
                "primary_eligible": False,
                "evidence_role": "primary",
                "coverage_status": "complete",
                "fact_count": 1,
                "source_fact_ids": [fact_id],
                "context": {"periods": ["2026"], "organizations": ["Unit Pengujian"]},
                "evidence_stages": ["implementation"],
                "primary_parameter": None,
                "secondary_parameters": [],
                "duplicate_of": None,
                "warnings": [],
            }
            for name, fact_id in items
        ],
        "workbook_summary": {
            "primary_parameter": None,
            "secondary_parameters": [],
            "parameter_sources": [],
            "detection_reasons": [],
            "warnings": [],
        },
    }


class CountingQueryExpansionEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def run(self, identity, facts):
        self.calls += 1
        if self.fail:
            return [], EngineResult(
                engine_name="test_query_expansion",
                engine_version="test",
                status=EngineStatus.FAILED,
                input_checksum=identity.sha256,
                warnings=["Provider query expansion gagal; retrieval lokal tetap digunakan."],
            ).finish()
        return ["register risiko"], EngineResult(
            engine_name="test_query_expansion",
            engine_version="test",
            status=EngineStatus.COMPLETED,
            input_checksum=identity.sha256,
        ).finish()


class WorkbookSheetRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        database = Database(str(Path(self.temporary_directory.name) / "parameters.db"))
        database.ensure_mapping()
        database.ensure_parameters()
        self.parameters = [
            item
            for item in AnalysisRepository(database).parameter_index()
            if item["kk_id"] == "KK3.1"
        ]
        self.identity = DocumentIdentity(
            file_name="workbook.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            size_bytes=100,
            sha256="workbook-sha",
            file_kind="xlsx",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _fallback(self, **kwargs) -> SheetRetrievalFallback:
        advanced_rag_enabled = bool(kwargs.pop("advanced_rag_enabled", False))
        return SheetRetrievalFallback(
            retrieval_engine=ParameterRetrievalEngine(
                advanced_rag_enabled=advanced_rag_enabled
            ),
            advanced_rag_enabled=advanced_rag_enabled,
            minimum_confidence=kwargs.pop("minimum_confidence", 0.68),
            ambiguity_margin=kwargs.pop("ambiguity_margin", 0.08),
            **kwargs,
        )

    def _run_case(
        self,
        name: str,
        claim: str,
        *,
        fallback: SheetRetrievalFallback | None = None,
        external_ai_allowed: bool = False,
    ) -> dict:
        sheet_unit = unit(name, 1)
        sheet_fact = fact(1, name, claim)
        return (fallback or self._fallback()).apply(
            workbook_evidence=unresolved_workbook([(name, 1)]),
            identity=self.identity,
            document_units=[sheet_unit],
            facts=[sheet_fact],
            parameters=self.parameters,
            document_role="primary",
            external_ai_allowed=external_ai_allowed,
        )

    def test_risk_stage_sheets_retrieve_expected_parameter_first_candidates(self) -> None:
        cases = (
            (
                "Register Risiko",
                "Risiko telah diidentifikasi dan dicatat dalam register risiko beserta pemilik, penyebab, dan dampaknya.",
                "2.1.2",
            ),
            (
                "Analisis Risiko",
                "Seluruh risiko dianalisis berdasarkan dampak dan kemungkinan untuk menentukan tingkat risiko.",
                "2.2.1",
            ),
            (
                "Prioritas Risiko",
                "Hasil analisis dievaluasi dan ditetapkan urutan prioritas risiko untuk ditangani.",
                "2.2.2",
            ),
            (
                "RTP",
                "Rencana tindak pengendalian risiko memuat tindakan, penanggung jawab, target waktu, dan sumber daya.",
                "2.2.3",
            ),
            (
                "Monitoring RTP",
                "Pelaksanaan rencana tindak pengendalian dimonitor, telah diimplementasikan, dan progres realisasi dicatat.",
                "2.2.4",
            ),
        )
        for name, claim, expected_detail in cases:
            with self.subTest(sheet=name):
                result = self._run_case(name, claim)
                sheet = result["sheet_results"][0]
                self.assertEqual(
                    sheet["primary_parameter"]["detail_kode"],
                    expected_detail,
                )
                self.assertEqual(
                    sheet["primary_parameter"]["provenance"]["origin"],
                    "sheet_retrieval",
                )
                self.assertFalse(sheet["primary_eligible"])

    def test_effectiveness_requires_explicit_risk_reduction_fact(self) -> None:
        without_reduction = self._run_case(
            "Efektivitas Penanganan",
            "Pelaksanaan tindak pengendalian telah diperiksa dan dilaporkan secara berkala.",
        )
        candidates_without = [
            item["detail_kode"]
            for item in [
                without_reduction["sheet_results"][0].get("primary_parameter"),
                *(without_reduction["sheet_results"][0].get("secondary_parameters") or []),
            ]
            if item
        ]
        self.assertNotIn("2.2.5", candidates_without)

        with_reduction = self._run_case(
            "Efektivitas Penanganan",
            "Pengukuran menunjukkan level risiko residual menurun sebesar 65 persen setelah tindak pengendalian.",
        )
        self.assertEqual(
            with_reduction["sheet_results"][0]["primary_parameter"]["detail_kode"],
            "2.2.5",
        )

    def test_provider_failure_remains_fail_closed(self) -> None:
        query_engine = CountingQueryExpansionEngine(fail=True)
        fallback = self._fallback(
            advanced_rag_enabled=True,
            query_expansion_engine=query_engine,
            minimum_confidence=0.99,
        )

        result = self._run_case(
            "Lampiran",
            "Dokumen administratif tersedia.",
            fallback=fallback,
            external_ai_allowed=True,
        )
        sheet = result["sheet_results"][0]

        self.assertEqual(query_engine.calls, 1)
        self.assertIn(sheet["status"], {"needs_sheet_retrieval", "needs_review"})
        self.assertFalse(sheet["primary_eligible"])
        self.assertEqual(sheet["fallback_retrieval"]["provider_status"], "failed")
        self.assertTrue(any("fail-closed" in warning for warning in sheet["warnings"]))

    def test_facts_never_cross_sheet_boundary(self) -> None:
        units = [unit("Register Risiko", 1), unit("Lampiran", 2)]
        facts = [
            fact(
                1,
                "Register Risiko",
                "Risiko telah diidentifikasi dan dicatat dalam register risiko.",
            ),
            fact(2, "Lampiran", "Dokumen administratif tersedia."),
        ]
        result = self._fallback().apply(
            workbook_evidence=unresolved_workbook([
                ("Register Risiko", 1),
                ("Lampiran", 2),
            ]),
            identity=self.identity,
            document_units=units,
            facts=facts,
            parameters=self.parameters,
            document_role="primary",
        )

        for sheet in result["sheet_results"]:
            expected_id = sheet["source_fact_ids"][0]
            recommendations = [
                item for item in [
                    sheet.get("primary_parameter"),
                    *(sheet.get("secondary_parameters") or []),
                ] if item
            ]
            for recommendation in recommendations:
                self.assertEqual(
                    set(recommendation["supporting_fact_ids"]),
                    {expected_id},
                )

    def test_recommendations_never_contain_or_authorize_grade(self) -> None:
        result = self._run_case(
            "Register Risiko",
            "Risiko telah diidentifikasi dan dituangkan dalam register risiko.",
        )
        recommendations = [
            result["sheet_results"][0]["primary_parameter"],
            *(result["sheet_results"][0]["secondary_parameters"] or []),
        ]
        for recommendation in recommendations:
            self.assertNotIn("grade", recommendation)
            self.assertNotIn("candidate_grade", recommendation)
            self.assertFalse(recommendation["primary_allowed"])
            self.assertFalse(recommendation["grade_eligible"])
            self.assertEqual(recommendation["grade_status"], "not_assessed")

    def test_query_expansion_is_not_called_when_local_retrieval_is_sufficient(self) -> None:
        query_engine = CountingQueryExpansionEngine()
        fallback = self._fallback(
            advanced_rag_enabled=True,
            query_expansion_engine=query_engine,
            minimum_confidence=0.40,
            ambiguity_margin=0.005,
        )

        result = self._run_case(
            "Register Risiko",
            "Risiko telah diidentifikasi dan dituangkan dalam register risiko beserta pemilik, penyebab, dan dampaknya.",
            fallback=fallback,
            external_ai_allowed=True,
        )

        self.assertEqual(query_engine.calls, 0)
        self.assertTrue(
            result["sheet_results"][0]["fallback_retrieval"]["deterministic_sufficient"]
        )


if __name__ == "__main__":
    unittest.main()
