from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any


STORAGE_ENCRYPTION_POLICY_VERSION = "storage-encryption-attestation-v1"
MAX_EVIDENCE_BYTES = 16 * 1024
MAX_KEY_BYTES = 4 * 1024
MIN_KEY_BYTES = 32
MAX_VALIDITY_DAYS = 365
ALLOWED_CONTROLS = {
    "luks2",
    "filevault",
    "cloud_kms",
    "encrypted_block_volume",
    "managed_database_encryption",
    "other_managed",
}
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+:-]{1,119}$")
TICKET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,119}$")
NONCE_PATTERN = re.compile(r"^[a-f0-9]{32,128}$")
ATTESTATION_FIELDS = {
    "policy_version",
    "scope",
    "control_type",
    "reviewer_id",
    "change_ticket",
    "issued_at",
    "expires_at",
    "nonce",
    "database_path_sha256",
    "database_device_id",
    "payload_backend",
    "payload_path_sha256",
    "payload_device_id",
    "signature_hmac_sha256",
}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _existing_anchor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise FileNotFoundError(path)
    return candidate


def _path_binding(path_value: str) -> dict[str, str]:
    resolved = Path(path_value).expanduser().resolve(strict=False)
    anchor = _existing_anchor(resolved)
    return {
        "path_sha256": _sha256_text(str(resolved)),
        "device_id": str(anchor.stat().st_dev),
    }


def storage_binding(settings: Any) -> dict[str, Any]:
    backend = str(settings.analysis_payload_storage_backend or "database").strip().lower()
    if backend not in {"database", "filesystem"}:
        raise ValueError("Backend payload tidak didukung policy attestation.")
    database = _path_binding(str(settings.database_path))
    payload = (
        _path_binding(str(settings.analysis_payload_storage_path))
        if backend == "filesystem"
        else database
    )
    return {
        "database_path_sha256": database["path_sha256"],
        "database_device_id": database["device_id"],
        "payload_backend": backend,
        "payload_path_sha256": payload["path_sha256"],
        "payload_device_id": payload["device_id"],
    }


def create_storage_encryption_attestation(
    settings: Any,
    *,
    key: bytes,
    control_type: str,
    reviewer_id: str,
    change_ticket: str,
    issued_at: datetime | None = None,
    expires_in_days: int = 90,
    nonce: str | None = None,
) -> dict[str, Any]:
    if not MIN_KEY_BYTES <= len(key) <= MAX_KEY_BYTES:
        raise ValueError(
            f"Kunci attestation harus {MIN_KEY_BYTES}-{MAX_KEY_BYTES} byte."
        )
    control = str(control_type or "").strip().lower()
    reviewer = str(reviewer_id or "").strip()
    ticket = str(change_ticket or "").strip()
    if control not in ALLOWED_CONTROLS:
        raise ValueError("Jenis kontrol enkripsi tidak didukung policy.")
    if not IDENTITY_PATTERN.fullmatch(reviewer):
        raise ValueError("Identitas reviewer attestation tidak valid.")
    if not TICKET_PATTERN.fullmatch(ticket):
        raise ValueError("Change ticket attestation tidak valid.")
    _, database_file = _private_regular_file(str(settings.database_path or ""))
    if not all(database_file.values()):
        raise ValueError(
            "Database attestation harus regular file private milik runtime user."
        )
    backend = str(
        settings.analysis_payload_storage_backend or "database"
    ).strip().lower()
    if backend == "filesystem":
        _, payload_directory = _private_directory(
            str(settings.analysis_payload_storage_path or "")
        )
        if not all(payload_directory.values()):
            raise ValueError(
                "Direktori payload attestation harus private milik runtime user."
            )
    validity = int(expires_in_days)
    if not 1 <= validity <= MAX_VALIDITY_DAYS:
        raise ValueError(f"Masa berlaku attestation harus 1-{MAX_VALIDITY_DAYS} hari.")
    issued = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = issued + timedelta(days=validity)
    payload = {
        "policy_version": STORAGE_ENCRYPTION_POLICY_VERSION,
        "scope": "database_and_payload_at_rest",
        "control_type": control,
        "reviewer_id": reviewer,
        "change_ticket": ticket,
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "nonce": nonce or secrets.token_hex(16),
        **storage_binding(settings),
    }
    if not NONCE_PATTERN.fullmatch(payload["nonce"]):
        raise ValueError("Nonce attestation tidak valid.")
    return {
        **payload,
        "signature_hmac_sha256": hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest(),
    }


def _private_regular_file(path_value: str) -> tuple[Path | None, dict[str, bool]]:
    configured = bool(str(path_value or "").strip())
    source = Path(path_value).expanduser() if configured else None
    is_symlink = bool(source and source.is_symlink())
    resolved = source.resolve(strict=False) if source else None
    exists = bool(resolved and resolved.is_file())
    private = False
    owned = False
    if exists and not is_symlink and resolved:
        metadata = resolved.stat()
        private = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        owned = not hasattr(os, "getuid") or metadata.st_uid == os.getuid()
    return resolved, {
        "configured": configured,
        "exists": exists,
        "not_symlink": not is_symlink,
        "private_permissions": private,
        "owned_by_runtime_user": owned,
    }


def _private_directory(path_value: str) -> tuple[Path | None, dict[str, bool]]:
    configured = bool(str(path_value or "").strip())
    source = Path(path_value).expanduser() if configured else None
    is_symlink = bool(source and source.is_symlink())
    resolved = source.resolve(strict=False) if source else None
    exists = bool(resolved and resolved.is_dir())
    private = False
    owned = False
    if exists and not is_symlink and resolved:
        metadata = resolved.stat()
        private = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        owned = not hasattr(os, "getuid") or metadata.st_uid == os.getuid()
    return resolved, {
        "configured": configured,
        "exists": exists,
        "not_symlink": not is_symlink,
        "private_permissions": private,
        "owned_by_runtime_user": owned,
    }


