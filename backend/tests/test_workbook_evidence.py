from __future__ import annotations

from copy import deepcopy
import unittest

from app.analysis.workbook_evidence import (
    build_workbook_evidence,
    build_workbook_evidence_view,
)


def sheet(
    name: str,
    index: int,
    *,
    status: str = "processed",
    hidden: bool = False,
    char_count: int = 120,
    template_only: bool = False,
    instruction_only: bool = False,
) -> dict:
    return {
        "id": index,
        "unit_key": f"sheet-{index}",
        "unit_type": "sheet",
        "ordinal": index,
        "status": status,
        "char_count": char_count,
        "source_location": {
            "sheet": name,
            "sheet_index": index,
            "hidden": hidden,
        },
        "metadata": {
            "name": name,
            "state": "hidden" if hidden else "visible",
            "unit_evidence_role": "primary",
            "instruction_only": instruction_only,
            "template_detection": {"template_only": template_only},
        },
        "warnings": [],
    }


def fact(
    fact_id: int,
    sheet_name: str,
    sheet_index: int,
    *,
    claim: str | None = None,
    fact_type: str = "implementation",
    evidence_role: str = "primary",
    period: str | None = "2026",
    organization: str | None = "Ditjen PDP",
) -> dict:
    text = claim or f"Fakta substantif {fact_id} pada {sheet_name}."
    return {
        "id": fact_id,
        "fact_key": f"fact-{fact_id}",
        "claim": text,
        "fact_type": fact_type,
        "evidence_role": evidence_role,
        "period": period,
        "organization": organization,
        "status": "extracted",
        "sources": [{
            "unit_id": sheet_index,
            "unit_key": f"sheet-{sheet_index}",
            "source_location": {
                "sheet": sheet_name,
                "sheet_index": sheet_index,
                "cell": f"A{fact_id}",
            },
            "source_quote": text,
            "source_quote_verified": True,
        }],
    }


def mapping(
    mapping_id: int,
    detail_kode: str,
    supporting_fact_ids: list[int],
    *,
    score: float = 0.8,
) -> dict:
    kode = ".".join(detail_kode.split(".")[:2])
    return {
        "id": mapping_id,
        "kk_id": "KK3.1",
        "kode": kode,
        "detail_kode": detail_kode,
        "parameter_uraian": f"Parameter {detail_kode}",
        "mapping_score": score,
        "supporting_fact_ids": supporting_fact_ids,
        "primary_allowed": False,
    }


def build(
    units: list[dict],
    facts: list[dict],
    mappings: list[dict],
    **kwargs,
) -> dict:
    return build_workbook_evidence(
        file_kind="xlsx",
        document_units=units,
        facts=facts,
        mapping_candidates=mappings,
        **kwargs,
    )


