from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat

from app.analysis.storage_evidence import create_storage_encryption_attestation
from app.config import Settings


def read_private_key(path: Path) -> bytes:
    source = path.expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("Key file harus regular file dan bukan symlink.")
    metadata = source.stat()
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("Key file harus private (tanpa permission group/other).")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ValueError("Key file harus dimiliki runtime user.")
    key = source.read_bytes()
    if not 32 <= len(key) <= 4096:
        raise ValueError("Key file harus berukuran 32-4096 byte.")
    return key


def write_private_json(path: Path, payload: dict) -> None:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Issue a signed, path-bound storage encryption attestation."
    )
    parser.add_argument("--database-path", required=True)
    parser.add_argument(
        "--payload-backend", choices=("database", "filesystem"), default="filesystem"
    )
    parser.add_argument("--payload-root", required=True)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--control", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--change-ticket", required=True)
    parser.add_argument("--expires-days", type=int, default=90)
    args = parser.parse_args()

    key = read_private_key(args.key_file)
    settings = Settings(
        _env_file=None,
        database_path=args.database_path,
        analysis_payload_storage_backend=args.payload_backend,
        analysis_payload_storage_path=args.payload_root,
        analysis_payload_storage_encryption_validated=True,
    )
    attestation = create_storage_encryption_attestation(
        settings,
        key=key,
        control_type=args.control,
        reviewer_id=args.reviewer,
        change_ticket=args.change_ticket,
        expires_in_days=args.expires_days,
    )
    write_private_json(args.output, attestation)
    print(json.dumps({
        "created": True,
        "policy_version": attestation["policy_version"],
        "control_type": attestation["control_type"],
        "expires_at": attestation["expires_at"],
        "path_exposed": False,
        "signature_exposed": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
