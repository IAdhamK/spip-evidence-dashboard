from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evidence_structure import (  # noqa: E402
    grade_folder,
    parameter_folder,
)

PARAMETERS_PATH = ROOT / "backend" / "app" / "spip_parameters.json"
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_ZIP = OUTPUT_DIR / "struktur_folder_evidence_spip_kertas_kerja.zip"
ZIP_ROOT = "SPIP_EVIDENCE_KERTAS_KERJA"

KK_FOLDERS = {
    "KK3.1": "KK 3.1 EFEKTIVITAS DAN EFISIENSI PENCAPAIAN TUJUAN ORGANISASI",
    "KK3.2": "KK 3.2 KEANDALAN PELAPORAN KEUANGAN",
    "KK3.3": "KK 3.3 PENGAMANAN ASET NEGARA DAERAH",
    "KK3.4": "KK 3.4 KETAATAN PADA PERATURAN PERUNDANG UNDANGAN",
}

def add_dir(zip_file: zipfile.ZipFile, path: str, seen: set[str]) -> None:
    normalized = path.rstrip("/") + "/"
    if normalized in seen:
        return
    zip_file.writestr(normalized, "")
    seen.add(normalized)


def manifest_csv(rows: list[dict[str, str]]) -> str:
    output = io.StringIO()
    fieldnames = [
        "kk_id",
        "kode_subunsur",
        "nama_subunsur",
        "detail_kode",
        "parameter_no",
        "grade",
        "uraian_parameter",
        "folder_path",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def build_zip() -> None:
    payload = json.loads(PARAMETERS_PATH.read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []
    seen_dirs: set[str] = set()

    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        add_dir(zip_file, ZIP_ROOT, seen_dirs)
        for kk_id, subunsur_map in payload.items():
            kk_folder = KK_FOLDERS.get(kk_id, kk_id)
            kk_path = f"{ZIP_ROOT}/{kk_folder}"
            add_dir(zip_file, kk_path, seen_dirs)

            for kode, group in sorted(subunsur_map.items(), key=lambda item: natural_key(item[0])):
                subunsur_name = group.get("matrix_subunsur_name", "")
                subunsur_folder = parameter_folder(kode, subunsur_name)
                subunsur_path = f"{kk_path}/{subunsur_folder}"
                add_dir(zip_file, subunsur_path, seen_dirs)

                for parameter in group.get("parameters", []):
                    parameter_no = str(parameter.get("no", "")).strip()
                    detail_kode = parameter.get("detail_kode") or f"{kode}.{parameter_no}"
                    uraian = parameter.get("uraian", "")
                    parameter_path = f"{subunsur_path}/{parameter_folder(detail_kode, uraian)}"
                    add_dir(zip_file, parameter_path, seen_dirs)

                    for grade in parameter.get("grades", []):
                        grade_value = str(grade.get("grade") or "").strip().upper()
                        if not grade_value:
                            continue
                        grade_path = f"{parameter_path}/{grade_folder(grade_value)}"
                        add_dir(zip_file, grade_path, seen_dirs)
                        manifest_rows.append(
                            {
                                "kk_id": kk_id,
                                "kode_subunsur": kode,
                                "nama_subunsur": subunsur_name,
                                "detail_kode": detail_kode,
                                "parameter_no": parameter_no,
                                "grade": grade_value,
                                "uraian_parameter": uraian,
                                "folder_path": grade_path,
                            }
                        )

        zip_file.writestr(f"{ZIP_ROOT}/00_MANIFEST_STRUKTUR.csv", manifest_csv(manifest_rows))
        zip_file.writestr(
            f"{ZIP_ROOT}/00_README_UPLOAD.txt",
            "\n".join(
                [
                    "Struktur folder evidence SPIP berdasarkan Kertas Kerja PM SPIP 2026.",
                    "",
                    "Pola folder:",
                    "KK -> Subunsur -> Detail parameter -> Grade.",
                    "",
                    "File evidence diupload langsung ke folder Grade A-E masing-masing.",
                    "",
                    "Gunakan 00_MANIFEST_STRUKTUR.csv untuk audit mapping folder dengan kertas kerja.",
                ]
            ),
        )

    print(f"ZIP: {OUTPUT_ZIP}")
    print(f"Parameters: {len(manifest_rows)}")
    print(f"Folders: {len(seen_dirs)}")


def natural_key(value: str) -> tuple[int, int]:
    left, _, right = value.partition(".")
    return (int(left or 0), int(right or 0))


if __name__ == "__main__":
    build_zip()
