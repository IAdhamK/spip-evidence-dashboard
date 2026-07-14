from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from urllib.parse import quote, urlsplit

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.analysis.storage_evidence import storage_encryption_attestation_status
from app.analysis.reviewer_identity import authorization_contract_summary
from app.config import Settings
from app.migrations import MIGRATIONS


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_VERSION = "document-intelligence-production-profile-v8"
EXPECTED_SCHEMA_VERSION = max(version for version, _name, _sql in MIGRATIONS)
PAYLOAD_KEY_PATTERN = re.compile(r"^[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]{64}\.blob$")


def database_runtime_status(database_path: Path | None) -> tuple[dict, dict]:
    checks = {
        "database_path_configured": database_path is not None,
        "database_exists": False,
        "database_not_symlink": False,
        "database_private_permissions": False,
        "database_owned_by_runtime_user": False,
        "database_integrity_ok": False,
        "database_schema_current": False,
        "controlled_upload_idempotency_schema": False,
        "controlled_upload_reconciliation_schema": False,
        "fact_evidence_role_schema": False,
        "expert_template_expectation_schema": False,
        "document_family_gate_schema": False,
        "controlled_upload_stale_reservations_clear": False,
        "controlled_upload_unresolved_ambiguities_clear": False,
    }
    summary = {
        "schema_version": None,
        "expected_schema_version": EXPECTED_SCHEMA_VERSION,
        "stale_controlled_upload_reservation_count": None,
        "ambiguous_controlled_upload_count": None,
        "resolved_controlled_upload_ambiguity_count": None,
        "unresolved_controlled_upload_ambiguity_count": None,
        "path_in_report": False,
    }
    if database_path is None:
        return checks, summary
    expanded = database_path.expanduser()
    checks["database_not_symlink"] = not expanded.is_symlink()
    checks["database_exists"] = expanded.is_file()
    if not checks["database_exists"] or not checks["database_not_symlink"]:
        return checks, summary
    metadata = expanded.stat()
    checks["database_private_permissions"] = stat.S_IMODE(metadata.st_mode) & 0o077 == 0
    checks["database_owned_by_runtime_user"] = (
        not hasattr(os, "getuid") or metadata.st_uid == os.getuid()
    )
    uri = f"file:{quote(str(expanded.resolve()))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            checks["database_integrity_ok"] = bool(integrity and integrity[0] == "ok")
            migration = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            schema_version = int(migration[0] or 0) if migration else 0
            summary["schema_version"] = schema_version
            checks["database_schema_current"] = schema_version == EXPECTED_SCHEMA_VERSION
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(controlled_upload_actions)"
                ).fetchall()
            }
            indexes = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA index_list(controlled_upload_actions)"
                ).fetchall()
            }
            checks["controlled_upload_idempotency_schema"] = bool(
                "idempotency_key" in columns
                and "idx_controlled_upload_actions_idempotency" in indexes
                and "idx_controlled_upload_actions_status_created" in indexes
            )
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            reconciliation_schema = bool(
                "controlled_upload_reconciliation_events" in tables
                and "trg_controlled_upload_reconciliation_no_update" in triggers
                and "trg_controlled_upload_reconciliation_no_delete" in triggers
            )
            checks["controlled_upload_reconciliation_schema"] = reconciliation_schema
            fact_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(extracted_facts)"
                ).fetchall()
            }
            fact_indexes = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA index_list(extracted_facts)"
                ).fetchall()
            }
            checks["fact_evidence_role_schema"] = bool(
                {"evidence_role", "evidence_role_method"} <= fact_columns
                and "idx_extracted_facts_run_evidence_role" in fact_indexes
            )
            expert_label_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(expert_review_labels)"
                ).fetchall()
            }
            expert_label_indexes = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA index_list(expert_review_labels)"
                ).fetchall()
            }
            checks["expert_template_expectation_schema"] = bool(
                "expected_template_status" in expert_label_columns
                and "idx_expert_review_labels_template_status" in expert_label_indexes
            )
            mapping_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(mapping_candidates)"
                ).fetchall()
            }
            mapping_indexes = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA index_list(mapping_candidates)"
                ).fetchall()
            }
            assessment_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(grade_assessments)"
                ).fetchall()
            }
            checks["document_family_gate_schema"] = bool(
                {
                    "raw_retrieval_score",
                    "calibrated_decision_confidence",
                    "confidence_components_json",
                    "decision_status",
                    "document_family",
                    "document_role",
                    "family_parameter_compatible",
                    "grade_eligible",
                    "grade_status",
                    "grade_block_reasons_json",
                }
                <= mapping_columns
                and {
                    "grade_eligible",
                    "grade_status",
                    "grade_block_reasons_json",
                }
                <= assessment_columns
                and "idx_mapping_candidates_family_decision" in mapping_indexes
            )
            stale = conn.execute(
                """
                SELECT COUNT(*)
                FROM controlled_upload_actions
                WHERE status = 'uploading'
                  AND created_at < datetime('now', '-10 minutes')
                """
            ).fetchone()
            ambiguous_rows = conn.execute(
                """
                SELECT id
                FROM controlled_upload_actions
                WHERE status = 'blocked_ambiguous'
                ORDER BY id
                """
            ).fetchall()
            reconciliation_rows = conn.execute(
                """
                SELECT id, action_id, reviewer_id, outcome
                FROM controlled_upload_reconciliation_events
                ORDER BY id
                """
            ).fetchall() if reconciliation_schema else []
            stale_count = int(stale[0] or 0) if stale else 0
            latest_by_action_reviewer: dict[tuple[int, str], str] = {}
            for _event_id, action_id, reviewer_id, outcome in reconciliation_rows:
                latest_by_action_reviewer[
                    (int(action_id), str(reviewer_id).casefold())
                ] = str(outcome)
            resolved_count = 0
            for row in ambiguous_rows:
                action_id = int(row[0])
                outcomes = [
                    outcome
                    for (event_action_id, _reviewer_id), outcome
                    in latest_by_action_reviewer.items()
                    if event_action_id == action_id
                ]
                terminal = [
                    outcome for outcome in outcomes
                    if outcome in {"confirmed_uploaded", "confirmed_not_uploaded"}
                ]
                if (
                    len(outcomes) >= 2
                    and "needs_investigation" not in outcomes
                    and len(set(terminal)) == 1
                    and len(terminal) >= 2
                ):
                    resolved_count += 1
            ambiguity_count = len(ambiguous_rows)
            unresolved_count = ambiguity_count - resolved_count
            summary["stale_controlled_upload_reservation_count"] = stale_count
            summary["ambiguous_controlled_upload_count"] = ambiguity_count
            summary["resolved_controlled_upload_ambiguity_count"] = resolved_count
            summary["unresolved_controlled_upload_ambiguity_count"] = unresolved_count
            checks["controlled_upload_stale_reservations_clear"] = stale_count == 0
            checks["controlled_upload_unresolved_ambiguities_clear"] = (
                reconciliation_schema and unresolved_count == 0
            )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return checks, summary
    return checks, summary


