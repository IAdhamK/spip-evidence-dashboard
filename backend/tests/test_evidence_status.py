from __future__ import annotations

import unittest

from app.evidence_status import (
    STATUS_EMPTY,
    STATUS_FULL,
    STATUS_PARTIAL,
    active_parameter_codes,
    attach_parameter_progress,
    evaluate_parameter_status,
    evaluate_subunsur_status,
)


def parameter(detail_kode: str = "1.1.1", *, spip: str = "SPIP", mri: str = "-", iepk: str = "-") -> dict:
    return {
        "detail_kode": detail_kode,
        "kode_spip": spip,
        "kode_mri": mri,
        "kode_iepk": iepk,
        "grades": [{"grade": grade} for grade in ("A", "B", "C", "D", "E")],
    }


def slots(detail_kode: str, *filled_grades: str, error_grade: str | None = None) -> list[dict]:
    return [
        {
            "detail_kode": detail_kode,
            "grade": grade,
            "file_count": 1 if grade in filled_grades else 0,
            "error_message": "gagal dibaca" if grade == error_grade else None,
        }
        for grade in ("A", "B", "C", "D", "E")
    ]


class EvidenceStatusTests(unittest.TestCase):
    def test_dash_placeholders_are_not_active_codes(self) -> None:
        self.assertEqual(active_parameter_codes(parameter()), ["SPIP"])

    def test_empty_parameter_is_empty(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1"))
        self.assertEqual(result["status"], STATUS_EMPTY)

    def test_spip_e_only_is_contiguous_and_full(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1", "E"))
        self.assertEqual(result["status"], STATUS_FULL)
        self.assertEqual(result["highest_filled_grade"], "E")

    def test_spip_may_stop_at_c_when_e_d_c_are_filled(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1", "E", "D", "C"))
        self.assertEqual(result["status"], STATUS_FULL)
        self.assertEqual(result["highest_filled_grade"], "C")

    def test_spip_c_without_e_and_d_is_partial(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1", "C"))
        self.assertEqual(result["status"], STATUS_PARTIAL)
        self.assertEqual(result["missing_grades_before_highest"], ["E", "D"])

    def test_spip_gap_is_partial(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1", "E", "C"))
        self.assertEqual(result["status"], STATUS_PARTIAL)
        self.assertEqual(result["missing_grades_before_highest"], ["D"])

    def test_mri_allows_one_evidence_at_any_grade(self) -> None:
        result = evaluate_parameter_status(
            parameter(mri="MRI"),
            slots("1.1.1", "B"),
        )
        self.assertEqual(result["status"], STATUS_FULL)
        self.assertEqual(result["rule_mode"], "single_grade")

    def test_iepk_allows_one_evidence_at_any_grade(self) -> None:
        result = evaluate_parameter_status(
            parameter(iepk="IEPK"),
            slots("1.1.1", "A"),
        )
        self.assertEqual(result["status"], STATUS_FULL)

    def test_scan_error_stays_fail_conservative(self) -> None:
        result = evaluate_parameter_status(parameter(), slots("1.1.1", error_grade="E"))
        self.assertEqual(result["status"], STATUS_PARTIAL)

    def test_missing_higher_grade_folders_do_not_block_contiguous_spip(self) -> None:
        grade_slots = slots("1.1.1", "E", "D", "C")
        for slot in grade_slots:
            if slot["grade"] in {"A", "B"}:
                slot["error_message"] = "WebDAV gagal: HTTP 404 Not Found; folder could not be located"

        result = evaluate_parameter_status(parameter(), grade_slots)

        self.assertEqual(result["status"], STATUS_FULL)
        self.assertEqual(result["highest_filled_grade"], "C")
        self.assertFalse(result["has_scan_error"])

    def test_missing_folder_inside_spip_sequence_is_still_a_gap(self) -> None:
        grade_slots = slots("1.1.1", "E", "C")
        next(slot for slot in grade_slots if slot["grade"] == "D")["error_message"] = (
            "WebDAV gagal: HTTP 404 Not Found; folder could not be located"
        )

        result = evaluate_parameter_status(parameter(), grade_slots)

        self.assertEqual(result["status"], STATUS_PARTIAL)
        self.assertEqual(result["missing_grades_before_highest"], ["D"])

    def test_server_error_above_highest_grade_still_blocks_status(self) -> None:
        grade_slots = slots("1.1.1", "E", "D", "C")
        next(slot for slot in grade_slots if slot["grade"] == "B")["error_message"] = (
            "WebDAV gagal: HTTP 500 Internal Server Error"
        )

        result = evaluate_parameter_status(parameter(), grade_slots)

        self.assertEqual(result["status"], STATUS_PARTIAL)
        self.assertTrue(result["has_scan_error"])

    def test_subunsur_without_evidence_is_empty(self) -> None:
        result = evaluate_subunsur_status([parameter()], slots("1.1.1"))
        self.assertEqual(result["status"], STATUS_EMPTY)

    def test_subunsur_with_some_parameters_full_is_partial(self) -> None:
        parameters = [parameter("1.1.1"), parameter("1.1.2")]
        all_slots = slots("1.1.1", "E") + slots("1.1.2")
        result = evaluate_subunsur_status(parameters, all_slots)
        self.assertEqual(result["status"], STATUS_PARTIAL)

    def test_subunsur_is_full_only_when_every_parameter_is_full(self) -> None:
        parameters = [parameter("1.1.1"), parameter("1.1.2", mri="MRI")]
        all_slots = slots("1.1.1", "E", "D") + slots("1.1.2", "C")
        result = evaluate_subunsur_status(parameters, all_slots)
        self.assertEqual(result["status"], STATUS_FULL)

    def test_unassigned_file_keeps_subunsur_partial(self) -> None:
        result = evaluate_subunsur_status(
            [parameter()],
            slots("1.1.1", "E"),
            unassigned_file_count=1,
        )
        self.assertEqual(result["status"], STATUS_PARTIAL)

    def test_attach_progress_is_additive_and_attaches_grade_slots(self) -> None:
        parameters = [parameter()]
        grade_slots = slots("1.1.1", "E")
        attach_parameter_progress(parameters, grade_slots)
        self.assertEqual(parameters[0]["evidence_status"], STATUS_FULL)
        self.assertEqual(parameters[0]["active_codes"], ["SPIP"])
        grade_e = next(item for item in parameters[0]["grades"] if item["grade"] == "E")
        self.assertEqual(grade_e["evidence_folders"][0]["file_count"], 1)


if __name__ == "__main__":
    unittest.main()
