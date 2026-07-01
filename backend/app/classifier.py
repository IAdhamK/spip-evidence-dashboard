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
            status="Perlu Kurasi",
            reason=f"{len(ambiguous)} file memiliki nama yang perlu dicek ulang.",
            ambiguous_files=ambiguous,
        )
    if count == 0:
        return StatusResult(
            status="Kosong",
            reason="Folder belum memiliki file evidence.",
            ambiguous_files=[],
        )
    if count < 4:
        return StatusResult(
            status="Terisi Sebagian",
            reason="Jumlah file belum mencapai acuan minimal empat kategori evidence.",
            ambiguous_files=[],
        )
    return StatusResult(
        status="Terisi",
        reason="Jumlah file sudah memenuhi acuan minimal awal.",
        ambiguous_files=[],
    )

