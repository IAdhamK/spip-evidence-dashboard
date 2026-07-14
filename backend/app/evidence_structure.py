from __future__ import annotations

import re


GRADE_ORDER = ("A", "B", "C", "D", "E")
PARAMETER_FOLDER_MAX_LENGTH = 118


def safe_segment(value: str, max_length: int = 118) -> str:
    text = value.replace("/", "-")
    text = re.sub(r"[\x00-\x1f:<>\"|?*]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip(" .") + "_"


def parameter_folder(detail_kode: str, uraian: str) -> str:
    label = f"{detail_kode} {uraian}"
    label = re.sub(r"\bdiberikan\s*/\s*dikuasakan\b", "diberikan atau dikuasakan", label)
    return safe_segment(label, max_length=PARAMETER_FOLDER_MAX_LENGTH)


def canonical_folder_path(folder_path: str) -> str:
    """Keep public links aligned with the physical WebDAV folder segments."""
    return "/".join(
        safe_segment(part, max_length=PARAMETER_FOLDER_MAX_LENGTH)
        for part in str(folder_path or "").strip("/").split("/")
        if part.strip()
    )


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
