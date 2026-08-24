from __future__ import annotations

from dataclasses import dataclass


AMBIGUOUS_NAME_PARTS = [
    "scan",
    "scanned",
    "dokumen baru",
    "document",
    "new document",
    "untitled",
    "image",
    "whatsapp",
    "img_",
    "copy of",
]


@dataclass(frozen=True)
class StatusResult:
    status: str
    reason: str
    ambiguous_files: list[str]


def find_ambiguous_files(file_names: list[str]) -> list[str]:
    ambiguous: list[str] = []
    for name in file_names:
        lowered = name.lower()
        stem = lowered.rsplit(".", 1)[0]
        if any(part in lowered for part in AMBIGUOUS_NAME_PARTS):
            ambiguous.append(name)
        elif stem.isdigit() or len(stem) <= 3:
            ambiguous.append(name)
    return ambiguous


def classify_folder(file_names: list[str]) -> StatusResult:
    count = len(file_names)
    ambiguous = find_ambiguous_files(file_names)

    if ambiguous:
        return StatusResult(
            status="Terisi Sebagian",
            reason=f"{len(ambiguous)} file sudah ada, tetapi namanya perlu dicek ulang.",
            ambiguous_files=ambiguous,
        )
    if count == 0:
        return StatusResult(
            status="Kosong",
            reason="Folder belum memiliki file evidence.",
            ambiguous_files=[],
        )
    return StatusResult(
        status="Terisi Sebagian",
        reason="File sudah ada, tetapi kelengkapannya harus dinilai terhadap kode dan Grade parameter.",
        ambiguous_files=[],
    )