def validate_production_profile(
    webhook_url_file: Path,
    payload_root: Path | None = None,
    *,
    database_path: Path | None = None,
    storage_evidence_file: Path | None = None,
    storage_evidence_key_file: Path | None = None,
    payload_backend: str = "filesystem",
    runtime_settings: Settings | None = None,
) -> dict:
    runtime_settings = runtime_settings or Settings()
    authorization_contract = authorization_contract_summary()
    secret_path = webhook_url_file.expanduser().resolve()
    secret_exists = secret_path.is_file()
    secret_is_symlink = webhook_url_file.expanduser().is_symlink()
    secret_private = False
    secret_owned = False
    url_valid = False
    url_sha256 = None
    if secret_exists and not secret_is_symlink:
        secret_metadata = secret_path.stat()
        mode = stat.S_IMODE(secret_metadata.st_mode)
        secret_private = mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
        secret_owned = not hasattr(os, "getuid") or secret_metadata.st_uid == os.getuid()
        if secret_metadata.st_size <= 4097:
            try:
                raw = secret_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                raw = ""
            value = raw.strip()
            parsed = urlsplit(value)
            url_valid = bool(
                value
                and len(raw.splitlines()) == 1
                and len(value) <= 4096
                and parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
            )
            if value:
                url_sha256 = hashlib.sha256(value.encode("utf-8")).hexdigest()

    alert_path = REPO_ROOT / "ops/alertmanager/alertmanager.webhook.yml"
    alert_config = yaml.safe_load(alert_path.read_text(encoding="utf-8"))
    receiver = alert_config["receivers"][0]
    webhook = receiver["webhook_configs"][0]
    alertmanager_safe = bool(
        alert_config["route"]["receiver"] == "organization-webhook"
        and receiver["name"] == "organization-webhook"
        and webhook.get("url_file") == "/run/secrets/alertmanager_webhook_url"
        and webhook.get("send_resolved") is True
        and "url" not in webhook
    )

    proxy_path = REPO_ROOT / "ops/reverse-proxy/nginx.conf"
    proxy = proxy_path.read_text(encoding="utf-8")
    identity_proxy_safe = bool(
        "auth_request /_oauth2_auth;" in proxy
        and "auth_request_set $reviewer_identity $upstream_http_x_auth_request_email;"
        in proxy
        and "proxy_set_header X-Reviewer-Identity $reviewer_identity;" in proxy
        and 'proxy_set_header X-Reviewer-Identity "";' in proxy
        and "$http_x_reviewer_identity" not in proxy.lower()
    )
    role_proxy_safe = bool(
        "auth_request_set $reviewer_roles $upstream_http_x_auth_request_groups;"
        in proxy
        and "proxy_set_header X-Reviewer-Roles $reviewer_roles;" in proxy
        and 'proxy_set_header X-Reviewer-Roles "";' in proxy
        and "$http_x_reviewer_roles" not in proxy.lower()
    )

    checks = {
        "webhook_secret_exists": secret_exists,
        "webhook_secret_not_symlink": not secret_is_symlink,
        "webhook_secret_private_permissions": secret_private,
        "webhook_secret_owned_by_runtime_user": secret_owned,
        "webhook_url_https_and_safe": url_valid,
        "alertmanager_secret_file_profile": alertmanager_safe,
        "trusted_identity_proxy_boundary": identity_proxy_safe,
        "trusted_role_proxy_boundary": role_proxy_safe,
        "reviewer_identity_required": bool(
            runtime_settings.analysis_require_reviewer_identity
        ),
        "reviewer_role_rbac_required": bool(
            runtime_settings.analysis_require_reviewer_role
        ),
        "reviewer_identity_header_matches_proxy": (
            runtime_settings.analysis_reviewer_identity_header.strip().lower()
            == "x-reviewer-identity"
        ),
        "reviewer_role_header_matches_proxy": (
            runtime_settings.analysis_reviewer_role_header.strip().lower()
            == "x-reviewer-roles"
        ),
        "authorization_contract_complete": bool(
            authorization_contract.get("policy_version") == "analysis-rbac-v1"
            and int(authorization_contract.get("classified_operation_count") or 0)
            == int(authorization_contract.get("secured_operation_count") or 0)
            + int(authorization_contract.get("proxy_boundary_operation_count") or 0)
            and int(authorization_contract.get("secured_operation_count") or 0) > 0
            and authorization_contract.get("all_mutations_role_secured") is True
        ),
    }
    database_checks, database_summary = database_runtime_status(database_path)
    checks.update(database_checks)
    payload_summary = None
    encryption_attestation = None
    if payload_root is not None:
        expanded_root = payload_root.expanduser()
        root_exists = expanded_root.is_dir()
        root_not_symlink = not expanded_root.is_symlink()
        root_private = False
        root_owned = False
        files_private = True
        files_regular = True
        keys_valid = True
        checksums_valid = True
        file_count = 0
        total_size_bytes = 0
        if root_exists and root_not_symlink:
            root_stat = expanded_root.stat()
            root_private = stat.S_IMODE(root_stat.st_mode) & 0o077 == 0
            root_owned = not hasattr(os, "getuid") or root_stat.st_uid == os.getuid()
            for path in expanded_root.rglob("*"):
                relative = path.relative_to(expanded_root).as_posix()
                if path.is_symlink():
                    files_regular = False
                    continue
                if path.is_dir():
                    if stat.S_IMODE(path.stat().st_mode) & 0o077:
                        files_private = False
                    continue
                if not path.is_file():
                    files_regular = False
                    continue
                file_count += 1
                metadata = path.stat()
                total_size_bytes += int(metadata.st_size)
                if stat.S_IMODE(metadata.st_mode) & 0o077:
                    files_private = False
                if not PAYLOAD_KEY_PATTERN.fullmatch(relative):
                    keys_valid = False
                    checksums_valid = False
                else:
                    digest = hashlib.sha256()
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest() != path.stem:
                        checksums_valid = False
        checks.update({
            "payload_root_exists": root_exists,
            "payload_root_not_symlink": root_not_symlink,
            "payload_root_private_permissions": root_private,
            "payload_root_owned_by_runtime_user": root_owned,
            "payload_files_private_permissions": files_private,
            "payload_entries_regular_no_symlink": files_regular,
            "payload_content_addressed_keys": keys_valid,
            "payload_checksums_match_keys": checksums_valid,
        })
        attestation_settings = Settings(
            _env_file=None,
            database_path=str(database_path or ""),
            analysis_payload_storage_backend=payload_backend,
            analysis_payload_storage_path=str(payload_root),
            analysis_payload_storage_encryption_validated=True,
            analysis_storage_encryption_evidence_path=str(
                storage_evidence_file or ""
            ),
            analysis_storage_encryption_key_path=str(
                storage_evidence_key_file or ""
            ),
        )
        encryption_attestation = storage_encryption_attestation_status(
            attestation_settings
        )
        checks["storage_encryption_attestation_effective"] = bool(
            database_path
            and storage_evidence_file
            and storage_evidence_key_file
            and encryption_attestation["effective"]
        )
        payload_summary = {
            "file_count": file_count,
            "total_size_bytes": total_size_bytes,
            "root_path_in_report": False,
        }
    else:
        checks["payload_root_configured"] = False
        checks["storage_encryption_attestation_effective"] = False
    report = {
        "validator_version": VALIDATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": all(checks.values()),
        "checks": checks,
        "webhook_url_sha256": url_sha256,
        "sensitive_url_in_report": False,
        "payload_storage": payload_summary,
        "database": database_summary,
        "storage_encryption_attestation": encryption_attestation,
        "authorization_contract": authorization_contract,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate trusted-identity and secret-backed alert production profiles."
    )
    parser.add_argument("--webhook-url-file", required=True, type=Path)
    parser.add_argument("--database-path", required=True, type=Path)
    parser.add_argument(
        "--payload-backend",
        choices=("database", "filesystem"),
        default="filesystem",
    )
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--storage-encryption-evidence-file", required=True, type=Path)
    parser.add_argument("--storage-encryption-key-file", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_production_profile(
        args.webhook_url_file,
        args.payload_root,
        database_path=args.database_path,
        storage_evidence_file=args.storage_encryption_evidence_file,
        storage_evidence_key_file=args.storage_encryption_key_file,
        payload_backend=args.payload_backend,
        runtime_settings=Settings(),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