def storage_encryption_attestation_status(
    settings: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _, database_file = _private_regular_file(str(settings.database_path or ""))
    payload_backend = str(
        settings.analysis_payload_storage_backend or "database"
    ).strip().lower()
    evidence_path, evidence_file = _private_regular_file(
        str(settings.analysis_storage_encryption_evidence_path or "")
    )
    key_path, key_file = _private_regular_file(
        str(settings.analysis_storage_encryption_key_path or "")
    )
    checks = {
        "validation_flag_enabled": bool(
            settings.analysis_payload_storage_encryption_validated
        ),
        "payload_backend_supported": payload_backend in {"database", "filesystem"},
        **{f"database_file_{key}": value for key, value in database_file.items()},
        **{f"evidence_file_{key}": value for key, value in evidence_file.items()},
        **{f"key_file_{key}": value for key, value in key_file.items()},
    }
    if payload_backend == "filesystem":
        _, payload_directory = _private_directory(
            str(settings.analysis_payload_storage_path or "")
        )
        checks.update({
            f"payload_directory_{key}": value
            for key, value in payload_directory.items()
        })
    evidence: dict[str, Any] = {}
    key = b""
    evidence_size_safe = False
    key_size_safe = False
    if evidence_path and checks["evidence_file_exists"]:
        try:
            evidence_size_safe = evidence_path.stat().st_size <= MAX_EVIDENCE_BYTES
            if evidence_size_safe:
                raw_evidence = evidence_path.read_bytes()
                evidence_size_safe = len(raw_evidence) <= MAX_EVIDENCE_BYTES
            if evidence_size_safe:
                parsed = json.loads(raw_evidence.decode("utf-8"))
                evidence = parsed if isinstance(parsed, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            evidence = {}
    if key_path and checks["key_file_exists"]:
        try:
            key_size_safe = MIN_KEY_BYTES <= key_path.stat().st_size <= MAX_KEY_BYTES
            if key_size_safe:
                key = key_path.read_bytes()
                key_size_safe = MIN_KEY_BYTES <= len(key) <= MAX_KEY_BYTES
        except OSError:
            key = b""
    checks["evidence_file_size_safe"] = evidence_size_safe
    checks["evidence_json_valid"] = bool(evidence)
    checks["evidence_schema_valid"] = set(evidence) == ATTESTATION_FIELDS
    checks["key_file_size_safe"] = key_size_safe

    signature = str(evidence.get("signature_hmac_sha256") or "")
    unsigned = {
        key: value for key, value in evidence.items()
        if key != "signature_hmac_sha256"
    }
    expected_signature = (
        hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest()
        if key_size_safe and evidence else ""
    )
    checks["signature_valid"] = bool(
        re.fullmatch(r"[a-f0-9]{64}", signature)
        and hmac.compare_digest(signature, expected_signature)
    )
    checks["policy_version_valid"] = (
        evidence.get("policy_version") == STORAGE_ENCRYPTION_POLICY_VERSION
    )
    checks["scope_valid"] = evidence.get("scope") == "database_and_payload_at_rest"
    checks["control_type_valid"] = evidence.get("control_type") in ALLOWED_CONTROLS
    checks["reviewer_valid"] = bool(
        IDENTITY_PATTERN.fullmatch(str(evidence.get("reviewer_id") or ""))
    )
    checks["change_ticket_valid"] = bool(
        TICKET_PATTERN.fullmatch(str(evidence.get("change_ticket") or ""))
    )
    checks["nonce_valid"] = bool(
        NONCE_PATTERN.fullmatch(str(evidence.get("nonce") or ""))
    )

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued = _parse_time(evidence.get("issued_at"))
    expires = _parse_time(evidence.get("expires_at"))
    checks["issued_at_valid"] = bool(
        issued and issued <= current + timedelta(minutes=5)
    )
    checks["expires_at_valid"] = bool(
        issued
        and expires
        and issued < expires
        and expires > current
        and expires - issued <= timedelta(days=MAX_VALIDITY_DAYS)
    )
    try:
        expected_binding = storage_binding(settings)
    except (OSError, FileNotFoundError, ValueError):
        expected_binding = {}
    binding_keys = (
        "database_path_sha256",
        "database_device_id",
        "payload_backend",
        "payload_path_sha256",
        "payload_device_id",
    )
    checks["storage_binding_valid"] = bool(
        expected_binding
        and all(evidence.get(key) == expected_binding.get(key) for key in binding_keys)
    )
    effective = all(checks.values())
    ticket = str(evidence.get("change_ticket") or "")
    binding_fingerprint = (
        hashlib.sha256(_canonical(expected_binding)).hexdigest()
        if expected_binding else None
    )
    seconds_until_expiry = max(
        0,
        int((expires - current).total_seconds()),
    ) if expires else 0
    return {
        "policy_version": STORAGE_ENCRYPTION_POLICY_VERSION,
        "effective": effective,
        "checks": checks,
        "control_type": evidence.get("control_type"),
        "issued_at": evidence.get("issued_at"),
        "expires_at": evidence.get("expires_at"),
        "seconds_until_expiry": seconds_until_expiry,
        "change_ticket_sha256": _sha256_text(ticket) if ticket else None,
        "binding_fingerprint": binding_fingerprint,
        "evidence_content_exposed": False,
        "path_exposed": False,
        "signature_exposed": False,
        "reasons": [key for key, value in checks.items() if not value],
    }
