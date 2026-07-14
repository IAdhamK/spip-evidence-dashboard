from __future__ import annotations

import re


GRADE_ORDER = ("A", "B", "C", "D", "E")
PARAMETER_FOLDER_MAX_LENGTH = 118
SPECIAL_KK31_310_ROOT = "KK 3.1 EFEKTIVITAS DAN EFISIENSI PENCAPAIAN TUJUAN ORGANISASI"
SPECIAL_KK31_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya"
SPECIAL_KK31_310_PARAMETER_SOURCE = (
    "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola sumber daya "
    "yang diberikan/dikuasakan kepadanya dalam rangka pencapaian tujuan organisasi"
)
SPECIAL_KK31_310_PARAMETER = (
    "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola sumber daya "
    "yang diberikan-dikuasak_"
)
SPECIAL_KK32_310_ROOT = "KK 3.2 KEANDALAN PELAPORAN KEUANGAN"
SPECIAL_KK32_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya"
SPECIAL_KK32_310_PARAMETER = (
    "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola sumber daya "
    "keuangan yang diberikan atau dikuasakan kepadanya dalam rangka pencapaian tujuan organisasi"
)
SPECIAL_KK33_310_ROOT = "KK 3.3 PENGAMANAN ASET NEGARA DAERAH"
SPECIAL_KK33_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya"
SPECIAL_KK33_310_PARAMETER_SOURCE = (
    "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola aset yang "
    "diberikan/dikuasakan kepadanya dalam rangka pencapaian tujuan organisasi"
)
SPECIAL_KK33_310_PARAMETER = (
    "3.10.1 Terdapat pertanggungjawaban seseorang atau unit organisasi dalam mengelola aset yang "
    "diberikan-dikuasakan kepa_"
)
SPECIAL_KK34_310_ROOT = "KK 3.4 KETAATAN PADA PERATURAN PERUNDANG UNDANGAN"
SPECIAL_KK34_310_SUBUNSUR = "3.10 Akuntabilitas terhadap Sumber Daya dan Pencatatannya"
SPECIAL_KK34_310_PARAMETER = SPECIAL_KK31_310_PARAMETER

SPECIAL_310_FOLDER_OVERRIDES = {
    (SPECIAL_KK31_310_ROOT.casefold(), SPECIAL_KK31_310_SUBUNSUR.casefold()): (
        SPECIAL_KK31_310_ROOT,
        SPECIAL_KK31_310_SUBUNSUR,
        SPECIAL_KK31_310_PARAMETER,
    ),
    (SPECIAL_KK32_310_ROOT.casefold(), SPECIAL_KK32_310_SUBUNSUR.casefold()): (
        SPECIAL_KK32_310_ROOT,
        SPECIAL_KK32_310_SUBUNSUR,
        SPECIAL_KK32_310_PARAMETER,
    ),
    (SPECIAL_KK33_310_ROOT.casefold(), SPECIAL_KK33_310_SUBUNSUR.casefold()): (
        SPECIAL_KK33_310_ROOT,
        SPECIAL_KK33_310_SUBUNSUR,
        SPECIAL_KK33_310_PARAMETER,
    ),
    (SPECIAL_KK34_310_ROOT.casefold(), SPECIAL_KK34_310_SUBUNSUR.casefold()): (
        SPECIAL_KK34_310_ROOT,
        SPECIAL_KK34_310_SUBUNSUR,
        SPECIAL_KK34_310_PARAMETER,
    ),
}


def safe_segment(value: str, max_length: int = 118) -> str:
    text = value.replace("/", "-")
    text = re.sub(r"[\x00-\x1f:<>\"|?*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip(" .") + "_"


def parameter_folder(detail_kode: str, uraian: str) -> str:
    label = f"{detail_kode} {uraian}"
    if label.casefold() == SPECIAL_KK31_310_PARAMETER_SOURCE.casefold():
        return SPECIAL_KK31_310_PARAMETER
    if label.casefold() == SPECIAL_KK33_310_PARAMETER_SOURCE.casefold():
        return SPECIAL_KK33_310_PARAMETER
    label = re.sub(r"\bdiberikan\s*/\s*dikuasakan\b", "diberikan atau dikuasakan", label)
    if label.casefold() == SPECIAL_KK32_310_PARAMETER.casefold():
        return SPECIAL_KK32_310_PARAMETER
    return safe_segment(label, max_length=PARAMETER_FOLDER_MAX_LENGTH)


def canonical_folder_path(folder_path: str) -> str:
    """Keep public links aligned with the physical WebDAV folder segments."""
    parts = [part for part in str(folder_path or "").strip("/").split("/") if part.strip()]
    canonical_parts = [
        safe_segment(part, max_length=PARAMETER_FOLDER_MAX_LENGTH)
        for part in parts
    ]

    # Keempat KK mempunyai nama fisik 3.10.1 yang tidak seragam. Pulihkan path
    # lama berdasarkan KK dan subunsur, lalu pertahankan Grade A-E sesudahnya.
    if len(parts) >= 3 and parts[2].strip().casefold().startswith("3.10.1 "):
        override = SPECIAL_310_FOLDER_OVERRIDES.get(
            (parts[0].strip().casefold(), parts[1].strip().casefold())
        )
        if override:
            canonical_parts[0], canonical_parts[1], canonical_parts[2] = override

    return "/".join(canonical_parts)


def grade_folder(grade: str) -> str:
    return safe_segment(f"Grade {grade}")


def slot_folder_path(
    subunsur_folder_path: str,
    detail_kode: str,
    uraian: str,
    grade: str,
) -> str:
    return "/".join(
        [
            subunsur_folder_path.strip("/"),
            parameter_folder(detail_kode, uraian),
            grade_folder(grade),
        ]
    )
