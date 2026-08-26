from __future__ import annotations

from typing import Iterable


STATUS_EMPTY = "Kosong"
STATUS_PARTIAL = "Terisi Sebagian"
STATUS_FULL = "Terisi Penuh"

GRADE_SEQUENCE = ("E", "D", "C", "B", "A")
EMPTY_CODE_VALUES = {"", "-", "–", "—"}


def _is_missing_folder_error(message: object) -> bool:
    """Treat a confirmed WebDAV 404 as an absent Grade folder, not a read failure."""

    text = str(message or "").strip().lower()
    return bool(text) and (
        "http 404 not found" in text
        or "sabredav\\exception\\notfound" in text
        or "could not be located" in text
    )


def _clean_code(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in EMPTY_CODE_VALUES else text


def active_parameter_codes(parameter: dict) -> list[str]:
    """Return the active assessment systems without treating dash placeholders as codes."""

    return [
        label
        for label, field in (("SPIP", "kode_spip"), ("MRI", "kode_mri"), ("IEPK", "kode_iepk"))
        if _clean_code(parameter.get(field))
    ]


def _slots_for_parameter(parameter: dict, slots: Iterable[dict]) -> list[dict]:
    detail_kode = str(parameter.get("detail_kode") or "").strip()
    return [slot for slot in slots if str(slot.get("detail_kode") or "").strip() == detail_kode]


def evaluate_parameter_status(parameter: dict, slots: Iterable[dict]) -> dict:
    """Evaluate one parameter from canonical grade slots without changing evidence data."""

    parameter_slots = _slots_for_parameter(parameter, slots)
    active_codes = active_parameter_codes(parameter)
    file_count = sum(max(int(slot.get("file_count") or 0), 0) for slot in parameter_slots)
    filled_grades = [
        grade
        for grade in GRADE_SEQUENCE
        if any(
            str(slot.get("grade") or "").strip().upper() == grade
            and int(slot.get("file_count") or 0) > 0
            for slot in parameter_slots
        )
    ]
    scan_errors = []
    for slot in parameter_slots:
        error_message = str(slot.get("error_message") or "").strip()
        if not error_message:
            continue
        # A missing empty Grade folder is equivalent to an unfilled Grade.
        # Real read failures must remain fail-conservative.
        if int(slot.get("file_count") or 0) == 0 and _is_missing_folder_error(error_message):
            continue
        scan_errors.append(error_message)
    has_alternative_code = "MRI" in active_codes or "IEPK" in active_codes
    rule_mode = "single_grade" if has_alternative_code else "sequential_from_e"

    highest_filled_grade = filled_grades[-1] if filled_grades else None
    missing_before_highest: list[str] = []
    if highest_filled_grade:
        highest_index = GRADE_SEQUENCE.index(highest_filled_grade)
        missing_before_highest = [
            grade for grade in GRADE_SEQUENCE[: highest_index + 1] if grade not in filled_grades
        ]

    if scan_errors:
        status = STATUS_PARTIAL
        reason = "Pemeriksaan folder Grade belum lengkap karena ada folder yang gagal dibaca."
    elif not filled_grades:
        status = STATUS_EMPTY
        reason = "Belum ada evidence pada Grade parameter ini."
    elif not active_codes:
        status = STATUS_PARTIAL
        reason = "Evidence ditemukan, tetapi jenis kode parameter belum dapat ditentukan."
    elif has_alternative_code:
        status = STATUS_FULL
        reason = (
            f"Kode {'/'.join(active_codes)} terpenuhi oleh evidence pada minimal satu Grade "
            f"({', '.join(filled_grades)})."
        )
    elif missing_before_highest:
        status = STATUS_PARTIAL
        reason = (
            "Evidence SPIP belum berurutan mulai Grade E; lengkapi Grade "
            f"{', '.join(missing_before_highest)} sebelum {highest_filled_grade}."
        )
    else:
        status = STATUS_FULL
        reason = (
            "Evidence SPIP sudah berurutan mulai Grade E sampai Grade "
            f"{highest_filled_grade}."
        )

    return {
        "status": status,
        "reason": reason,
        "active_codes": active_codes,
        "rule_mode": rule_mode,
        "filled_grades": filled_grades,
        "missing_grades_before_highest": missing_before_highest,
        "highest_filled_grade": highest_filled_grade,
        "evidence_file_count": file_count,
        "has_scan_error": bool(scan_errors),
    }


def evaluate_subunsur_status(
    parameters: Iterable[dict],
    slots: Iterable[dict],
    *,
    unassigned_file_count: int = 0,
) -> dict:
    """Roll parameter results into the three user-facing subunsur statuses."""

    parameter_list = list(parameters)
    slot_list = list(slots)
    results = [evaluate_parameter_status(parameter, slot_list) for parameter in parameter_list]
    total_evidence = sum(item["evidence_file_count"] for item in results) + max(unassigned_file_count, 0)
    full_count = sum(item["status"] == STATUS_FULL for item in results)
    has_scan_error = any(item["has_scan_error"] for item in results)

    if has_scan_error:
        status = STATUS_PARTIAL
        reason = "Status belum dapat dipastikan karena sebagian folder Grade gagal dibaca."
    elif unassigned_file_count > 0:
        status = STATUS_PARTIAL
        reason = (
            f"Ada {unassigned_file_count} file yang belum ditempatkan pada Grade parameter; "
            "pindahkan file ke folder Grade yang sesuai."
        )
    elif not parameter_list:
        status = STATUS_EMPTY if total_evidence == 0 else STATUS_PARTIAL
        reason = (
            "Parameter belum tersedia dan belum ada evidence."
            if status == STATUS_EMPTY
            else "Evidence ditemukan, tetapi parameter belum tersedia untuk menentukan kelengkapannya."
        )
    elif total_evidence == 0:
        status = STATUS_EMPTY
        reason = "Belum ada evidence pada seluruh parameter subunsur ini."
    elif full_count == len(results):
        status = STATUS_FULL
        reason = f"Seluruh {full_count} parameter telah memenuhi aturan evidence kode parameternya."
    else:
        status = STATUS_PARTIAL
        remaining_count = len(results) - full_count
        reason = (
            f"{full_count} dari {len(results)} parameter sudah penuh; "
            f"{remaining_count} parameter masih perlu dilengkapi atau diperiksa."
        )

    return {
        "status": status,
        "reason": reason,
        "parameter_results": results,
        "full_parameter_count": full_count,
        "total_parameter_count": len(results),
        "evidence_file_count": total_evidence,
    }


def attach_parameter_progress(parameters: list[dict], slots: list[dict]) -> None:
    """Add derived status fields while preserving the existing parameter/grade contract."""

    slot_map: dict[tuple[str, str], list[dict]] = {}
    for slot in slots:
        key = (
            str(slot.get("detail_kode") or "").strip(),
            str(slot.get("grade") or "").strip().upper(),
        )
        slot_map.setdefault(key, []).append(slot)

    for parameter in parameters:
        detail_kode = str(parameter.get("detail_kode") or "").strip()
        for grade in parameter.get("grades", []):
            grade_value = str(grade.get("grade") or "").strip().upper()
            grade["evidence_folders"] = slot_map.get((detail_kode, grade_value), [])

        result = evaluate_parameter_status(parameter, slots)
        parameter["evidence_status"] = result["status"]
        parameter["evidence_status_reason"] = result["reason"]
        parameter["active_codes"] = result["active_codes"]
        parameter["evidence_rule"] = result["rule_mode"]
        parameter["filled_grades"] = result["filled_grades"]
        parameter["missing_grades_before_highest"] = result["missing_grades_before_highest"]
        parameter["highest_filled_grade"] = result["highest_filled_grade"]
        parameter["evidence_file_count"] = result["evidence_file_count"]
