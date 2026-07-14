from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.recommendations import attach_recommendations  # noqa: E402
from app.spip_mapping import EVIDENCE_CATEGORIES, KK_LIST, STATUS_EXPLANATIONS  # noqa: E402
from app.webdav_client import canonical_public_folder_url, public_folder_link  # noqa: E402


def main() -> None:
    settings = get_settings()
    seed_path = os.environ.get("SNAPSHOT_SEED_PATH")
    if seed_path and Path(seed_path).exists():
        payload = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        payload["health"]["webdav_configured"] = settings.has_share_token
        inject_public_urls(payload, settings)
    else:
        payload = build_snapshot_from_database(settings)

    output_path = ROOT / "frontend" / "public" / "snapshot.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported static snapshot to {output_path}")


def with_public_url(folder: dict, settings) -> dict:
    if not settings.has_share_token:
        return {**folder, "public_url": None}
    return {
        **folder,
        "public_url": public_folder_link(
            settings.lumbung_host,
            settings.lumbung_share_token,
            folder["folder_path"],
        ),
    }


def build_snapshot_from_database(settings) -> dict:
    db = Database(os.environ.get("DATABASE_PATH", str(ROOT / "data" / "evidence.db")))
    db.ensure_mapping()
    db.ensure_parameters()

    folders = [with_public_url(folder, settings) for folder in db.folders()]
    dashboard = build_dashboard(folders)
    kk_payload = [
        {
            "id": kk.id,
            "title": kk.title,
            "folder_name": kk.folder_name,
            "description": kk.description,
            "folders": [folder for folder in folders if folder["kk_id"] == kk.id],
        }
        for kk in KK_LIST
    ]

    subunsur_details = {}
    for folder in folders:
        parameters = db.parameters(folder["kk_id"], folder["kode"])
        slots = [with_public_url(slot, settings) for slot in db.evidence_slots(folder["kk_id"], folder["kode"])]
        attach_slots(parameters, slots)
        attach_recommendations(parameters)
        matrix_subunsur_name = parameters[0]["matrix_subunsur_name"] if parameters else None
        subunsur_details[f'{folder["kk_id"]}::{folder["kode"]}'] = {
            **folder,
            "matrix_subunsur_name": matrix_subunsur_name,
            "parameters": parameters,
            "evidence_slots": slots,
            "files": [sanitize_file(file) for file in db.files(folder["kk_id"], folder["kode"])],
        }

    return {
        "health": {
            "ok": True,
            "service": "spip-evidence-dashboard-static",
            "webdav_configured": settings.has_share_token,
        },
        "meta": {
            "status_explanations": STATUS_EXPLANATIONS,
            "evidence_categories": EVIDENCE_CATEGORIES,
        },
        "dashboard": dashboard,
        "kk": kk_payload,
        "subunsur_details": subunsur_details,
    }


def inject_public_urls(payload: dict, settings) -> None:
    if not settings.has_share_token:
        canonicalize_cached_public_urls(payload)
        return

    by_key = {}
    for folder in payload.get("dashboard", {}).get("folders", []):
        by_key[(folder["kk_id"], folder["kode"])] = public_folder_link(
            settings.lumbung_host,
            settings.lumbung_share_token,
            folder["folder_path"],
        )
        folder["public_url"] = by_key[(folder["kk_id"], folder["kode"])]

    for kk in payload.get("kk", []):
        for folder in kk.get("folders", []):
            url = by_key.get((folder["kk_id"], folder["kode"]))
            if url:
                folder["public_url"] = url

    for detail in payload.get("subunsur_details", {}).values():
        url = by_key.get((detail["kk_id"], detail["kode"]))
        if url:
            detail["public_url"] = url
        detail["files"] = [sanitize_file(file) for file in detail.get("files", [])]

    refresh_nested_public_urls(payload, settings)


def canonicalize_cached_public_urls(value) -> None:
    if isinstance(value, list):
        for item in value:
            canonicalize_cached_public_urls(item)
        return
    if not isinstance(value, dict):
        return

    folder_path = str(value.get("folder_path") or "").strip()
    if value.get("public_url"):
        value["public_url"] = canonical_public_folder_url(value.get("public_url"), folder_path)
    parameter_path = str(value.get("parameter_entry_folder_path") or "").strip()
    if value.get("parameter_entry_public_url"):
        value["parameter_entry_public_url"] = canonical_public_folder_url(
            value.get("parameter_entry_public_url"),
            parameter_path,
        )
    for item in value.values():
        canonicalize_cached_public_urls(item)


def refresh_nested_public_urls(value, settings) -> None:
    if isinstance(value, list):
        for item in value:
            refresh_nested_public_urls(item, settings)
        return
    if not isinstance(value, dict):
        return

    folder_path = str(value.get("folder_path") or "").strip()
    if folder_path:
        value["public_url"] = public_folder_link(
            settings.lumbung_host,
            settings.lumbung_share_token,
            folder_path,
        )
    parameter_path = str(value.get("parameter_entry_folder_path") or "").strip()
    if parameter_path:
        value["parameter_entry_public_url"] = public_folder_link(
            settings.lumbung_host,
            settings.lumbung_share_token,
            parameter_path,
        )
    for item in value.values():
        refresh_nested_public_urls(item, settings)


def sanitize_file(file: dict) -> dict:
    return {**file, "href": ""}


def attach_slots(parameters: list[dict], slots: list[dict]) -> None:
    slot_map: dict[tuple[str, str], list[dict]] = {}
    for slot in slots:
        slot_map.setdefault((slot["detail_kode"], slot["grade"]), []).append(slot)

    for parameter in parameters:
        detail_kode = parameter.get("detail_kode")
        for grade in parameter.get("grades", []):
            grade_value = str(grade.get("grade") or "").strip().upper()
            grade["evidence_folders"] = slot_map.get((detail_kode, grade_value), [])


def build_dashboard(folders: list[dict]) -> dict:
    status_counts: dict[str, int] = {}
    kk_summary: dict[str, dict] = {}

    for folder in folders:
        status_counts[folder["status"]] = status_counts.get(folder["status"], 0) + 1
        summary = kk_summary.setdefault(
            folder["kk_id"],
            {
                "kk_id": folder["kk_id"],
                "title": folder["kk_title"],
                "total": 0,
                "file_count": 0,
                "total_size_bytes": 0,
                "status_counts": {},
            },
        )
        summary["total"] += 1
        summary["file_count"] += folder["file_count"]
        summary["total_size_bytes"] += folder["total_size_bytes"]
        summary["status_counts"][folder["status"]] = summary["status_counts"].get(folder["status"], 0) + 1

    return {
        "total_folders": len(folders),
        "total_files": sum(folder["file_count"] for folder in folders),
        "total_size_bytes": sum(folder["total_size_bytes"] for folder in folders),
        "status_counts": status_counts,
        "kk_summary": list(kk_summary.values()),
        "folders": folders,
    }


if __name__ == "__main__":
    main()