class WorkbookEvidenceDomainTests(unittest.TestCase):
    def test_single_sheet_remains_single_evidence(self) -> None:
        result = build(
            [sheet("Register Risiko", 1)],
            [fact(1, "Register Risiko", 1)],
            [mapping(10, "2.1.2", [1])],
        )

        self.assertTrue(result["applicable"])
        self.assertFalse(result["is_multi_evidence"])
        self.assertEqual(result["sheet_count"], 1)
        self.assertEqual(result["substantive_sheet_count"], 1)
        self.assertEqual(
            result["sheet_results"][0]["primary_parameter"]["detail_kode"],
            "2.1.2",
        )

    def test_two_sheets_with_different_primary_parameters_are_multi_evidence(self) -> None:
        result = build(
            [sheet("Register Risiko", 1), sheet("RTP", 2)],
            [
                fact(1, "Register Risiko", 1),
                fact(2, "RTP", 2),
            ],
            [
                mapping(10, "2.1.2", [1], score=0.91),
                mapping(11, "2.2.3", [2], score=0.88),
            ],
        )

        self.assertTrue(result["is_multi_evidence"])
        self.assertIn(
            "different_primary_parameters_across_substantive_sheets",
            result["workbook_summary"]["detection_reasons"],
        )
        self.assertEqual(
            [item["primary_parameter"]["detail_kode"] for item in result["sheet_results"]],
            ["2.1.2", "2.2.3"],
        )

    def test_same_parameter_on_two_sheets_keeps_both_source_sheets(self) -> None:
        identical_claim = "Register risiko telah disusun dan digunakan oleh Ditjen PDP tahun 2026."
        result = build(
            [sheet("Register Risiko", 1), sheet("Salinan Register", 2)],
            [
                fact(1, "Register Risiko", 1, claim=identical_claim),
                fact(2, "Salinan Register", 2, claim=identical_claim),
            ],
            [mapping(10, "2.1.2", [1, 2])],
        )

        parameter = result["workbook_summary"]["parameter_sources"][0]
        self.assertEqual(
            [item["sheet_name"] for item in parameter["source_sheets"]],
            ["Register Risiko", "Salinan Register"],
        )
        self.assertEqual(result["sheet_results"][1]["duplicate_of"], "sheet:register-risiko")
        self.assertEqual(result["substantive_sheet_count"], 2)
        self.assertEqual(result["unique_substantive_sheet_count"], 1)
        self.assertFalse(result["is_multi_evidence"])

    def test_hidden_sheet_never_becomes_primary(self) -> None:
        result = build(
            [sheet("Arsip", 1, hidden=True)],
            [fact(1, "Arsip", 1)],
            [mapping(10, "2.1.2", [1])],
        )

        hidden = result["sheet_results"][0]
        self.assertEqual(hidden["status"], "hidden")
        self.assertFalse(hidden["primary_eligible"])
        self.assertIsNone(hidden["primary_parameter"])
        self.assertEqual(hidden["secondary_parameters"][0]["detail_kode"], "2.1.2")

    def test_template_empty_and_instruction_only_sheets_never_become_primary(self) -> None:
        cases = (
            (sheet("Kosong", 1, char_count=0), "empty"),
            (sheet("Template", 1, template_only=True), "template_only"),
            (sheet("Petunjuk", 1, instruction_only=True), "instruction_only"),
        )
        for unit, expected_status in cases:
            with self.subTest(status=expected_status):
                result = build(
                    [unit],
                    [fact(1, unit["source_location"]["sheet"], 1)],
                    [mapping(10, "2.1.2", [1])],
                )
                item = result["sheet_results"][0]
                self.assertEqual(item["status"], expected_status)
                self.assertIsNone(item["primary_parameter"])
                self.assertEqual(item["secondary_parameters"], [])

    def test_partial_sheet_is_recommendation_only_and_fail_closed(self) -> None:
        result = build(
            [sheet("Register Parsial", 1, status="partial")],
            [fact(1, "Register Parsial", 1)],
            [mapping(10, "2.1.2", [1])],
        )

        item = result["sheet_results"][0]
        self.assertEqual(item["status"], "needs_review")
        self.assertEqual(item["coverage_status"], "partial")
        self.assertFalse(item["primary_eligible"])
        self.assertEqual(
            item["primary_parameter"]["selection_status"],
            "needs_review",
        )
        self.assertTrue(any("Coverage sheet baru sebagian" in value for value in item["warnings"]))

    def test_empty_workbook_returns_safe_ready_structure_with_warning(self) -> None:
        result = build([], [], [])

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["sheet_results"], [])
        self.assertEqual(result["workbook_summary"]["primary_parameter"], None)
        self.assertIn(
            "NO_WORKBOOK_SHEETS",
            result["workbook_summary"]["warning_codes"],
        )

    def test_failed_and_cancelled_runs_never_publish_sheet_results(self) -> None:
        for run_status, expected_code in (
            ("failed", "RUN_FAILED"),
            ("cancelled", "RUN_CANCELLED"),
        ):
            with self.subTest(run_status=run_status):
                result = build_workbook_evidence_view(
                    run_status=run_status,
                    file_kind="xlsx",
                    document_units=[sheet("Register Risiko", 1)],
                    facts=[fact(1, "Register Risiko", 1)],
                    mapping_candidates=[mapping(10, "2.1.2", [1])],
                )

                self.assertEqual(result["status"], run_status)
                self.assertTrue(result["applicable"])
                self.assertFalse(result["is_multi_evidence"])
                self.assertEqual(result["sheet_results"], [])
                self.assertIn(
                    expected_code,
                    result["workbook_summary"]["warning_codes"],
                )

    def test_mapping_without_supporting_fact_on_sheet_is_not_attributed(self) -> None:
        result = build(
            [sheet("Analisis Risiko", 1)],
            [fact(1, "Analisis Risiko", 1)],
            [mapping(10, "2.2.1", [99])],
        )

        item = result["sheet_results"][0]
        self.assertEqual(item["status"], "needs_sheet_retrieval")
        self.assertIsNone(item["primary_parameter"])
        self.assertEqual(item["secondary_parameters"], [])
        self.assertEqual(item["source_fact_ids"], [1])

    def test_secondary_parameters_are_distinct_and_capped_at_three(self) -> None:
        facts = [fact(index, "Matriks Risiko", 1) for index in range(1, 6)]
        mappings = [
            mapping(10 + index, detail, [index + 1], score=score)
            for index, (detail, score) in enumerate((
                ("2.1.2", 1.0),
                ("2.2.1", 0.8),
                ("2.2.2", 0.6),
                ("2.2.3", 0.4),
                ("2.2.4", 0.2),
            ))
        ]
        result = build([sheet("Matriks Risiko", 1)], facts, mappings)

        item = result["sheet_results"][0]
        self.assertEqual(item["primary_parameter"]["detail_kode"], "2.1.2")
        self.assertEqual(
            [candidate["detail_kode"] for candidate in item["secondary_parameters"]],
            ["2.2.1", "2.2.2", "2.2.3"],
        )

    def test_substantive_register_sheet_prefers_212_over_generic_mapping(self) -> None:
        register = sheet("Peta Risiko 2026", 1)
        register["metadata"].update({
            "risk_matrix_relevant": True,
            "substantive_row_count": 12,
        })
        result = build(
            [register],
            [fact(1, "Peta Risiko 2026", 1)],
            [
                mapping(10, "5.1.3", [1], score=0.95),
                mapping(11, "2.1.2", [1], score=0.80),
            ],
        )

        item = result["sheet_results"][0]
        self.assertEqual(item["primary_parameter"]["detail_kode"], "2.1.2")
        self.assertEqual(item["status"], "mapped")
        self.assertEqual(item["primary_parameter"]["sheet_intent_score"], 1.0)

    def test_monitoring_sheet_prefers_224_and_rejects_225_without_outcome(self) -> None:
        result = build(
            [sheet("Monitoring RTP SET 2025", 1)],
            [fact(1, "Monitoring RTP SET 2025", 1)],
            [
                mapping(10, "2.2.3", [1], score=0.95),
                mapping(11, "2.2.4", [1], score=0.75),
                mapping(12, "2.2.5", [1], score=0.99),
            ],
        )

        item = result["sheet_results"][0]
        self.assertEqual(item["primary_parameter"]["detail_kode"], "2.2.4")
        self.assertNotIn(
            "2.2.5",
            [
                item["primary_parameter"]["detail_kode"],
                *(candidate["detail_kode"] for candidate in item["secondary_parameters"]),
            ],
        )

    def test_effectiveness_225_requires_and_accepts_risk_reduction_fact(self) -> None:
        result = build(
            [sheet("Efektivitas Penanganan", 1)],
            [
                fact(
                    1,
                    "Efektivitas Penanganan",
                    1,
                    claim="Risiko residual menurun setelah penanganan dilaksanakan.",
                )
            ],
            [mapping(10, "2.2.5", [1])],
        )

        self.assertEqual(
            result["sheet_results"][0]["primary_parameter"]["detail_kode"],
            "2.2.5",
        )

    def test_summary_warns_for_mixed_periods_and_organizations(self) -> None:
        result = build(
            [sheet("Register 2025", 1), sheet("Register 2026", 2)],
            [
                fact(
                    1,
                    "Register 2025",
                    1,
                    period="2025",
                    organization="Direktorat A",
                ),
                fact(
                    2,
                    "Register 2026",
                    2,
                    period="2026",
                    organization="Direktorat B",
                ),
            ],
            [mapping(10, "2.1.2", [1, 2])],
        )

        self.assertEqual(
            result["workbook_summary"]["warning_codes"],
            [
                "MIXED_PERIODS_ACROSS_SHEETS",
                "MIXED_ORGANIZATIONS_ACROSS_SHEETS",
            ],
        )

    def test_ambiguity_margin_marks_sheet_and_keeps_existing_source_support(self) -> None:
        result = build(
            [sheet("Analisis", 1)],
            [fact(1, "Analisis", 1)],
            [
                mapping(10, "2.2.1", [1], score=0.80),
                mapping(11, "2.2.2", [1], score=0.77),
            ],
            ambiguity_margin=0.05,
        )

        item = result["sheet_results"][0]
        self.assertEqual(item["status"], "ambiguous")
        self.assertFalse(item["primary_eligible"])
        self.assertEqual(item["primary_parameter"]["selection_status"], "ambiguous")
        self.assertEqual(item["primary_parameter"]["supporting_fact_ids"], [1])
        self.assertTrue(any("selisih mapping score" in warning for warning in item["warnings"]))

    def test_cross_sheet_secondary_support_can_detect_multi_evidence(self) -> None:
        result = build(
            [sheet("Register Utama", 1), sheet("Register dan Monitoring", 2)],
            [
                fact(1, "Register Utama", 1),
                fact(2, "Register dan Monitoring", 2),
                fact(3, "Register dan Monitoring", 2, fact_type="evaluation"),
            ],
            [
                mapping(10, "2.1.2", [1, 2], score=0.90),
                mapping(11, "2.2.4", [3], score=0.60),
            ],
        )

        self.assertTrue(result["is_multi_evidence"])
        self.assertIn(
            "cross_sheet_secondary_parameter_with_source_support",
            result["workbook_summary"]["detection_reasons"],
        )

    def test_different_evidence_stages_can_detect_multi_evidence(self) -> None:
        result = build(
            [sheet("Pelaksanaan", 1), sheet("Evaluasi", 2)],
            [
                fact(1, "Pelaksanaan", 1, fact_type="implementation"),
                fact(2, "Evaluasi", 2, fact_type="evaluation"),
            ],
            [mapping(10, "3.1.1", [1, 2])],
        )

        self.assertTrue(result["is_multi_evidence"])
        self.assertIn(
            "different_evidence_stages_across_substantive_sheets",
            result["workbook_summary"]["detection_reasons"],
        )

    def test_pdf_and_docx_are_not_applicable(self) -> None:
        for file_kind in ("pdf", "docx"):
            with self.subTest(file_kind=file_kind):
                result = build_workbook_evidence(
                    file_kind=file_kind,
                    document_units=[sheet("Tidak boleh diproses", 1)],
                    facts=[fact(1, "Tidak boleh diproses", 1)],
                    mapping_candidates=[mapping(10, "2.1.2", [1])],
                )
                self.assertFalse(result["applicable"])
                self.assertFalse(result["is_multi_evidence"])
                self.assertEqual(result["sheet_results"], [])

    def test_existing_grade_is_information_only_and_never_created(self) -> None:
        mappings = [mapping(10, "2.1.2", [1])]
        assessments = [{
            "id": 50,
            "mapping_candidate_id": 10,
            "candidate_grade": "B",
            "grade_ceiling": "B",
            "grade_status": "direction_only",
            "primary_allowed": False,
        }]
        result = build(
            [sheet("Register", 1)],
            [fact(1, "Register", 1)],
            mappings,
            grade_assessments=assessments,
            verification_results=[{
                "id": 70,
                "mapping_candidate_id": 10,
                "status": "verified",
            }],
        )

        candidate = result["sheet_results"][0]["primary_parameter"]
        self.assertNotIn("grade", candidate)
        self.assertNotIn("candidate_grade", candidate)
        self.assertEqual(candidate["verification_status"], "verified")
        self.assertEqual(
            candidate["existing_grade_assessment"],
            {
                "assessment_id": 50,
                "candidate_grade": "B",
                "grade_ceiling": "B",
                "grade_status": "direction_only",
                "primary_allowed": False,
                "source": "existing_grade_assessment",
            },
        )
        self.assertFalse(mappings[0]["primary_allowed"])

    def test_domain_function_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        units = [sheet("Register", 1)]
        facts = [fact(1, "Register", 1)]
        mappings = [mapping(10, "2.1.2", [1])]
        original = deepcopy((units, facts, mappings))

        first = build(units, facts, mappings)
        second = build(units, facts, mappings)

        self.assertEqual(first, second)
        self.assertEqual((units, facts, mappings), original)


if __name__ == "__main__":
    unittest.main()
