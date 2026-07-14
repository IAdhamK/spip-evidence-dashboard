from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOC_MARKERS = {
    "docs/API_DOCUMENT_INTELLIGENCE_V2.md": (
        "GET /openapi.json",
        "trusted header",
        "analysis-rbac-v1",
        "Server-Sent Events",
        "controlled-upload",
    ),
    "docs/SCHEMA_DOCUMENT_INTELLIGENCE_V2.md": (
        "Migration V1–V32",
        "forward-only",
        "append-only",
        "integrity_check=ok",
    ),
    "docs/RULE_AUTHORING_GUIDE.md": (
        "backend/app/spip_parameters.json",
        "required_stages",
        "required_source_types",
        "prerequisite_grade",
        "template_only",
        "plan_without_result",
        "rule_checksum",
        "920",
    ),
    "docs/EVAL_AUTHORING_GUIDE.md": (
        "evaluation",
        "learning",
        "retrieval_recall_at_5",
        "source_accuracy",
        "overgrade_rate",
        "template_detection_recall",
        "--minimum-cases 50",
        "--minimum-cases 200",
    ),
    "docs/HANDOVER_CHECKLIST_DOCUMENT_INTELLIGENCE.md": (
        "knowledge transfer",
        "Incident simulation",
        "Domain owner",
        "Product owner",
        "Pending",
    ),
}

TABLE_PATTERN = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_]+)", re.IGNORECASE)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_required_docs(root: Path, errors: list[str]) -> dict[str, str]:
    documents: dict[str, str] = {}
    for relative_path, markers in REQUIRED_DOC_MARKERS.items():
        path = root / relative_path
        if not path.is_file():
            errors.append(f"Dokumen wajib hilang: {relative_path}")
            continue
        content = path.read_text(encoding="utf-8")
        documents[relative_path] = content
        for marker in markers:
            if marker not in content:
                errors.append(f"{relative_path} belum memuat marker kontrak: {marker}")
    return documents


def _schema_tables(root: Path) -> list[str]:
    source = "\n".join(
        (root / relative_path).read_text(encoding="utf-8")
        for relative_path in ("backend/app/database.py", "backend/app/migrations.py")
    )
    return sorted(set(TABLE_PATTERN.findall(source)))


def _openapi_contract(root: Path, database_path: Path) -> dict[str, Any]:
    backend_root = root / "backend"
    for import_root in (root, backend_root):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))

    os.environ["DATABASE_PATH"] = str(database_path)
    os.environ.pop("STATIC_DIR", None)

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    return app.openapi()


def validate_handover(root: Path, database_path: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    documents = _read_required_docs(root, errors)

    schema_doc = documents.get("docs/SCHEMA_DOCUMENT_INTELLIGENCE_V2.md", "")
    tables = _schema_tables(root)
    for table in tables:
        if f"`{table}`" not in schema_doc:
            errors.append(f"Referensi schema belum mencakup tabel: {table}")

    openapi = _openapi_contract(root, database_path)
    from app.analysis.reviewer_identity import (
        AUTHORIZATION_POLICY_VERSION,
        PROXY_BOUNDARY_OPERATIONS,
        SECURED_OPERATION_ROLES,
    )

    v2_paths = sorted(
        path
        for path in openapi.get("paths", {})
        if path == "/api/analysis-runs"
        or path.startswith("/api/analysis-runs/")
        or path == "/api/analysis-packages"
        or path.startswith("/api/analysis-packages/")
    )
    api_doc = documents.get("docs/API_DOCUMENT_INTELLIGENCE_V2.md", "")
    for path in v2_paths:
        if f"`{path}`" not in api_doc:
            errors.append(f"Referensi API belum mencakup route OpenAPI: {path}")

    operation_count = sum(
        method.lower() in {"get", "post", "put", "patch", "delete"}
        for path in v2_paths
        for method in openapi["paths"][path]
    )
    openapi_operations = {
        (method.upper(), path)
        for path in v2_paths
        for method in openapi["paths"][path]
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    secured_operations = set(SECURED_OPERATION_ROLES)
    proxy_operations = set(PROXY_BOUNDARY_OPERATIONS)
    if secured_operations & proxy_operations:
        errors.append("Kontrak authorization mempunyai klasifikasi operasi yang tumpang tindih.")
    missing_authorization = openapi_operations - secured_operations - proxy_operations
    stale_authorization = (secured_operations | proxy_operations) - openapi_operations
    for method, path in sorted(missing_authorization):
        errors.append(f"Operasi OpenAPI belum mempunyai authorization policy: {method} {path}")
    for method, path in sorted(stale_authorization):
        errors.append(f"Authorization policy mengacu route yang tidak aktif: {method} {path}")
    if any(method != "GET" for method, _path in proxy_operations):
        errors.append("Mutation V2 tidak boleh diklasifikasikan hanya sebagai proxy boundary.")
    digest_payload = b"\n".join(
        relative_path.encode("utf-8") + b":" + _sha256(content.encode("utf-8")).encode("ascii")
        for relative_path, content in sorted(documents.items())
    )
    return {
        "contract": "document-intelligence-handover-v1",
        "valid": not errors,
        "document_count": len(documents),
        "openapi_path_count": len(v2_paths),
        "openapi_operation_count": operation_count,
        "authorization_policy_version": AUTHORIZATION_POLICY_VERSION,
        "role_secured_operation_count": len(secured_operations),
        "proxy_boundary_operation_count": len(proxy_operations),
        "schema_table_count": len(tables),
        "documentation_sha256": _sha256(digest_payload),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Document Intelligence V2 handover documentation against code contracts."
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--database-path", type=Path)
    args = parser.parse_args()

    if args.database_path:
        report = validate_handover(args.root, args.database_path)
    else:
        with TemporaryDirectory(prefix="spip-handover-") as temporary_directory:
            report = validate_handover(
                args.root,
                Path(temporary_directory) / "handover-openapi.db",
            )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
