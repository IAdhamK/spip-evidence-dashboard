from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Iterator

from app.evidence_structure import canonical_folder_path, parameter_folder, slot_folder_path
from app.migrations import run_migrations
from app.spip_mapping import KK_LIST, SUBUNSUR_LIST
from app.webdav_client import canonical_public_folder_url


def normalize_lumbung_value(value):
    if isinstance(value, list):
        return [normalize_lumbung_value(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {
        key: normalize_lumbung_value(item)
        for key, item in value.items()
    }

    folder_path = str(normalized.get("folder_path") or "").strip()
    if folder_path:
        normalized["folder_path"] = canonical_folder_path(folder_path)
        folder_path = normalized["folder_path"]
    if normalized.get("public_url"):
        normalized["public_url"] = canonical_public_folder_url(normalized.get("public_url"), folder_path)

    parameter_path = str(normalized.get("parameter_entry_folder_path") or "").strip()
    if parameter_path:
        normalized["parameter_entry_folder_path"] = canonical_folder_path(parameter_path)
        parameter_path = normalized["parameter_entry_folder_path"]
    if normalized.get("parameter_entry_public_url"):
        normalized["parameter_entry_public_url"] = canonical_public_folder_url(
            normalized.get("parameter_entry_public_url"),
            parameter_path,
        )

    return normalized


def normalize_lumbung_json(value: str | None, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        parsed = default
    return json.dumps(normalize_lumbung_value(parsed), ensure_ascii=False)


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
    file_sha256 TEXT,
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

CREATE TABLE IF NOT EXISTS evidence_link_cache (
    url TEXT PRIMARY KEY,
    source_label TEXT NOT NULL DEFAULT '',
    source_context TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    content_type TEXT,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    stage_hits_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_fetched_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evidence_link_cache_status
ON evidence_link_cache(status, updated_at);
"""


def normalize_duplicate_file_name(value: str | None) -> str:
    name = Path(str(value or "").strip()).name.lower()
    return " ".join(name.split())


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare_private_database_file()
        self.init()

    def canonical_persistence_capabilities(self) -> dict[str, object]:
        """Capabilities consumed by queue adapters; never inferred from config."""

        return {
            "backend_name": "sqlite",
            "shared_across_replicas": False,
            "atomic_distributed_claims": False,
            "shared_payload_storage": False,
        }

    def _prepare_private_database_file(self) -> None:
        if self.path.is_symlink():
            raise RuntimeError("Database SQLite tidak boleh berupa symlink.")
        if not self.path.exists():
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        if self.path.is_symlink():
            raise RuntimeError("Database SQLite tidak boleh berupa symlink.")
        metadata = self.path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Path database SQLite bukan regular file.")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise RuntimeError("Database SQLite harus dimiliki runtime user.")
        self.path.chmod(0o600)

    def _harden_sqlite_sidecars(self) -> None:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            try:
                metadata = sidecar.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("SQLite sidecar harus regular file dan bukan symlink.")
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise RuntimeError("SQLite sidecar harus dimiliki runtime user.")
            try:
                sidecar.chmod(0o600)
            except FileNotFoundError:
                # SQLite may remove the last WAL/SHM sidecar between lstat and
                # chmod when another connection closes.
                continue

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self._harden_sqlite_sidecars()
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
            self._harden_sqlite_sidecars()

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            run_migrations(conn)
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
                "file_sha256": "TEXT",
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
            review_rows = conn.execute(
                """
                SELECT id, candidates_json, confirmed_candidate_json
                FROM smart_upload_reviews
                """
            ).fetchall()
            for row in review_rows:
                candidates_json = normalize_lumbung_json(row["candidates_json"], [])
                confirmed_candidate_json = (
                    normalize_lumbung_json(row["confirmed_candidate_json"], {})
                    if row["confirmed_candidate_json"]
                    else None
                )
                conn.execute(
                    """
                    UPDATE smart_upload_reviews
                    SET candidates_json = ?, confirmed_candidate_json = ?
                    WHERE id = ?
                    """,
                    (candidates_json, confirmed_candidate_json, row["id"]),
                )
            action_rows = conn.execute(
                "SELECT id, candidate_json FROM smart_upload_actions"
            ).fetchall()
            for row in action_rows:
                if not row["candidate_json"]:
                    continue
                conn.execute(
                    """
                    UPDATE smart_upload_actions
                    SET candidate_json = ?
                    WHERE id = ?
                    """,
                    (normalize_lumbung_json(row["candidate_json"], {}), row["id"]),
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

    def indexed_file_duplicate_matches(
        self,
        file_name: str,
        size_bytes: int | None,
        limit: int = 12,
    ) -> list[dict]:
        normalized_name = normalize_duplicate_file_name(file_name)
        if not normalized_name:
            return []

        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    f.kk_id, f.kode, f.name, f.href, f.size_bytes, f.mime_type, f.modified_at,
                    folders.kk_title, folders.subunsur_name, folders.folder_path
                FROM files f
                JOIN folders ON folders.kk_id = f.kk_id AND folders.kode = f.kode
                WHERE f.is_folder = 0
                ORDER BY f.modified_at DESC, f.id DESC
                """,
            ).fetchall()

        matches = []
        for row in rows:
            item = dict(row)
            candidate_name = normalize_duplicate_file_name(item.get("name") or "")
            if candidate_name != normalized_name:
                continue
            same_size = size_bytes is not None and item.get("size_bytes") == size_bytes
            remote_name = str(item.get("name") or "").strip("/")
            remote_path = "/".join([str(item.get("folder_path") or "").strip("/"), remote_name]).strip("/")
            matches.append(
                {
                    "source": "lumbung_index",
                    "match_type": "same_name_size" if same_size else "same_name",
                    "kk_id": item.get("kk_id"),
                    "kode": item.get("kode"),
                    "kk_title": item.get("kk_title"),
                    "subunsur_name": item.get("subunsur_name"),
                    "name": item.get("name"),
                    "remote_path": remote_path,
                    "href": item.get("href"),
                    "size_bytes": item.get("size_bytes"),
                    "mime_type": item.get("mime_type"),
                    "modified_at": item.get("modified_at"),
                }
            )
            if len(matches) >= limit:
                break
        return matches

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
        file_sha256: str | None,
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
                    file_name, content_type, size_bytes, file_sha256, preview_text,
                    candidates_json, ai_status, ai_message, file_bytes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_name,
                    content_type,
                    size_bytes,
                    file_sha256,
                    preview_text,
                    json.dumps(normalize_lumbung_value(candidates), ensure_ascii=False),
                    ai_status,
                    ai_message,
                    payload,
                ),
            )
            return int(cursor.lastrowid)

    def smart_upload_hash_matches(
        self,
        file_sha256: str | None,
        current_review_id: int | None = None,
        limit: int = 8,
    ) -> list[dict]:
        if not file_sha256:
            return []
        params: list[object] = [file_sha256]
        review_filter = ""
        if current_review_id is not None:
            review_filter = "AND id <> ?"
            params.append(current_review_id)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    id, file_name, content_type, size_bytes, upload_status, upload_message,
                    confirmed_candidate_json, created_at, confirmed_at
                FROM smart_upload_reviews
                WHERE file_sha256 = ?
                  {review_filter}
                  AND upload_status IN ('uploaded', 'uploaded_primary')
                ORDER BY confirmed_at DESC, created_at DESC, id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        matches = []
        for row in rows:
            item = dict(row)
            candidate = None
            if item.get("confirmed_candidate_json"):
                try:
                    candidate = json.loads(item["confirmed_candidate_json"])
                except json.JSONDecodeError:
                    candidate = None
            item["source"] = "smart_upload_history"
            item["match_type"] = "same_hash"
            item["confirmed_candidate"] = candidate
            matches.append(item)
        return matches


    def smart_upload_review(self, review_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM smart_upload_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            data["candidates_json"] = normalize_lumbung_json(data.get("candidates_json"), [])
            if data.get("confirmed_candidate_json"):
                data["confirmed_candidate_json"] = normalize_lumbung_json(data.get("confirmed_candidate_json"), {})
            return data


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
                    json.dumps(normalize_lumbung_value(candidate), ensure_ascii=False) if candidate else None,
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
                (
                    upload_status,
                    upload_message,
                    json.dumps(normalize_lumbung_value(candidate), ensure_ascii=False),
                    review_id,
                ),
            )

    def upsert_evidence_link_cache(self, url: str, label: str = "", context: str = "") -> None:
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_link_cache (url, source_label, source_context)
                VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    source_label = CASE
                        WHEN excluded.source_label <> '' THEN excluded.source_label
                        ELSE evidence_link_cache.source_label
                    END,
                    source_context = CASE
                        WHEN excluded.source_context <> '' THEN excluded.source_context
                        ELSE evidence_link_cache.source_context
                    END,
                    status = CASE
                        WHEN evidence_link_cache.status IN ('ok', 'unsupported', 'fetching') THEN evidence_link_cache.status
                        ELSE 'pending'
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_url, label or "", context or ""),
            )

    def evidence_link_cache_many(self, urls: list[str]) -> dict[str, dict]:
        unique_urls = [url for url in dict.fromkeys(str(item or "").strip() for item in urls) if url]
        if not unique_urls:
            return {}
        placeholders = ",".join("?" for _ in unique_urls)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM evidence_link_cache WHERE url IN ({placeholders})",
                tuple(unique_urls),
            ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            try:
                item["stage_hits"] = json.loads(item.get("stage_hits_json") or "{}")
            except json.JSONDecodeError:
                item["stage_hits"] = {}
            result[item["url"]] = item
        return result

    def evidence_link_cache_counts(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM evidence_link_cache
                GROUP BY status
                """
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        counts["total"] = sum(counts.values())
        return counts

    def claim_evidence_link_jobs(self, limit: int = 5, max_attempts: int = 3) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM evidence_link_cache
                WHERE status IN ('pending', 'error')
                  AND attempt_count < ?
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (max_attempts, limit),
            ).fetchall()
            jobs = [dict(row) for row in rows]
            for job in jobs:
                conn.execute(
                    """
                    UPDATE evidence_link_cache
                    SET status = 'fetching',
                        attempt_count = attempt_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE url = ?
                    """,
                    (job["url"],),
                )
        return jobs

    def mark_evidence_link_cache(
        self,
        url: str,
        status: str,
        content_type: str | None = None,
        title: str = "",
        text: str = "",
        summary: str = "",
        stage_hits: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE evidence_link_cache
                SET status = ?,
                    content_type = ?,
                    title = ?,
                    text = ?,
                    summary = ?,
                    stage_hits_json = ?,
                    error_message = ?,
                    last_fetched_at = CASE WHEN ? = 'ok' THEN CURRENT_TIMESTAMP ELSE last_fetched_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE url = ?
                """,
                (
                    status,
                    content_type,
                    title or "",
                    text or "",
                    summary or "",
                    json.dumps(stage_hits or {}, ensure_ascii=False),
                    error_message,
                    status,
                    str(url or "").strip(),
                ),
            )
