from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ALLOWED_STATUS = {"pilot_unlabelled", "expert_candidate", "expert_gold"}
ALLOWED_CONSENT = {"local_analysis_only", "regression_testing", "expert_labelling"}
ALLOWED_SENSITIVITY = {"public", "internal", "restricted"}
AUTOMATED_LABEL_MARKERS = ("synthetic", "bootstrap", "generator", "automated")


def validate(path: Path, document_root: Path | None = None) -> dict:
    errors: list[str] = []
    items: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: JSON tidak valid: {exc}")
            continue
        items.append(item)
        prefix = f"line {line_number}"
        for field in ("document_id", "file_name", "sha256", "consent_scope", "sensitivity", "dataset_status", "case_types"):
            if not item.get(field):
                errors.append(f"{prefix}: {field} wajib diisi")
        if item.get("dataset_status") not in ALLOWED_STATUS:
            errors.append(f"{prefix}: dataset_status tidak valid")
        if item.get("consent_scope") not in ALLOWED_CONSENT:
            errors.append(f"{prefix}: consent_scope tidak valid")
        if item.get("sensitivity") not in ALLOWED_SENSITIVITY:
            errors.append(f"{prefix}: sensitivity tidak valid")
        if len(str(item.get("sha256") or "")) != 64:
            errors.append(f"{prefix}: sha256 harus 64 karakter")
        if item.get("dataset_status") == "expert_gold":
            reviewer = str(item.get("labelled_by") or "").strip().lower()
            if not reviewer or any(marker in reviewer for marker in AUTOMATED_LABEL_MARKERS):
                errors.append(f"{prefix}: expert_gold membutuhkan reviewer manusia")
            if not item.get("labelled_at") or not item.get("expected_mappings") or not item.get("expected_source_locations"):
                errors.append(f"{prefix}: expert_gold membutuhkan waktu, mapping, dan source location")
            if item.get("consent_scope") != "expert_labelling":
                errors.append(f"{prefix}: expert_gold membutuhkan consent_scope expert_labelling")
        if document_root:
            document = (document_root / str(item.get("file_name") or "")).resolve()
            if document_root.resolve() not in document.parents:
                errors.append(f"{prefix}: file_name keluar dari document root")
            elif not document.is_file():
                errors.append(f"{prefix}: file dokumen tidak ditemukan")
            elif hashlib.sha256(document.read_bytes()).hexdigest() != item.get("sha256"):
                errors.append(f"{prefix}: checksum dokumen tidak cocok")
    identifiers = [str(item.get("document_id") or "") for item in items]
    if len(identifiers) != len(set(identifiers)):
        errors.append("document_id harus unik")
    return {
        "manifest": str(path),
        "document_count": len(items),
        "expert_gold_count": sum(item.get("dataset_status") == "expert_gold" for item in items),
        "valid": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a document corpus manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--document-root", type=Path)
    args = parser.parse_args()
    report = validate(args.manifest, args.document_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
