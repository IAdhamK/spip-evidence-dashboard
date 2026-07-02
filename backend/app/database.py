from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from app.evidence_structure import parameter_folder, slot_folder_path
from app.spip_mapping import KK_LIST, SUBUNSUR_LIST


SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    kk_id TEXT NOT NULL,
    kode TEXT NOT NULL,
    kk_title TEXT NOT NULL,
    kk_folder TEXT NOT NULL,
    subunsur_name TEXT NOT NULL,
    unsur TEXT NOT NULL,
    evidence_hint TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    public_url TEXT,
    status TEXT NOT NULL DEFAULT 'Kosong',
    status_reason TEXT NOT NULL DEFAULT 'Belum pernah disinkronkan.',
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    error_message TEXT,
    PRIMARY KEY (kk_id, kode)
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kk_id TEXT NOT NULL,
    kode TEXT NOT NULL,
    name TEXT NOT NULL,
    href TEXT NOT NULL,
    is_folder INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER,
    mime_type TEXT,
    modified_at TEXT,
    FOREIGN KEY (kk_id, kode) REFERENCES folders (kk_id, kode)
);

CREATE TABLE IF NOT EXISTS parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kk_id TEXT NOT NULL,
    kode TEXT NOT NULL,
    matrix_subunsur_name TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    parameter_no TEXT,
    uraian TEXT NOT NULL,
    kode_spip TEXT,
    kode_mri TEXT,
    kode_iepk TEXT,
    grade_sample TEXT,
    kriteria_sample TEXT,
    penjelasan_sample TEXT,
    grades_json TEXT NOT NULL DEFAULT '[]',
    cara_pengujian TEXT,
    UNIQUE (kk_id, kode, source_row, uraian)
);

