from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock, Thread
from typing import Any

from app.config import Settings
from app.database import Database
from app.scanner import EvidenceScanner
from app.webdav_client import WebDavError


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state: dict[str, Any] = self._idle_state()

    def _idle_state(self) -> dict[str, Any]:
        return {
            "is_running": False,
            "scope": None,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "synced": 0,
            "failed": 0,
            "current": None,
            "errors": [],
            "message": "Belum ada sinkronisasi berjalan.",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start_full(self, db: Database, settings: Settings) -> dict[str, Any]:
        folders = db.folders()
        return self._start(
            target=lambda: self._run_full(db, settings, folders),
            scope="full",
            total=len(folders),
            message="Sinkronisasi penuh berjalan di background.",
        )

    def start_folder(self, db: Database, settings: Settings, kk_id: str, kode: str) -> dict[str, Any]:
        if not db.folder(kk_id, kode):
            raise WebDavError(f"Folder {kk_id}/{kode} tidak ditemukan di mapping.")
        return self._start(
            target=lambda: self._run_folder(db, settings, kk_id, kode),
            scope=f"{kk_id}/{kode}",
            total=1,
            message=f"Sinkronisasi {kk_id}/{kode} berjalan di background.",
        )

    def _start(self, target, scope: str, total: int, message: str) -> dict[str, Any]:
        with self._lock:
            if self._state["is_running"]:
                return {**dict(self._state), "started": False, "already_running": True}

            self._state = {
                "is_running": True,
                "scope": scope,
                "started_at": now_iso(),
                "finished_at": None,
                "total": total,
                "synced": 0,
                "failed": 0,
                "current": None,
                "errors": [],
                "message": message,
            }

        thread = Thread(target=target, daemon=True)
        thread.start()
        return {**self.status(), "started": True, "already_running": False}

    def _run_full(self, db: Database, settings: Settings, folders: list[dict]) -> None:
        scanner = EvidenceScanner(db, settings)
        try:
            for folder in folders:
                self._set_current(f"{folder['kk_id']}/{folder['kode']}")
                try:
                    scanner.sync_folder(folder["kk_id"], folder["kode"])
                    self._increment("synced")
                except WebDavError as exc:
                    self._record_error(folder["kk_id"], folder["kode"], str(exc))
                except Exception as exc:
                    self._record_error(folder["kk_id"], folder["kode"], f"Error tidak terduga: {exc}")
        finally:
            self._finish("Sinkronisasi penuh selesai.")

    def _run_folder(self, db: Database, settings: Settings, kk_id: str, kode: str) -> None:
        scanner = EvidenceScanner(db, settings)
        self._set_current(f"{kk_id}/{kode}")
        try:
            scanner.sync_folder(kk_id, kode)
            self._increment("synced")
        except WebDavError as exc:
            self._record_error(kk_id, kode, str(exc))
        except Exception as exc:
            self._record_error(kk_id, kode, f"Error tidak terduga: {exc}")
        finally:
            self._finish(f"Sinkronisasi {kk_id}/{kode} selesai.")

    def _set_current(self, current: str) -> None:
        with self._lock:
            self._state["current"] = current

    def _increment(self, key: str) -> None:
        with self._lock:
            self._state[key] += 1

    def _record_error(self, kk_id: str, kode: str, message: str) -> None:
        with self._lock:
            self._state["failed"] += 1
            self._state["errors"].append({"kk_id": kk_id, "kode": kode, "message": message})

    def _finish(self, message: str) -> None:
        with self._lock:
            failed = self._state["failed"]
            self._state["is_running"] = False
            self._state["finished_at"] = now_iso()
            self._state["current"] = None
            self._state["message"] = f"{message} {failed} gagal." if failed else message
