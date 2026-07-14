from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from typing import Iterable


PAYLOAD_KEY_PATTERN = re.compile(
    r"^(?P<a>[a-f0-9]{2})/(?P<b>[a-f0-9]{2})/(?P<sha>[a-f0-9]{64})\.blob$"
)


class PayloadStorageError(RuntimeError):
    pass


class PayloadIntegrityError(PayloadStorageError):
    pass


@dataclass(frozen=True)
class StoredPayload:
    backend: str
    key: str
    sha256: str
    size_bytes: int


class FilesystemPayloadStore:
    backend = "filesystem"

    def __init__(self, root: str | Path, *, fsync: bool = True):
        raw_root = Path(root).expanduser()
        if raw_root.exists() and raw_root.is_symlink():
            raise PayloadStorageError("Root payload storage tidak boleh berupa symlink.")
        raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = raw_root.resolve()
        if not self.root.is_dir():
            raise PayloadStorageError("Root payload storage bukan direktori.")
        self.root.chmod(0o700)
        self.fsync = bool(fsync)

    def put(self, payload: bytes, expected_sha256: str | None = None) -> StoredPayload:
        content = bytes(payload)
        sha256 = hashlib.sha256(content).hexdigest()
        if expected_sha256 and sha256 != expected_sha256:
            raise PayloadIntegrityError("Checksum payload tidak cocok sebelum penyimpanan.")
        key = self.key_for_sha256(sha256)
        destination = self._path_for_key(key)
        current = self.root
        for part in Path(key).parts[:-1]:
            current = current / part
            current.mkdir(exist_ok=True, mode=0o700)
            current.chmod(0o700)

        if destination.exists():
            existing = self.get(key, expected_sha256=sha256, expected_size_bytes=len(content))
            if existing != content:
                raise PayloadIntegrityError("Content-addressed payload mempunyai byte berbeda.")
            return StoredPayload(self.backend, key, sha256, len(content))

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{sha256}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(handle.name, 0o600)
                handle.write(content)
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
            destination.chmod(0o600)
            if self.fsync:
                directory_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()
        self.get(key, expected_sha256=sha256, expected_size_bytes=len(content))
        return StoredPayload(self.backend, key, sha256, len(content))

    def get(
        self,
        key: str,
        *,
        expected_sha256: str,
        expected_size_bytes: int,
    ) -> bytes:
        path = self._path_for_key(key)
        if path.is_symlink() or not path.is_file():
            raise PayloadIntegrityError("Payload eksternal hilang atau bukan regular file.")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise PayloadIntegrityError("Payload eksternal bukan regular file.")
        if metadata.st_mode & 0o077:
            raise PayloadIntegrityError("Permission payload eksternal tidak privat.")
        if int(metadata.st_size) != int(expected_size_bytes):
            raise PayloadIntegrityError("Ukuran payload eksternal tidak cocok.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            handle = os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            payload = handle.read(int(expected_size_bytes) + 1)
        if len(payload) != int(expected_size_bytes):
            raise PayloadIntegrityError("Byte payload eksternal tidak lengkap.")
        observed_sha256 = hashlib.sha256(payload).hexdigest()
        if observed_sha256 != expected_sha256:
            raise PayloadIntegrityError("Checksum payload eksternal tidak cocok.")
        if self.key_for_sha256(observed_sha256) != key:
            raise PayloadIntegrityError("Key payload eksternal tidak sesuai checksum.")
        return payload

    def delete(self, key: str) -> bool:
        path = self._path_for_key(key)
        if path.is_symlink():
            raise PayloadIntegrityError("Symlink payload tidak boleh dihapus sebagai artefak.")
        if not path.exists():
            return False
        if not path.is_file():
            raise PayloadIntegrityError("Path payload bukan regular file.")
        path.unlink()
        self._remove_empty_parent(path.parent)
        return True

    def keys(self) -> set[str]:
        keys: set[str] = set()
        for path in self.root.rglob("*.blob"):
            if path.is_symlink() or not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if PAYLOAD_KEY_PATTERN.fullmatch(key):
                keys.add(key)
        return keys

    def cleanup_orphans(self, referenced_keys: Iterable[str]) -> dict[str, int]:
        referenced = {str(key) for key in referenced_keys if key}
        stored = self.keys()
        deleted = 0
        for key in sorted(stored - referenced):
            deleted += int(self.delete(key))
        return {
            "stored_count": len(stored),
            "referenced_count": len(stored & referenced),
            "orphan_count": len(stored - referenced),
            "deleted_count": deleted,
        }

    @staticmethod
    def key_for_sha256(sha256: str) -> str:
        normalized = str(sha256 or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", normalized):
            raise PayloadStorageError("SHA-256 payload tidak valid.")
        return f"{normalized[:2]}/{normalized[2:4]}/{normalized}.blob"

    def _path_for_key(self, key: str) -> Path:
        match = PAYLOAD_KEY_PATTERN.fullmatch(str(key or ""))
        if not match or match.group("a") != match.group("sha")[:2] or match.group("b") != match.group("sha")[2:4]:
            raise PayloadStorageError("Key payload eksternal tidak valid.")
        current = self.root
        for part in Path(key).parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise PayloadStorageError("Direktori payload tidak boleh berupa symlink.")
            if current.exists() and not current.is_dir():
                raise PayloadStorageError("Komponen direktori payload bukan direktori.")
            if current.exists() and current.stat().st_mode & 0o077:
                raise PayloadIntegrityError("Permission direktori payload tidak privat.")
        candidate = self.root.joinpath(*Path(key).parts)
        resolved_parent = candidate.parent.resolve()
        if self.root != resolved_parent and self.root not in resolved_parent.parents:
            raise PayloadStorageError("Key payload keluar dari root storage.")
        return candidate

    def _remove_empty_parent(self, directory: Path) -> None:
        current = directory
        while current != self.root and self.root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
