from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.spip_mapping import SUBUNSUR_LIST  # noqa: E402

DEFAULT_WORKBOOK = Path("/Users/m/Downloads/Kertas Kerja PM SPIP 2026.xlsx")
OUTPUT_PATH = ROOT / "backend" / "app" / "spip_parameters.json"
KK_SHEETS = ("KK3.1", "KK3.2", "KK3.3", "KK3.4")
GRADE_VALUES = {"A", "B", "C", "D", "E"}


def clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", "\n").split())


def clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


SUBUNSUR_BY_NAME = {
    clean_name(item.nama): item.kode
    for item in SUBUNSUR_LIST
}


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", "\n").strip()


def is_subunsur_code(value: Any) -> bool:
    text = clean(value)
    if "." not in text:
        return False
    left, right = text.split(".", 1)
    return left.isdigit() and right.isdigit()


def subunsur_code(value: Any, name: str) -> str:
    mapped = SUBUNSUR_BY_NAME.get(clean_name(name))
    if mapped:
        return mapped
    text = clean(value)
    return text if is_subunsur_code(text) else ""


def parameter_no(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean(value)


def grade_payload(row: tuple[Any, ...]) -> dict[str, str]:
    return {
        "grade": clean(row[7]),
        "kode_parameter": {
            "spip": clean(row[4]),
            "mri": clean(row[5]),
            "iepk": clean(row[6]),
        },
        "kriteria": compact_text(row[8]),
        "penjelasan": compact_text(row[9]),
        "cara_pengujian": clean(row[10]),
    }


def extract_sheet(ws) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    current_kode = ""
    current_parameter: dict[str, Any] | None = None

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        values = tuple(row[:11])
        raw_subunsur_code = clean(values[0])
        subunsur_name = clean(values[1])
        detected_subunsur_code = subunsur_code(values[0], subunsur_name)
        grade = clean(values[7]).upper()

        if detected_subunsur_code and subunsur_name:
            current_kode = detected_subunsur_code
            current_parameter = None
            groups.setdefault(
                current_kode,
                {
                    "matrix_subunsur_name": subunsur_name,
                    "parameters": [],
                },
            )
            continue

        if not current_kode or grade not in GRADE_VALUES:
            continue

        no = parameter_no(values[2])
        uraian = compact_text(values[3])
        if no and uraian:
            current_parameter = {
                "source_row": row_idx,
                "no": no,
                "detail_kode": f"{current_kode}.{no}",
                "uraian": uraian,
                "kode_parameter": {
                    "spip": clean(values[4]),
                    "mri": clean(values[5]),
                    "iepk": clean(values[6]),
                },
                "grades": [],
                "cara_pengujian": clean(values[10]),
            }
            groups[current_kode]["parameters"].append(current_parameter)

        if current_parameter is None:
            continue

        grade_item = grade_payload(values)
        current_parameter["grades"].append(grade_item)

        if len(current_parameter["grades"]) == 1:
            current_parameter["grade_sample"] = grade_item["grade"]
            current_parameter["kriteria_sample"] = grade_item["kriteria"]
            current_parameter["penjelasan_sample"] = grade_item["penjelasan"]

    return groups


def main() -> None:
    workbook_path = DEFAULT_WORKBOOK
    wb = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    payload = {sheet: extract_sheet(wb[sheet]) for sheet in KK_SHEETS}
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    total_parameters = sum(
        len(group.get("parameters", []))
        for sheet_payload in payload.values()
        for group in sheet_payload.values()
    )
    total_grades = sum(
        len(parameter.get("grades", []))
        for sheet_payload in payload.values()
        for group in sheet_payload.values()
        for parameter in group.get("parameters", [])
    )
    print(f"Exported {total_parameters} parameters and {total_grades} grade rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
