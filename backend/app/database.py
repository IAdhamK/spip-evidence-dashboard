from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from app.evidence_structure import canonical_folder_path, parameter_folder, slot_folder_path
from app.spip_mapping import KK_LIST, SUBUNSUR_LIST
from app.webdav_client import canonical_public_folder_url


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

CREATE TABLE IF NOT EXISTS smart_upload_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    preview_text TEXT NOT NULL DEFAULT '',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    file_bytes BLOB,
    ai_status TEXT NOT NULL DEFAULT 'skipped',
    ai_message TEXT,
    upload_status TEXT NOT NULL DEFAULT 'pending',
    upload_message TEXT,
    confirmed_candidate_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS smart_upload_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    candidate_index INTEGER,
    candidate_json TEXT,
    action_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (review_id) REFERENCES smart_upload_reviews (id)
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
            smart_upload_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(smart_upload_reviews)").fetchall()
            }
            smart_upload_defaults = {
                "file_bytes": "BLOB",
                "upload_status": "TEXT NOT NULL DEFAULT 'pending'",
                "upload_message": "TEXT",
                "confirmed_candidate_json": "TEXT",
            }
            for column_name, column_type in smart_upload_defaults.items():
                if column_name not in smart_upload_columns:
                    conn.execute(f"ALTER TABLE smart_upload_reviews ADD COLUMN {column_name} {column_type}")

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
            expected_slots: set[tuple[str, str, str, str, str]] = set()
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
                            category_name = "Evidence Grade"
                            expected_slots.add((kk_id, kode, detail_kode, grade_value, category_name))
                            conn.execute(
                                """
                                INSERT INTO evidence_slots (
                                    kk_id, kode, detail_kode, parameter_no, grade,
                                    category_name, category_folder, folder_path
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(kk_id, kode, detail_kode, grade, category_name) DO UPDATE SET
                                    parameter_no=excluded.parameter_no,
                                    category_folder=excluded.category_folder,
                                    public_url=CASE
                                        WHEN evidence_slots.folder_path = excluded.folder_path
                                        THEN evidence_slots.public_url
                                        ELSE NULL
                                    END,
                                    file_count=CASE
                                        WHEN evidence_slots.folder_path = excluded.folder_path
                                        THEN evidence_slots.file_count
                                        ELSE 0
                                    END,
                                    total_size_bytes=CASE
                                        WHEN evidence_slots.folder_path = excluded.folder_path
                                        THEN evidence_slots.total_size_bytes
                                        ELSE 0
                                    END,
                                    last_scanned_at=CASE
                                        WHEN evidence_slots.folder_path = excluded.folder_path
                                        THEN evidence_slots.last_scanned_at
                                        ELSE NULL
                                    END,
                                    error_message=CASE
                                        WHEN evidence_slots.folder_path = excluded.folder_path
                                        THEN evidence_slots.error_message
                                        ELSE NULL
                                    END,
                                    folder_path=excluded.folder_path
                                """,
                                (
                                    kk_id,
                                    kode,
                                    detail_kode,
                                    parameter_no,
                                    grade_value,
                                    category_name,
                                    "",
                                    path,
                                ),
                            )
            existing_slots = conn.execute(
                "SELECT kk_id, kode, detail_kode, grade, category_name FROM evidence_slots"
            ).fetchall()
            for row in existing_slots:
                key = (row["kk_id"], row["kode"], row["detail_kode"], row["grade"], row["category_name"])
                if key in expected_slots:
                    continue
                conn.execute(
                    """
                    DELETE FROM evidence_slots
                    WHERE kk_id = ? AND kode = ? AND detail_kode = ? AND grade = ? AND category_name = ?
                    """,
                    key,
                )

    def normalize_lumbung_links(self) -> None:
        with self.connect() as conn:
            for table in ("folders", "evidence_slots"):
                rows = conn.execute(
                    f"SELECT rowid, folder_path, public_url FROM {table}"
                ).fetchall()
                for row in rows:
                    folder_path = canonical_folder_path(row["folder_path"])
                    public_url = canonical_public_folder_url(row["public_url"], folder_path)
                    conn.execute(
                        f"""
                        UPDATE {table}
                        SET folder_path = ?, public_url = ?
                        WHERE rowid = ?
                        """,
                        (folder_path, public_url, row["rowid"]),
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

    def parameter_folder_entry(self, kk_id: str, kode: str) -> dict | None:
        with self.connect() as conn:
            folder = conn.execute(
                "SELECT folder_path FROM folders WHERE kk_id = ? AND kode = ?",
                (kk_id, kode),
            ).fetchone()
            if not folder:
                return None

            parameter_count = conn.execute(
                "SELECT COUNT(*) AS total FROM parameters WHERE kk_id = ? AND kode = ?",
                (kk_id, kode),
            ).fetchone()["total"]
            parameter = conn.execute(
                """
                SELECT parameter_no, uraian
                FROM parameters
                WHERE kk_id = ? AND kode = ?
                ORDER BY source_row, id
                LIMIT 1
                """,
                (kk_id, kode),
            ).fetchone()
            if not parameter:
                return None

            parameter_no = str(parameter["parameter_no"] or "").strip()
            detail_kode = f"{kode}.{parameter_no}" if parameter_no else kode
            folder_path = "/".join(
                [
                    folder["folder_path"].strip("/"),
                    parameter_folder(detail_kode, parameter["uraian"] or ""),
                ]
            )
            return {
                "detail_kode": detail_kode,
                "folder_path": folder_path,
                "parameter_count": parameter_count,
            }

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


    def record_smart_upload_review(
        self,
        file_name: str,
        content_type: str | None,
        size_bytes: int,
        preview_text: str,
        candidates: list[dict],
        ai_status: str,
        ai_message: str | None,
        payload: bytes | None = None,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO smart_upload_reviews (
                    file_name, content_type, size_bytes, preview_text,
                    candidates_json, ai_status, ai_message, file_bytes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_name, content_type, size_bytes, preview_text, json.dumps(candidates, ensure_ascii=False), ai_status, ai_message, payload),
            )
            return int(cursor.lastrowid)


    def smart_upload_review(self, review_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM smart_upload_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            return dict(row) if row else None


    def record_smart_upload_action(
        self,
        review_id: int,
        action_type: str,
        candidate_index: int | None,
        candidate: dict | None,
        action_message: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO smart_upload_actions (
                    review_id, action_type, candidate_index, candidate_json, action_message
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    action_type,
                    candidate_index,
                    json.dumps(candidate, ensure_ascii=False) if candidate else None,
                    action_message,
                ),
            )
            return int(cursor.lastrowid)

    def smart_upload_actions(self, review_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM smart_upload_actions
                WHERE review_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (review_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_smart_upload_confirmed(
        self,
        review_id: int,
        candidate: dict,
        upload_status: str,
        upload_message: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE smart_upload_reviews
                SET upload_status = ?, upload_message = ?,
                    confirmed_candidate_json = ?, confirmed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (upload_status, upload_message, json.dumps(candidate, ensure_ascii=False), review_id),
            )
