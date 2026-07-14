from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import HTTPException
from starlette.requests import Request
import yaml

from app.analysis.reviewer_identity import (
    DOMAIN_RULE_ROLES,
    EVIDENCE_REVIEW_ROLES,
    authorize_reviewer,
    resolve_reviewer_identity,
)
from app.analysis.payload_storage import FilesystemPayloadStore
from app.analysis.storage_evidence import create_storage_encryption_attestation
from app.config import Settings
from app.database import Database
from scripts.validate_production_profile import validate_production_profile


def request_with_headers(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 10000),
        "scheme": "http",
    })


class ProductionSecurityProfileTests(unittest.TestCase):
    def test_database_file_is_private_and_symlink_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "private.db"
            database_path.write_bytes(b"")
            database_path.chmod(0o644)

            database = Database(str(database_path))
            self.assertEqual(database_path.stat().st_mode & 0o777, 0o600)
            with database.connect() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS permission_probe (id INTEGER)")
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(database_path) + suffix)
                if sidecar.exists():
                    self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)

            linked_path = root / "linked.db"
            linked_path.symlink_to(database_path)
            with self.assertRaises(RuntimeError):
                Database(str(linked_path))

    def test_trusted_reviewer_header_is_required_validated_and_payload_bound(self) -> None:
        settings = Settings(
            _env_file=None,
            analysis_require_reviewer_identity=True,
            analysis_reviewer_identity_header="X-Reviewer-Identity",
        )
        with self.assertRaises(HTTPException) as missing:
            resolve_reviewer_identity(request_with_headers([]), settings, "payload-user")
        self.assertEqual(missing.exception.status_code, 401)

        trusted = request_with_headers([
            (b"x-reviewer-identity", b"reviewer@example.go.id"),
        ])
        self.assertEqual(
            resolve_reviewer_identity(trusted, settings, "reviewer@example.go.id"),
            "reviewer@example.go.id",
        )
        with self.assertRaises(HTTPException) as mismatch:
            resolve_reviewer_identity(trusted, settings, "different-user")
        self.assertEqual(mismatch.exception.status_code, 403)

        unsafe = request_with_headers([
            (b"x-reviewer-identity", b"reviewer with spaces"),
        ])
        with self.assertRaises(HTTPException) as invalid:
            resolve_reviewer_identity(unsafe, settings, "")
        self.assertEqual(invalid.exception.status_code, 401)

    def test_reference_proxy_replaces_spoofed_identity_after_auth_request(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = (root / "ops/reverse-proxy/nginx.conf").read_text(encoding="utf-8")
        self.assertIn("auth_request /_oauth2_auth;", config)
        self.assertIn(
            "auth_request_set $reviewer_identity $upstream_http_x_auth_request_email;",
            config,
        )
        self.assertIn(
            "proxy_set_header X-Reviewer-Identity $reviewer_identity;",
            config,
        )
        self.assertIn('proxy_set_header X-Reviewer-Identity "";', config)
        self.assertNotIn("$http_x_reviewer_identity", config.lower())
        self.assertIn(
            "auth_request_set $reviewer_roles $upstream_http_x_auth_request_groups;",
            config,
        )
        self.assertIn("proxy_set_header X-Reviewer-Roles $reviewer_roles;", config)
        self.assertIn('proxy_set_header X-Reviewer-Roles "";', config)
        self.assertNotIn("$http_x_reviewer_roles", config.lower())

    def test_trusted_role_header_is_fail_closed_and_scope_bound(self) -> None:
        settings = Settings(
            _env_file=None,
            analysis_require_reviewer_identity=True,
            analysis_reviewer_identity_header="X-Reviewer-Identity",
            analysis_require_reviewer_role=True,
            analysis_reviewer_role_header="X-Reviewer-Roles",
        )
        evidence_reviewer = request_with_headers([
            (b"x-reviewer-identity", b"reviewer@example.go.id"),
            (b"x-reviewer-roles", b"evidence_reviewer"),
        ])
        self.assertEqual(
            authorize_reviewer(
                evidence_reviewer,
                settings,
                "reviewer@example.go.id",
                EVIDENCE_REVIEW_ROLES,
            ),
            "reviewer@example.go.id",
        )
        with self.assertRaises(HTTPException) as wrong_scope:
            authorize_reviewer(
                evidence_reviewer,
                settings,
                "reviewer@example.go.id",
                DOMAIN_RULE_ROLES,
            )
        self.assertEqual(wrong_scope.exception.status_code, 403)

        missing_role = request_with_headers([
            (b"x-reviewer-identity", b"reviewer@example.go.id"),
        ])
        with self.assertRaises(HTTPException) as missing:
            authorize_reviewer(
                missing_role,
                settings,
                "reviewer@example.go.id",
                EVIDENCE_REVIEW_ROLES,
            )
        self.assertEqual(missing.exception.status_code, 403)

        unsafe_role = request_with_headers([
            (b"x-reviewer-identity", b"reviewer@example.go.id"),
            (b"x-reviewer-roles", b"evidence_reviewer,super-admin"),
        ])
        with self.assertRaises(HTTPException) as invalid:
            authorize_reviewer(
                unsafe_role,
                settings,
                "reviewer@example.go.id",
                EVIDENCE_REVIEW_ROLES,
            )
        self.assertEqual(invalid.exception.status_code, 403)

        admin = request_with_headers([
            (b"x-reviewer-identity", b"admin@example.go.id"),
            (b"x-reviewer-roles", b"analysis_admin"),
        ])
        self.assertEqual(
            authorize_reviewer(admin, settings, "", DOMAIN_RULE_ROLES),
            "admin@example.go.id",
        )

        unsafe_config = Settings(
            _env_file=None,
            analysis_require_reviewer_identity=False,
            analysis_require_reviewer_role=True,
        )
        with self.assertRaises(HTTPException) as misconfigured:
            authorize_reviewer(
                evidence_reviewer,
                unsafe_config,
                "reviewer@example.go.id",
                EVIDENCE_REVIEW_ROLES,
            )
        self.assertEqual(misconfigured.exception.status_code, 503)

    def test_alertmanager_webhook_profile_is_opt_in_and_secret_file_backed(self) -> None:
        root = Path(__file__).resolve().parents[2]
        development = yaml.safe_load(
            (root / "ops/alertmanager/alertmanager.yml").read_text(encoding="utf-8")
        )
        production = yaml.safe_load(
            (root / "ops/alertmanager/alertmanager.webhook.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(development["receivers"], [{"name": "local-observability"}])
        receiver = production["receivers"][0]
        self.assertEqual(receiver["name"], "organization-webhook")
        webhook = receiver["webhook_configs"][0]
        self.assertEqual(
            webhook["url_file"],
            "/run/secrets/alertmanager_webhook_url",
        )
        self.assertTrue(webhook["send_resolved"])
        self.assertNotIn("url", webhook)

    def test_production_profile_validator_never_exposes_webhook_url(self) -> None:
        with TemporaryDirectory() as directory:
            secret = Path(directory) / "webhook-url"
            secret.write_text(
                "https://alerts.example.invalid/hooks/private-token\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            missing_storage = validate_production_profile(secret)
            self.assertFalse(missing_storage["passed"])
            self.assertFalse(missing_storage["checks"]["payload_root_configured"])
            payload_root = Path(directory) / "payloads"
            store = FilesystemPayloadStore(payload_root, fsync=False)
            store.put(b"production-profile-payload")
            database = Path(directory) / "evidence.db"
            runtime_database = Database(str(database))
            attestation_key = Path(directory) / "storage-attestation.key"
            key = b"production-profile-storage-attestation-key" * 2
            attestation_key.write_bytes(key)
            attestation_key.chmod(0o600)
            attestation_file = Path(directory) / "storage-attestation.json"
            settings = Settings(
                _env_file=None,
                database_path=str(database),
                analysis_payload_storage_backend="filesystem",
                analysis_payload_storage_path=str(payload_root),
                analysis_payload_storage_encryption_validated=True,
                analysis_require_reviewer_identity=True,
                analysis_require_reviewer_role=True,
            )
            attestation_file.write_text(
                json.dumps(
                    create_storage_encryption_attestation(
                        settings,
                        key=key,
                        control_type="encrypted_block_volume",
                        reviewer_id="platform-owner@example.go.id",
                        change_ticket="CHG-2026-0713",
                    )
                ),
                encoding="utf-8",
            )
            attestation_file.chmod(0o600)

            validator_args = {
                "database_path": database,
                "storage_evidence_file": attestation_file,
                "storage_evidence_key_file": attestation_key,
                "runtime_settings": settings,
            }
            flag_only = validate_production_profile(
                secret,
                payload_root,
                database_path=database,
            )
            self.assertFalse(flag_only["passed"])
            self.assertFalse(
                flag_only["checks"]["storage_encryption_attestation_effective"]
            )

            report = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertTrue(report["passed"], report)
            self.assertEqual(
                report["validator_version"],
                "document-intelligence-production-profile-v7",
            )
            self.assertTrue(report["checks"]["trusted_role_proxy_boundary"])
            self.assertTrue(report["checks"]["reviewer_identity_required"])
            self.assertTrue(report["checks"]["reviewer_role_rbac_required"])
            self.assertTrue(report["checks"]["authorization_contract_complete"])
            self.assertEqual(
                report["authorization_contract"]["policy_version"],
                "analysis-rbac-v1",
            )
            self.assertEqual(
                report["authorization_contract"]["classified_operation_count"],
                61,
            )
            self.assertFalse(report["sensitive_url_in_report"])
            self.assertNotIn("private-token", json.dumps(report))
            self.assertRegex(report["webhook_url_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(report["payload_storage"]["file_count"], 1)
            self.assertFalse(report["payload_storage"]["root_path_in_report"])
            self.assertTrue(report["storage_encryption_attestation"]["effective"])
            self.assertNotIn(str(attestation_file), json.dumps(report))
            self.assertEqual(report["database"]["schema_version"], 31)
            self.assertEqual(report["database"]["expected_schema_version"], 31)
            self.assertTrue(
                report["checks"]["controlled_upload_reconciliation_schema"]
            )
            self.assertTrue(report["checks"]["fact_evidence_role_schema"])
            self.assertTrue(
                report["checks"]["expert_template_expectation_schema"]
            )
            self.assertEqual(
                report["database"]["stale_controlled_upload_reservation_count"],
                0,
            )
            self.assertFalse(report["database"]["path_in_report"])

            with runtime_database.connect() as conn:
                document_id = conn.execute(
                    """
                    INSERT INTO documents(file_name, content_type, size_bytes, sha256)
                    VALUES ('validator-fixture.txt', 'text/plain', 1, ?)
                    """,
                    ("1" * 64,),
                ).lastrowid
                run_id = conn.execute(
                    """
                    INSERT INTO analysis_runs(
                        document_id, pipeline_version, parser_version, rule_version,
                        prompt_version, configuration_hash
                    ) VALUES (?, '2.0', '2.0', 'draft', '2.0', 'validator')
                    """,
                    (document_id,),
                ).lastrowid
                mapping_id = conn.execute(
                    """
                    INSERT INTO mapping_candidates(
                        run_id, kk_id, kode, detail_kode, retrieval_score,
                        mapping_score, status
                    ) VALUES (?, 'KK-1', '1.1', '1.1.1', 0.8, 0.7, 'candidate')
                    """,
                    (run_id,),
                ).lastrowid
                action_id = conn.execute(
                    """
                    INSERT INTO controlled_upload_actions(
                        run_id, mapping_candidate_id, reviewer_id, status,
                        destination_json, message, idempotency_key, created_at
                    ) VALUES (?, ?, 'operator', 'uploading', '{}', 'stale fixture', ?,
                              datetime('now', '-11 minutes'))
                    """,
                    (run_id, mapping_id, "2" * 64),
                ).lastrowid
            stale_database = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(stale_database["passed"])
            self.assertFalse(
                stale_database["checks"][
                    "controlled_upload_stale_reservations_clear"
                ]
            )
            self.assertEqual(
                stale_database["database"][
                    "stale_controlled_upload_reservation_count"
                ],
                1,
            )
            with runtime_database.connect() as conn:
                conn.execute(
                    "UPDATE controlled_upload_actions SET status = 'blocked_ambiguous' WHERE id = ?",
                    (action_id,),
                )
            terminal_ambiguity = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(terminal_ambiguity["passed"], terminal_ambiguity)
            self.assertFalse(
                terminal_ambiguity["checks"][
                    "controlled_upload_unresolved_ambiguities_clear"
                ]
            )
            self.assertEqual(
                terminal_ambiguity["database"]["ambiguous_controlled_upload_count"],
                1,
            )
            self.assertEqual(
                terminal_ambiguity["database"][
                    "unresolved_controlled_upload_ambiguity_count"
                ],
                1,
            )

            with runtime_database.connect() as conn:
                for reviewer in ("operator-one", "operator-two"):
                    conn.execute(
                        """
                        INSERT INTO controlled_upload_reconciliation_events(
                            action_id, reviewer_id, outcome, reason, attested
                        ) VALUES (?, ?, 'confirmed_not_uploaded', ?, 1)
                        """,
                        (
                            action_id,
                            reviewer,
                            "Folder tujuan dan legacy review diperiksa langsung.",
                        ),
                    )
            resolved_ambiguity = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertTrue(resolved_ambiguity["passed"], resolved_ambiguity)
            self.assertEqual(
                resolved_ambiguity["database"][
                    "resolved_controlled_upload_ambiguity_count"
                ],
                1,
            )
            self.assertEqual(
                resolved_ambiguity["database"][
                    "unresolved_controlled_upload_ambiguity_count"
                ],
                0,
            )

            with runtime_database.connect() as conn:
                conn.execute("DELETE FROM schema_migrations WHERE version = 31")
            stale_schema = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(stale_schema["passed"])
            self.assertFalse(stale_schema["checks"]["database_schema_current"])

            secret.write_text("http://insecure.example.invalid/hook\n", encoding="utf-8")
            rejected = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(rejected["passed"])
            self.assertFalse(rejected["checks"]["webhook_url_https_and_safe"])

            payload_file = next(payload_root.rglob("*.blob"))
            payload_file.chmod(0o644)
            insecure_storage = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(
                insecure_storage["checks"]["payload_files_private_permissions"]
            )
            payload_file.chmod(0o600)
            payload_file.write_bytes(b"x" * payload_file.stat().st_size)
            tampered_storage = validate_production_profile(
                secret, payload_root, **validator_args
            )
            self.assertFalse(
                tampered_storage["checks"]["payload_checksums_match_keys"]
            )


if __name__ == "__main__":
    unittest.main()
