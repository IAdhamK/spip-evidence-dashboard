from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from app.analysis.storage_evidence import (
    create_storage_encryption_attestation,
    storage_encryption_attestation_status,
)
from app.analysis.storage_attestation_cli import read_private_key, write_private_json
from app.config import Settings


class StorageEncryptionEvidenceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Settings, bytes, Path, Path]:
        database = root / "evidence.db"
        database.write_bytes(b"sqlite-placeholder")
        database.chmod(0o600)
        payload_root = root / "payloads"
        payload_root.mkdir(mode=0o700)
        key_file = root / "storage-attestation.key"
        key = b"production-storage-attestation-key-material-2026" * 2
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        evidence_file = root / "storage-attestation.json"
        settings = Settings(
            _env_file=None,
            database_path=str(database),
            analysis_payload_storage_backend="filesystem",
            analysis_payload_storage_path=str(payload_root),
            analysis_payload_storage_encryption_validated=True,
            analysis_storage_encryption_evidence_path=str(evidence_file),
            analysis_storage_encryption_key_path=str(key_file),
        )
        return settings, key, evidence_file, key_file

    @staticmethod
    def _write_evidence(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)

    def test_signed_current_private_and_storage_bound_evidence_is_effective(self) -> None:
        with TemporaryDirectory() as directory:
            settings, key, evidence_file, _ = self._fixture(Path(directory))
            evidence = create_storage_encryption_attestation(
                settings,
                key=key,
                control_type="encrypted_block_volume",
                reviewer_id="platform-owner@example.go.id",
                change_ticket="CHG-2026-0713",
                expires_in_days=90,
                nonce="ab" * 16,
            )
            self._write_evidence(evidence_file, evidence)

            status = storage_encryption_attestation_status(settings)

            self.assertTrue(status["effective"], status)
            self.assertEqual(status["reasons"], [])
            self.assertGreater(status["seconds_until_expiry"], 0)
            rendered = json.dumps(status)
            self.assertNotIn(str(evidence_file), rendered)
            self.assertNotIn("platform-owner@example.go.id", rendered)
            self.assertNotIn(evidence["signature_hmac_sha256"], rendered)
            self.assertTrue(status["evidence_content_exposed"] is False)

            database_backend = settings.model_copy(
                update={"analysis_payload_storage_backend": "database"}
            )
            database_evidence = create_storage_encryption_attestation(
                database_backend,
                key=key,
                control_type="managed_database_encryption",
                reviewer_id="platform-owner@example.go.id",
                change_ticket="CHG-2026-0713",
                expires_in_days=90,
            )
            self._write_evidence(evidence_file, database_evidence)
            self.assertTrue(
                storage_encryption_attestation_status(database_backend)["effective"]
            )

    def test_flag_alone_tamper_expiry_and_changed_binding_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings, key, evidence_file, _ = self._fixture(root)
            evidence = create_storage_encryption_attestation(
                settings,
                key=key,
                control_type="filevault",
                reviewer_id="platform-owner@example.go.id",
                change_ticket="CHG-2026-0713",
                expires_in_days=1,
            )

            flag_only = storage_encryption_attestation_status(settings)
            self.assertFalse(flag_only["effective"])
            self.assertIn("evidence_file_exists", flag_only["reasons"])

            tampered = {**evidence, "control_type": "luks2"}
            self._write_evidence(evidence_file, tampered)
            tampered_status = storage_encryption_attestation_status(settings)
            self.assertFalse(tampered_status["effective"])
            self.assertFalse(tampered_status["checks"]["signature_valid"])

            issued = datetime.now(timezone.utc) - timedelta(days=3)
            expired = create_storage_encryption_attestation(
                settings,
                key=key,
                control_type="filevault",
                reviewer_id="platform-owner@example.go.id",
                change_ticket="CHG-2026-0713",
                issued_at=issued,
                expires_in_days=1,
            )
            self._write_evidence(evidence_file, expired)
            expired_status = storage_encryption_attestation_status(settings)
            self.assertFalse(expired_status["effective"])
            self.assertFalse(expired_status["checks"]["expires_at_valid"])

            self._write_evidence(evidence_file, evidence)
            different_payload = root / "different-payloads"
            different_payload.mkdir(mode=0o700)
            moved_settings = settings.model_copy(
                update={"analysis_payload_storage_path": str(different_payload)}
            )
            moved_status = storage_encryption_attestation_status(moved_settings)
            self.assertFalse(moved_status["effective"])
            self.assertFalse(moved_status["checks"]["storage_binding_valid"])

    def test_public_permissions_symlink_and_disabled_flag_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings, key, evidence_file, key_file = self._fixture(root)
            evidence = create_storage_encryption_attestation(
                settings,
                key=key,
                control_type="cloud_kms",
                reviewer_id="platform-owner@example.go.id",
                change_ticket="CHG-2026-0713",
            )
            self._write_evidence(evidence_file, evidence)

            evidence_file.chmod(0o644)
            public_evidence = storage_encryption_attestation_status(settings)
            self.assertFalse(public_evidence["effective"])
            self.assertFalse(
                public_evidence["checks"]["evidence_file_private_permissions"]
            )
            evidence_file.chmod(0o600)

            evidence_file.write_bytes(b"{" + b"x" * (16 * 1024) + b"}")
            evidence_file.chmod(0o600)
            oversized_evidence = storage_encryption_attestation_status(settings)
            self.assertFalse(oversized_evidence["effective"])
            self.assertFalse(
                oversized_evidence["checks"]["evidence_file_size_safe"]
            )
            self._write_evidence(evidence_file, evidence)

            key_file.chmod(0o644)
            public_key = storage_encryption_attestation_status(settings)
            self.assertFalse(public_key["effective"])
            self.assertFalse(public_key["checks"]["key_file_private_permissions"])
            key_file.chmod(0o600)

            link = root / "evidence-link.json"
            link.symlink_to(evidence_file)
            linked_settings = settings.model_copy(
                update={"analysis_storage_encryption_evidence_path": str(link)}
            )
            linked = storage_encryption_attestation_status(linked_settings)
            self.assertFalse(linked["effective"])
            self.assertFalse(linked["checks"]["evidence_file_not_symlink"])

            disabled_settings = settings.model_copy(
                update={"analysis_payload_storage_encryption_validated": False}
            )
            disabled = storage_encryption_attestation_status(disabled_settings)
            self.assertFalse(disabled["effective"])
            self.assertFalse(disabled["checks"]["validation_flag_enabled"])

            database = Path(settings.database_path)
            database.chmod(0o644)
            public_database = storage_encryption_attestation_status(settings)
            self.assertFalse(public_database["effective"])
            self.assertFalse(
                public_database["checks"]["database_file_private_permissions"]
            )
            database.chmod(0o600)

            payload_root = Path(settings.analysis_payload_storage_path)
            payload_root.chmod(0o755)
            public_payload_root = storage_encryption_attestation_status(settings)
            self.assertFalse(public_payload_root["effective"])
            self.assertFalse(
                public_payload_root["checks"]["payload_directory_private_permissions"]
            )
            payload_root.chmod(0o700)

    def test_issuer_rejects_weak_key_unknown_control_and_invalid_nonce(self) -> None:
        with TemporaryDirectory() as directory:
            settings, _, _, _ = self._fixture(Path(directory))
            common = {
                "settings": settings,
                "reviewer_id": "platform-owner@example.go.id",
                "change_ticket": "CHG-2026-0713",
            }
            with self.assertRaises(ValueError):
                create_storage_encryption_attestation(
                    **common, key=b"weak", control_type="filevault"
                )
            with self.assertRaises(ValueError):
                create_storage_encryption_attestation(
                    **common, key=b"x" * 64, control_type="plain-disk"
                )
            with self.assertRaises(ValueError):
                create_storage_encryption_attestation(
                    **common,
                    key=b"x" * 64,
                    control_type="filevault",
                    nonce="not-random",
                )

    def test_cli_helpers_require_private_key_and_never_overwrite_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            key_file = root / "issuer.key"
            key_file.write_bytes(b"k" * 64)
            key_file.chmod(0o600)
            self.assertEqual(read_private_key(key_file), b"k" * 64)

            output = root / "attestation.json"
            write_private_json(output, {"safe": True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_private_json(output, {"safe": False})
            self.assertEqual(json.loads(output.read_text()), {"safe": True})

            key_file.chmod(0o644)
            with self.assertRaises(ValueError):
                read_private_key(key_file)

    def test_root_issuer_cli_creates_runtime_verifiable_content_free_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings, _, evidence_file, key_file = self._fixture(root)
            script = Path(__file__).resolve().parents[2] / "scripts" / "issue_storage_encryption_attestation.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--database-path",
                    settings.database_path,
                    "--payload-backend",
                    "filesystem",
                    "--payload-root",
                    settings.analysis_payload_storage_path,
                    "--key-file",
                    str(key_file),
                    "--output",
                    str(evidence_file),
                    "--control",
                    "encrypted_block_volume",
                    "--reviewer",
                    "platform-owner@example.go.id",
                    "--change-ticket",
                    "CHG-2026-0713",
                    "--expires-days",
                    "30",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertTrue(summary["created"])
            self.assertFalse(summary["path_exposed"])
            self.assertFalse(summary["signature_exposed"])
            self.assertNotIn(str(root), completed.stdout)
            self.assertNotIn("platform-owner@example.go.id", completed.stdout)
            self.assertEqual(evidence_file.stat().st_mode & 0o777, 0o600)
            self.assertTrue(
                storage_encryption_attestation_status(settings)["effective"]
            )


if __name__ == "__main__":
    unittest.main()
