from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.document_family import (
    DocumentFamilyEngine,
    GradeEligibilityGate,
    apply_document_evidence_role,
)
from app.analysis.document_family_registry import (
    parameter_scope_for_family,
    restrict_parameters,
)
from app.analysis.document_map import CoverageEngine, NativeParsingEngine
from app.analysis.domain.grading import DomainRuleGradeEngine, finalize_grade_statuses
from app.analysis.domain.retrieval import ParameterRetrievalEngine, SPIPMappingEngine
from app.analysis.facts import FactExtractionEngine, classify_fact_type
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.template_detection import TemplateCompletenessEngine
from app.config import Settings
from app.database import Database


def xlsx_with_risk_sheet(*, filled: bool) -> bytes:
    sheet_names = ["Petunjuk", "Referensi", "Skala", "Metadata", "Arsip", "Matriks Risiko"]
    relationships = []
    sheets = []
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for index, name in enumerate(sheet_names, start=1):
            relationships.append(
                f'<Relationship Id="rId{index}" Target="worksheets/sheet{index}.xml"/>'
            )
            sheets.append(f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>')
            if index < 6:
                rows = [["Keterangan", f"Data pendukung {index}"]]
            else:
                rows = [[
                    "Sasaran", "Pernyataan Risiko", "Penyebab", "Dampak",
                    "Kemungkinan", "Level Risiko", "RTP", "PIC", "Target Waktu",
                ]]
                if filled:
                    rows.extend([
                        ["Layanan tepat waktu", "Keterlambatan penyaluran", "Data terlambat", "Target tidak tercapai", "4", "Tinggi", "Validasi mingguan", "Tim A", "Maret 2025"],
                        ["Data akurat", "Kesalahan rekonsiliasi", "Sistem tidak sinkron", "Laporan salah", "3", "Sedang", "Rekonsiliasi bulanan", "Tim B", "April 2025"],
                        ["Kinerja tercapai", "Gangguan layanan", "Kapasitas kurang", "Pelayanan berhenti", "2", "Sedang", "Tambah kapasitas", "Tim C", "Mei 2025"],
                    ])
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _worksheet_xml(rows),
            )
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(sheets)}</sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(relationships)}</Relationships>',
        )
    return buffer.getvalue()


def _worksheet_xml(rows: list[list[str]]) -> str:
    row_xml = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(values, start=1):
            column = _column_name(column_index)
            cells.append(
                f'<c r="{column}{row_index}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:I{len(rows)}"/><sheetData>{"".join(row_xml)}</sheetData>'
        '</worksheet>'
    )


def _column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(ord("A") + remainder) + value
    return value


def text_unit(text: str) -> dict:
    return {
        "id": 1,
        "unit_key": "text-1",
        "unit_type": "text_chunk",
        "ordinal": 1,
        "status": "processed",
        "text": text,
        "source_location": {"line_start": 1},
        "metadata": {},
        "warnings": [],
    }


def parameter(
    detail_kode: str,
    *,
    parameter_id: int,
    evidence_hint: str = "register peta risiko rencana tindak pengendalian",
    uraian: str | None = None,
) -> dict:
    kode = ".".join(detail_kode.split(".")[:2])
    return {
        "id": parameter_id,
        "kk_id": "KK3.1",
        "kk_title": "Efektivitas dan Efisiensi",
        "kode": kode,
        "detail_kode": detail_kode,
        "matrix_subunsur_name": "Manajemen Risiko",
        "subunsur_name": "Manajemen Risiko",
        "unsur": "Penilaian Risiko",
        "evidence_hint": evidence_hint,
        "uraian": uraian or f"Parameter manajemen risiko {detail_kode}",
        "cara_pengujian": "Periksa peta risiko dan RTP",
        "grades": [],
    }


class DocumentFamilyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = NativeParsingEngine()
        self.coverage = CoverageEngine()
        self.templates = TemplateCompletenessEngine()
        self.family = DocumentFamilyEngine()

    def _profile_xlsx(self, *, filled: bool, mode: str = "screening") -> tuple[list[dict], dict, dict]:
        payload = xlsx_with_risk_sheet(filled=filled)
        identity = DocumentIdentity("FORM MATRIKS PETA RISIKO.xlsx", None, len(payload), "xlsx", "xlsx")
        units, inventory, _ = self.parser.run(identity, payload, mode)
        ledger, _ = self.coverage.run(identity, units)
        units, template_ledger, _ = self.templates.run(identity, units)
        contract, result = self.family.run(identity, units, ledger, template_ledger)
        self.assertIn(result.status, {EngineStatus.COMPLETED, EngineStatus.PARTIAL})
        return units, inventory, contract

    def test_filled_risk_matrix_after_fourth_sheet_is_primary_and_scoped(self) -> None:
        units, inventory, contract = self._profile_xlsx(filled=True)
        risk_sheet = next(item for item in units if item["source_location"].get("sheet") == "Matriks Risiko")
        self.assertEqual(risk_sheet["status"], "processed")
        self.assertEqual(inventory["selected_relevant_sheet_count"], 1)
        self.assertEqual(contract["family"], "risk_matrix")
        self.assertEqual(contract["evidence_role"], "primary")
        self.assertTrue(contract["grade_eligible"])
        self.assertGreaterEqual(contract["features"]["unique_risk_statement_count"], 3)
        self.assertEqual(contract["features"]["relevant_coverage_ratio"], 1.0)

        parameters = [
            parameter("1.3.4", parameter_id=1),
            parameter("2.1.2", parameter_id=2),
            parameter("2.2.1", parameter_id=3),
            parameter("2.2.2", parameter_id=4),
            parameter("2.2.3", parameter_id=5),
            parameter("5.1.3", parameter_id=6),
        ]
        scope = parameter_scope_for_family("risk_matrix")
        scope["evidence_role"] = "primary"
        facts = [{
            "id": 1,
            "fact_key": "matrix",
            "claim": "Matriks peta risiko memuat penyebab dampak RTP PIC dan level risiko.",
            "fact_type": "implementation",
            "evidence_role": "primary",
        }]
        retrieved, _ = ParameterRetrievalEngine().run(
            DocumentIdentity("matrix.xlsx", None, 1, "x", "xlsx"),
            facts,
            parameters,
            parameter_scope=scope,
        )
        keys = [item["detail_kode"] for item in retrieved]
        self.assertNotIn("1.3.4", keys)
        self.assertTrue(set(keys[:4]) & {"2.1.2", "2.2.1", "2.2.2", "2.2.3"})
        self.assertNotIn("1.3.4", [item["detail_kode"] for item in restrict_parameters(parameters, scope)])

    def test_empty_risk_matrix_is_template_and_has_no_primary_scope(self) -> None:
        _units, _inventory, contract = self._profile_xlsx(filled=False)
        self.assertEqual(contract["family"], "template_form")
        self.assertIn(contract["evidence_role"], {"optional", "reject"})
        self.assertFalse(contract["grade_eligible"])
        self.assertEqual(contract["allowed_parameter_keys"], [])

    def test_transmittal_letter_is_supporting_has_relationships_and_no_grade(self) -> None:
        identity = DocumentIdentity("ND Penyampaian Peta Risiko.pdf", None, 1, "nd", "pdf")
        units = [text_unit(
            "NOTA DINAS Nomor 10 Sifat Segera Lampiran dua berkas Hal Penyampaian. "
            "Kepada Direktur, Dari Sekretaris, Tanggal 19 Januari 2026. "
            "Bersama ini disampaikan dan terlampir Peta Risiko dan Laporan MR Semester II 2025-2029 "
            "untuk menjadi perhatian."
        )]
        ledger, _ = self.coverage.run(identity, units)
        units, template_ledger, _ = self.templates.run(identity, units)
        contract, _ = self.family.run(identity, units, ledger, template_ledger)
        self.assertEqual(contract["family"], "transmittal_letter")
        self.assertEqual(contract["evidence_role"], "supporting")
        self.assertFalse(contract["grade_eligible"])
        self.assertEqual(contract["grade_status"], "not_applicable")
        self.assertEqual(contract["allowed_parameter_keys"], [])
        self.assertEqual(
            contract["relationship_hints"][0]["referenced_document_types"],
            ["risk_matrix", "monitoring_report"],
        )

        units = apply_document_evidence_role(units, contract)
        self.assertEqual(
            units[0]["metadata"]["unit_evidence_role"],
            "supporting",
        )
        facts, _ = FactExtractionEngine().run(identity, units, contract)
        self.assertTrue(facts)
        self.assertNotIn("primary", {item["evidence_role"] for item in facts})
        scope = parameter_scope_for_family(contract["family"], facts)
        retrieved, _ = ParameterRetrievalEngine().run(
            identity,
            facts,
            [parameter("1.8.2", parameter_id=1), parameter("5.1.3", parameter_id=2)],
            parameter_scope=scope,
        )
        self.assertEqual(retrieved, [])

    def test_filename_alone_cannot_assign_transmittal_family(self) -> None:
        identity = DocumentIdentity(
            "NOTA DINAS PENYAMPAIAN PETA RISIKO.pdf",
            None,
            1,
            "filename-only",
            "pdf",
        )
        units = [text_unit("Catatan administrasi umum tanpa struktur surat atau lampiran.")]
        ledger, _ = self.coverage.run(identity, units)
        units, template_ledger, _ = self.templates.run(identity, units)
        contract, _ = self.family.run(identity, units, ledger, template_ledger)
        self.assertEqual(contract["family"], "unknown")
        self.assertEqual(contract["grade_status"], "blocked")

    def test_monitoring_report_scope_and_execution_is_not_effectiveness(self) -> None:
        identity = DocumentIdentity("Laporan Monitoring Risiko.pdf", None, 1, "mr", "pdf")
        units = [text_unit(
            "Laporan monitoring semester II memuat realisasi RTP dan status pelaksanaan. "
            "RTP telah dilaksanakan oleh unit kerja."
        )]
        ledger, _ = self.coverage.run(identity, units)
        units, template_ledger, _ = self.templates.run(identity, units)
        contract, _ = self.family.run(identity, units, ledger, template_ledger)
        self.assertEqual(contract["family"], "monitoring_report")
        self.assertEqual(
            set(contract["allowed_parameter_keys"]),
            {"KK3.1|5.1|5.1.3", "KK3.1|2.2|2.2.4", "KK3.1|2.2|2.2.5"},
        )
        self.assertEqual(classify_fact_type("RTP telah dilaksanakan oleh unit kerja.")[0], "implementation")

    def test_low_relevant_coverage_caps_confidence_and_blocks_grade(self) -> None:
        identity = DocumentIdentity("matrix.xlsx", None, 1, "low", "xlsx")
        family = {
            "family": "risk_matrix",
            "family_confidence": 0.95,
            "evidence_role": "primary",
            "grade_eligible": True,
            "parameter_scope": parameter_scope_for_family("risk_matrix"),
        }
        mappings = [{
            "kk_id": "KK3.1", "kode": "2.1", "detail_kode": "2.1.2",
            "retrieval_score": 0.95, "mapping_score": 0.90,
            "supporting_fact_ids": [1, 2, 3], "status": "candidate",
        }]
        gated, _ = GradeEligibilityGate().run(
            identity,
            mappings,
            [],
            family,
            {"relevant_coverage_ratio": 0.50, "unprocessed_relevant_units": 1},
        )
        self.assertLess(gated[0]["calibrated_decision_confidence"], 0.60)
        self.assertNotEqual(gated[0]["decision_confidence_label"], "high")
        self.assertFalse(gated[0]["grade_eligible"])
        self.assertEqual(gated[0]["grade_status"], "blocked")

    def test_pending_relevant_sheet_is_classified_but_cannot_grade(self) -> None:
        identity = DocumentIdentity("matrix.xlsx", None, 1, "pending", "xlsx")
        units = [{
            "id": 1,
            "unit_key": "sheet-9",
            "unit_type": "sheet",
            "ordinal": 9,
            "status": "pending",
            "text": "",
            "source_location": {"sheet": "Matriks Risiko"},
            "metadata": {
                "risk_matrix_relevant": True,
                "risk_header_categories": [
                    "risk_statement", "cause", "impact", "likelihood", "control_plan",
                ],
                "substantive_row_count": 3,
                "risk_statement_values": ["risiko satu", "risiko dua", "risiko tiga"],
                "filled_rtp_count": 3,
                "filled_likelihood_count": 3,
                "filled_impact_count": 3,
                "filled_risk_owner_count": 3,
            },
            "warnings": [],
        }]
        ledger, _ = self.coverage.run(identity, units)
        units, template_ledger, _ = self.templates.run(identity, units)
        contract, _ = self.family.run(identity, units, ledger, template_ledger)
        self.assertEqual(contract["family"], "risk_matrix")
        self.assertEqual(contract["features"]["relevant_coverage_ratio"], 0.0)
        self.assertTrue(contract["warnings"])

        gated, _ = GradeEligibilityGate().run(
            identity,
            [{
                "kk_id": "KK3.1",
                "kode": "2.1",
                "detail_kode": "2.1.2",
                "retrieval_score": 0.95,
                "mapping_score": 0.90,
                "supporting_fact_ids": [1, 2, 3],
            }],
            [],
            contract,
            ledger,
        )
        self.assertLess(gated[0]["calibrated_decision_confidence"], 0.60)
        self.assertEqual(gated[0]["grade_status"], "blocked")

    def test_ambiguous_candidates_are_not_presented_as_certain(self) -> None:
        identity = DocumentIdentity("matrix.xlsx", None, 1, "ambiguous", "xlsx")
        family = {
            "family": "risk_matrix",
            "family_confidence": 0.95,
            "evidence_role": "primary",
            "grade_eligible": True,
            "parameter_scope": parameter_scope_for_family("risk_matrix"),
        }
        mappings = [
            {"kk_id": "KK3.1", "kode": "2.1", "detail_kode": "2.1.2", "retrieval_score": 0.9, "mapping_score": 0.81, "supporting_fact_ids": [1]},
            {"kk_id": "KK3.1", "kode": "2.2", "detail_kode": "2.2.1", "retrieval_score": 0.9, "mapping_score": 0.76, "supporting_fact_ids": [1]},
        ]
        gated, result = GradeEligibilityGate().run(
            identity, mappings, [], family, {"relevant_coverage_ratio": 1.0}
        )
        self.assertTrue(result.output["ambiguous"])
        self.assertTrue(all(item["decision_status"] == "ambiguous" for item in gated))
        self.assertTrue(all(not item["grade_eligible"] for item in gated))

    def test_grade_engine_does_not_create_direction_when_gate_blocks(self) -> None:
        mapping = {
            "id": 1,
            "kk_id": "KK3.1",
            "kode": "2.1",
            "detail_kode": "2.1.2",
            "supporting_fact_ids": [],
            "grades": [{"grade": "E", "kriteria": "Kebijakan ditetapkan"}],
            "grade_eligible": False,
            "grade_status": "not_applicable",
            "grade_block_reasons": [],
        }
        assessments, _ = DomainRuleGradeEngine().run(
            DocumentIdentity("nota.pdf", None, 1, "nota", "pdf"),
            [mapping],
            [],
        )
        self.assertIsNone(assessments[0]["candidate_grade"])
        self.assertEqual(assessments[0]["grade_status"], "not_applicable")
        self.assertFalse(assessments[0]["primary_allowed"])

    def test_supported_grade_requires_successful_independent_verification(self) -> None:
        assessment = {
            "mapping_candidate_id": 10,
            "candidate_grade": "C",
            "primary_allowed": True,
            "grade_eligible": True,
            "grade_status": "direction_only",
            "grade_block_reasons": [],
            "rule_trace": {"approval_status": "approved"},
        }
        rejected = finalize_grade_statuses(
            [assessment],
            [{"mapping_candidate_id": 10, "status": "needs_human_review"}],
        )[0]
        self.assertEqual(rejected["grade_status"], "direction_only")
        self.assertIn(
            "independent_verification_not_passed",
            rejected["grade_block_reasons"],
        )
        self.assertEqual(
            rejected["rule_trace"]["grade_block_reasons"],
            rejected["grade_block_reasons"],
        )

        verified = finalize_grade_statuses(
            [assessment],
            [{"mapping_candidate_id": 10, "status": "verified"}],
        )[0]
        self.assertEqual(verified["grade_status"], "supported")
        self.assertEqual(verified["grade_block_reasons"], [])
        self.assertTrue(verified["rule_trace"]["verification_passed"])

    def test_synthetic_document_family_accuracy_meets_target(self) -> None:
        cases = [
            (
                "transmittal_letter",
                "pdf",
                "NOTA DINAS Nomor 11 Sifat Segera Lampiran dua berkas Hal Penyampaian Kepada Direktur Dari Sekretaris Tanggal 1 Juli 2026. Bersama ini disampaikan laporan dan terlampir Peta Risiko.",
            ),
            (
                "monitoring_report",
                "pdf",
                "Laporan monitoring semester II memuat realisasi RTP, status pelaksanaan, dan efektivitas pengendalian triwulan.",
            ),
            (
                "risk_policy",
                "pdf",
                "Kebijakan manajemen risiko ditetapkan melalui keputusan. Pedoman manajemen risiko ini mengatur ruang lingkup organisasi.",
            ),
            (
                "review_audit",
                "pdf",
                "Laporan audit APIP memuat hasil pemeriksaan, temuan, rekomendasi, dan tindak lanjut auditor.",
            ),
            (
                "meeting_invitation",
                "pdf",
                "Undangan rapat kepada seluruh unit. Hari Senin, tanggal 3, tempat ruang rapat, acara pembahasan risiko.",
            ),
            (
                "meeting_minutes",
                "pdf",
                "Notulen rapat mencatat peserta, agenda, hasil rapat, kesimpulan, dan keputusan bersama.",
            ),
            (
                "photo_documentation",
                "image",
                "Foto dokumentasi kegiatan pengendalian intern.",
            ),
            (
                "template_form",
                "text",
                "Template laporan. Petunjuk pengisian: harap diisi nama unit [nama unit] dan tanggal ........",
            ),
            (
                "unknown",
                "text",
                "Catatan administrasi umum mengenai kegiatan kantor.",
            ),
        ]
        correct = 0
        false_primary = 0
        for expected, kind, text in cases:
            identity = DocumentIdentity(f"synthetic-{expected}.{kind}", None, 1, expected, kind)
            units = [text_unit(text)]
            ledger, _ = self.coverage.run(identity, units)
            units, template_ledger, _ = self.templates.run(identity, units)
            contract, _ = self.family.run(identity, units, ledger, template_ledger)
            correct += contract["family"] == expected
            if expected in {
                "transmittal_letter", "meeting_invitation",
                "photo_documentation", "template_form",
            }:
                false_primary += contract["evidence_role"] == "primary"
        accuracy = correct / len(cases)
        false_primary_rate = false_primary / 4
        self.assertGreaterEqual(accuracy, 0.95)
        self.assertLessEqual(false_primary_rate, 0.02)

    def test_synthetic_parameter_top_k_and_non_grade_safety_meet_targets(self) -> None:
        catalog = [
            parameter("1.3.4", parameter_id=1, evidence_hint="kepemimpinan kinerja manajemen risiko"),
            parameter("2.1.1", parameter_id=2, evidence_hint="kebijakan pedoman keputusan manajemen risiko"),
            parameter("2.1.2", parameter_id=3, evidence_hint="identifikasi register pernyataan risiko penyebab dampak"),
            parameter("2.2.1", parameter_id=4, evidence_hint="analisis kemungkinan nilai dampak level risiko"),
            parameter("2.2.2", parameter_id=5, evidence_hint="prioritas urutan risiko"),
            parameter("2.2.3", parameter_id=6, evidence_hint="rencana tindak pengendalian RTP PIC target waktu"),
            parameter("2.2.4", parameter_id=7, evidence_hint="realisasi pelaksanaan RTP"),
            parameter("2.2.5", parameter_id=8, evidence_hint="efektivitas pengendalian risiko residual"),
            parameter("5.1.2", parameter_id=9, evidence_hint="reviu proses manajemen risiko"),
            parameter("5.1.3", parameter_id=10, evidence_hint="monitoring pemantauan laporan semester"),
            parameter("5.2.1", parameter_id=11, evidence_hint="audit evaluasi terpisah APIP temuan"),
        ]
        cases = [
            (
                "risk_matrix",
                "Matriks register memuat pernyataan risiko, penyebab, dampak, kemungkinan, level, RTP, PIC, dan target waktu.",
                {"2.1.2", "2.2.1", "2.2.2", "2.2.3"},
            ),
            (
                "monitoring_report",
                "Laporan monitoring semester memuat realisasi RTP dan efektivitas pengendalian risiko residual.",
                {"5.1.3", "2.2.4", "2.2.5"},
            ),
            (
                "risk_policy",
                "Kebijakan dan pedoman manajemen risiko ditetapkan melalui keputusan.",
                {"2.1.1"},
            ),
            (
                "review_audit",
                "Audit APIP merupakan evaluasi terpisah yang mencatat temuan dan rekomendasi.",
                {"5.1.2", "5.2.1"},
            ),
        ]
        top_1_hits = 0
        top_5_hits = 0
        for family, claim, expected in cases:
            scope = parameter_scope_for_family(family)
            retrieved, _ = ParameterRetrievalEngine().run(
                DocumentIdentity(f"synthetic-{family}.txt", None, 1, family, "text"),
                [{
                    "id": 1,
                    "fact_key": family,
                    "claim": claim,
                    "fact_type": "implementation",
                    "evidence_role": "primary",
                }],
                catalog,
                parameter_scope=scope,
            )
            ranked = [item["detail_kode"] for item in retrieved]
            top_1_hits += bool(ranked and ranked[0] in expected)
            top_5_hits += bool(set(ranked[:5]) & expected)
            self.assertTrue(set(ranked) <= {
                key.rsplit("|", 1)[-1]
                for key in scope["allowed_parameter_keys"]
            })

        non_grade_primary = 0
        non_grade_overgrade = 0
        for family in (
            "transmittal_letter",
            "meeting_invitation",
            "photo_documentation",
            "template_form",
        ):
            scope = parameter_scope_for_family(family)
            retrieved, _ = ParameterRetrievalEngine().run(
                DocumentIdentity(f"synthetic-{family}.txt", None, 1, family, "text"),
                [{"id": 1, "claim": "risiko kinerja", "evidence_role": "supporting"}],
                catalog,
                parameter_scope=scope,
            )
            non_grade_primary += bool(retrieved)
            non_grade_overgrade += any(item.get("candidate_grade") for item in retrieved)

        self.assertGreaterEqual(top_5_hits / len(cases), 0.95)
        self.assertGreaterEqual(top_1_hits / len(cases), 0.85)
        self.assertLessEqual(non_grade_primary / 4, 0.02)
        self.assertLessEqual(non_grade_overgrade / 4, 0.02)


class DocumentFamilyPipelineRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        database_path = str(Path(self.directory.name) / "family-pipeline.db")
        self.database = Database(database_path)
        self.database.ensure_mapping()
        self.database.ensure_parameters()
        self.settings = Settings(
            _env_file=None,
            database_path=database_path,
            analysis_pipeline_v2_enabled=True,
            analysis_advanced_rag_enabled=False,
            analysis_structured_model_enabled=False,
            analysis_mapping_reasoning_enabled=False,
            analysis_model_verifier_enabled=False,
            smart_upload_allow_real_upload=False,
        )
        self.orchestrator = AnalysisOrchestrator(self.database, self.settings)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_pipeline_risk_matrix_never_promotes_leadership_parameter(self) -> None:
        payload = xlsx_with_risk_sheet(filled=True)
        result = self.orchestrator.start(
            file_name="251210 FORM MATRIKS PETA RISIKO TA 2025 - 2029.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            payload=payload,
            analysis_mode="full_audit",
        )
        self.assertEqual(result["document_family"]["family"], "risk_matrix")
        self.assertEqual(result["document_family"]["evidence_role"], "primary")
        self.assertNotIn("1.3.4", [item["detail_kode"] for item in result["mappings"]])
        self.assertTrue(
            {item["detail_kode"] for item in result["mappings"]}
            & {"2.1.2", "2.2.1", "2.2.2", "2.2.3"}
        )
        self.assertTrue(all(
            item["grade_status"] in {"blocked", "direction_only"}
            for item in result["mappings"]
        ))
        engine_names = [item["engine_name"] for item in result["engines"]]
        self.assertLess(engine_names.index("document_family"), engine_names.index("parameter_retrieval"))

    def test_pipeline_transmittal_letter_has_no_primary_mapping_or_grade(self) -> None:
        payload = (
            "NOTA DINAS Nomor 10 Sifat Segera Lampiran dua berkas Hal Penyampaian. "
            "Kepada Direktur, Dari Sekretaris, Tanggal 19 Januari 2026. "
            "Bersama ini disampaikan dan terlampir Peta Risiko dan Laporan MR Semester II "
            "2025-2029 untuk menjadi perhatian."
        ).encode()
        result = self.orchestrator.start(
            file_name="Copy of ND Penyampaian Peta Risiko dan Laporan MR II Ditjen PDP 2025-2029.txt",
            content_type="text/plain",
            payload=payload,
            analysis_mode="full_audit",
        )
        self.assertEqual(result["document_family"]["family"], "transmittal_letter")
        self.assertEqual(result["document_family"]["evidence_role"], "supporting")
        self.assertFalse(result["document_family"]["grade_eligible"])
        self.assertEqual(result["document_family"]["grade_status"], "not_applicable")
        self.assertTrue(all(
            item["metadata"]["unit_evidence_role"] == "supporting"
            for item in result["document_units"]
        ))
        self.assertEqual(result["mappings"], [])
        self.assertEqual(result["grade_assessments"], [])
        self.assertNotIn("1.8.2", [item["detail_kode"] for item in result["mappings"]])


if __name__ == "__main__":
    unittest.main()