CREATE TABLE IF NOT EXISTS evidence_slots (
    kk_id TEXT NOT NULL,
    kode TEXT NOT NULL,
    detail_kode TEXT NOT NULL,
    parameter_no TEXT NOT NULL,
    grade TEXT NOT NULL,
    category_name TEXT NOT NULL,
    category_folder TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    public_url TEXT,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    error_message TEXT,
    PRIMARY KEY (kk_id, kode, detail_kode, grade, category_name)
);
"""


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(parameters)").fetchall()
            }
            if "grades_json" not in columns:
                conn.execute("ALTER TABLE parameters ADD COLUMN grades_json TEXT NOT NULL DEFAULT '[]'")

    def ensure_mapping(self) -> None:
        with self.connect() as conn:
            for kk in KK_LIST:
                for subunsur in SUBUNSUR_LIST:
                    folder_path = f"{kk.folder_name}/{subunsur.kode} {subunsur.nama}"
                    conn.execute(
                        """
                        INSERT INTO folders (
                            kk_id, kode, kk_title, kk_folder, subunsur_name, unsur,
                            evidence_hint, folder_path
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(kk_id, kode) DO UPDATE SET
                            kk_title=excluded.kk_title,
                            kk_folder=excluded.kk_folder,
                            subunsur_name=excluded.subunsur_name,
                            unsur=excluded.unsur,
                            evidence_hint=excluded.evidence_hint,
                            folder_path=excluded.folder_path
                        """,
                        (
                            kk.id,
                            subunsur.kode,
                            kk.title,
                            kk.folder_name,
                            subunsur.nama,
                            subunsur.unsur,
                            subunsur.evidence_hint,
                            folder_path,
                        ),
                    )

    def ensure_parameters(self) -> None:
        parameter_path = Path(__file__).with_name("spip_parameters.json")
        if not parameter_path.exists():
            return

        payload = json.loads(parameter_path.read_text(encoding="utf-8"))
        with self.connect() as conn:
            conn.execute("DELETE FROM parameters")
            conn.execute("DELETE FROM evidence_slots")
            for kk_id, codes in payload.items():
                for kode, group in codes.items():
                    matrix_subunsur_name = group.get("matrix_subunsur_name", "")
                    folder_row = conn.execute(
                        "SELECT kk_folder, folder_path FROM folders WHERE kk_id = ? AND kode = ?",
                        (kk_id, kode),
                    ).fetchone()
                    subunsur_folder_path = folder_row["folder_path"] if folder_row else ""
                    if folder_row and matrix_subunsur_name:
                        subunsur_folder_path = "/".join(
                            [
                                folder_row["kk_folder"].strip("/"),
                                parameter_folder(kode, matrix_subunsur_name),
                            ]
                        )
                        conn.execute(
                            """
                            UPDATE folders
                            SET folder_path = ?
                            WHERE kk_id = ? AND kode = ?
                            """,
                            (subunsur_folder_path, kk_id, kode),
                        )
                    for item in group.get("parameters", []):
                        kode_parameter = item.get("kode_parameter", {})
                        parameter_no = str(item.get("no") or "").strip()
                        detail_kode = item.get("detail_kode") or f"{kode}.{parameter_no}"
                        conn.execute(
                            """
                            INSERT INTO parameters (
                                kk_id, kode, matrix_subunsur_name, source_row, parameter_no,
                                uraian, kode_spip, kode_mri, kode_iepk, grade_sample,
                                kriteria_sample, penjelasan_sample, grades_json, cara_pengujian
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                kk_id,
                                kode,
                                matrix_subunsur_name,
                                item.get("source_row"),
                                item.get("no"),
                                item.get("uraian", ""),
                                kode_parameter.get("spip"),
                                kode_parameter.get("mri"),
                                kode_parameter.get("iepk"),
                                item.get("grade_sample"),
                                item.get("kriteria_sample"),
                                item.get("penjelasan_sample"),
                                json.dumps(item.get("grades", []), ensure_ascii=False),
                                item.get("cara_pengujian"),
                            ),
                        )
                        for grade in item.get("grades", []):
                            grade_value = str(grade.get("grade") or "").strip().upper()
                            if not grade_value:
                                continue
                            path = slot_folder_path(
                                subunsur_folder_path,
                                detail_kode,
                                item.get("uraian", ""),
                                grade_value,
                            )
                            conn.execute(
                                """
                                INSERT INTO evidence_slots (
                                    kk_id, kode, detail_kode, parameter_no, grade,
                                    category_name, category_folder, folder_path
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    kk_id,
                                    kode,
                                    detail_kode,
                                    parameter_no,
                                    grade_value,
                                    "Evidence Grade",
                                    "",
                                    path,
                                ),
                            )

    def folders(self, kk_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM folders"
        params: tuple[str, ...] = ()
        if kk_id:
            query += " WHERE kk_id = ?"
            params = (kk_id,)
        query += " ORDER BY kk_id, CAST(substr(kode, 1, instr(kode, '.') - 1) AS INTEGER), CAST(substr(kode, instr(kode, '.') + 1) AS INTEGER)"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def folder(self, kk_id: str, kode: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM folders WHERE kk_id = ? AND kode = ?",
                (kk_id, kode),
            ).fetchone()
            return dict(row) if row else None

    def files(self, kk_id: str, kode: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE kk_id = ? AND kode = ? ORDER BY is_folder DESC, name",
                (kk_id, kode),
            ).fetchall()
            return [dict(row) for row in rows]

    def parameters(self, kk_id: str, kode: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, kk_id, kode, matrix_subunsur_name, source_row, parameter_no,
                       uraian, kode_spip, kode_mri, kode_iepk, grade_sample,
                       kriteria_sample, penjelasan_sample, grades_json, cara_pengujian
                FROM parameters
                WHERE kk_id = ? AND kode = ?
                ORDER BY source_row, id
                """,
                (kk_id, kode),
            ).fetchall()
            parameters = []
            for row in rows:
                item = dict(row)
                item["grades"] = json.loads(item.pop("grades_json") or "[]")
                item["detail_kode"] = f"{item['kode']}.{item['parameter_no']}" if item.get("parameter_no") else item["kode"]
                parameters.append(item)
            return parameters

    def evidence_slots(self, kk_id: str, kode: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM evidence_slots
                WHERE kk_id = ? AND kode = ?
                ORDER BY detail_kode, grade, category_folder
                """,
                (kk_id, kode),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_evidence_slot_scan(
        self,
        kk_id: str,
        kode: str,
        detail_kode: str,
        grade: str,
        category_name: str,
        public_url: str,
        file_count: int,
        total_size_bytes: int,
        scanned_at: str,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE evidence_slots
                SET public_url = ?, file_count = ?, total_size_bytes = ?,
                    last_scanned_at = ?, error_message = ?
                WHERE kk_id = ? AND kode = ? AND detail_kode = ? AND grade = ? AND category_name = ?
                """,
                (
                    public_url,
                    file_count,
                    total_size_bytes,
                    scanned_at,
                    error_message,
                    kk_id,
                    kode,
                    detail_kode,
                    grade,
                    category_name,
                ),
            )

    def update_folder_rollup(
        self,
        kk_id: str,
        kode: str,
        status: str,
        status_reason: str,
        file_count: int,
        total_size_bytes: int,
        scanned_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE folders
                SET status = ?, status_reason = ?, file_count = ?,
                    total_size_bytes = ?, last_scanned_at = ?, error_message = NULL
                WHERE kk_id = ? AND kode = ?
                """,
                (
                    status,
                    status_reason,
                    file_count,
                    total_size_bytes,
                    scanned_at,
                    kk_id,
                    kode,
                ),
            )

    def replace_scan_result(
        self,
        kk_id: str,
        kode: str,
        files: list[dict],
        status: str,
        status_reason: str,
        public_url: str,
        scanned_at: str,
        error_message: str | None = None,
    ) -> None:
        file_count = sum(1 for item in files if not item["is_folder"])
        total_size = sum(item["size_bytes"] or 0 for item in files if not item["is_folder"])
        with self.connect() as conn:
            conn.execute("DELETE FROM files WHERE kk_id = ? AND kode = ?", (kk_id, kode))
            conn.executemany(
                """
                INSERT INTO files (kk_id, kode, name, href, is_folder, size_bytes, mime_type, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        kk_id,
                        kode,
                        item["name"],
                        item["href"],
                        1 if item["is_folder"] else 0,
                        item["size_bytes"],
                        item["mime_type"],
                        item["modified_at"],
                    )
                    for item in files
                ],
            )
            conn.execute(
                """
                UPDATE folders
                SET status = ?, status_reason = ?, file_count = ?, total_size_bytes = ?,
                    public_url = ?, last_scanned_at = ?, error_message = ?
                WHERE kk_id = ? AND kode = ?
                """,
                (
                    status,
                    status_reason,
                    file_count,
                    total_size,
                    public_url,
                    scanned_at,
                    error_message,
                    kk_id,
                    kode,
                ),
            )

    def mark_scan_error(self, kk_id: str, kode: str, message: str, scanned_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE folders
                SET error_message = ?, last_scanned_at = ?, status_reason = ?
                WHERE kk_id = ? AND kode = ?
                """,
                (message, scanned_at, message, kk_id, kode),
            )
