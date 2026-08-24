from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.analysis.contracts import DocumentIdentity
from app.analysis.domain.retrieval import infer_document_role
from app.analysis.orchestrator import AnalysisOrchestrator
from app.analysis.workbook_evidence import build_workbook_evidence_view
from app.analysis.workbook_sheet_retrieval import configured_sheet_retrieval_fallback
from app.config import Settings
from app.database import Database


ACCEPTANCE_VERSION = "workbook-multi-evidence-operational-acceptance-v1"
XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
DEFAULT_FILENAMES = {
    "draft-petris-pdp": "(Rev) 1.Draft Matriks Petris PDP Renstra 2025-2029 PDP.xlsx",
    "dit-rentek": "Dit Rentek.xlsx",
    "dit-advoker": "Dit. Advoker (Rev).xlsx",
    "dit-fpdd": "Dit. FPDD.xlsx",
    "dit-sarpras": "Dit. Sarpras rev.xlsx",
    "dit-sosbud": "Dit. Sosbud.xlsx",
    "setditjen": "Setditjen.xlsx",
}
EXCLUDED_SHEET_STATUSES = {
    "empty",
    "hidden",
    "instruction_only",
    "no_substantive_facts",
    "not_processed",
    "template_only",
    "unreadable",
}
RAW_LOG_KEYS = {"claim", "raw_content", "source_quote", "text"}
REDUCTION_RE = re.compile(
    r"\b(?:menurun|penurunan|berkurang|pengurangan|risiko residual[^.]{0,80}(?:rendah|turun))\b",
    flags=re.IGNORECASE,
)


def _settings(database_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_path=str(database_path),
        app_env="workbook_acceptance",
        analysis_pipeline_v2_enabled=True,
        analysis_pipeline_v2_shadow=False,
        legacy_smart_upload_enabled=True,
        smart_upload_allow_real_upload=False,
        smart_upload_require_confirmation=True,
        analysis_worker_limit=1,
        analysis_expected_replicas=1,
        analysis_structured_model_enabled=False,
        analysis_mapping_reasoning_enabled=False,
        analysis_model_verifier_enabled=False,
        analysis_advanced_rag_enabled=True,
        analysis_advanced_rag_deepseek_enabled=False,
        vision_analysis_enabled=False,
        analysis_vision_provider_validated=False,
        analysis_local_ocr_enabled=False,
        analysis_office_rendering_enabled=False,
        deepseek_api_key="",
        sumopod_api_key="",
        ai_api_key="",
    )


def _identity(run: dict[str, Any]) -> DocumentIdentity:
    return DocumentIdentity(
        file_name=str(run.get("file_name") or "fixture.xlsx"),
        content_type=run.get("content_type"),
        size_bytes=int(run.get("size_bytes") or 0),
        sha256=str(run.get("sha256") or ""),
        file_kind="xlsx",
    )


def _workbook_view(
    result: dict[str, Any],
    orchestrator: AnalysisOrchestrator,
    settings: Settings,
) -> dict[str, Any]:
    run = result["run"]
    facts = result.get("facts") or []
    units = result.get("document_units") or []
    mappings = result.get("mappings") or []
    workbook = build_workbook_evidence_view(
        run_status=str(run.get("status") or ""),
        file_kind="xlsx",
        document_units=units,
        facts=facts,
        mapping_candidates=mappings,
        grade_assessments=result.get("grade_assessments") or [],
        verification_results=result.get("verification_results") or [],
    )
    if not any(
        sheet.get("status") == "needs_sheet_retrieval"
        for sheet in workbook.get("sheet_results") or []
    ):
        return workbook
    document_role = (
        str(mappings[0].get("document_role") or "")
        if mappings else infer_document_role(_identity(run), facts)
    )
    return configured_sheet_retrieval_fallback(settings).apply(
        workbook_evidence=workbook,
        identity=_identity(run),
        document_units=units,
        facts=facts,
        parameters=orchestrator.repository.parameter_index(),
        document_role=document_role,
        feedback_terms=orchestrator.repository.active_retrieval_feedback_terms(),
        external_ai_allowed=False,
    )


def _parameter_key(parameter: dict[str, Any] | None) -> str:
    parameter = parameter or {}
    values = [
        str(parameter.get("kk_id") or ""),
        str(parameter.get("kode") or ""),
        str(parameter.get("detail_kode") or ""),
    ]
    return "|".join(values) if all(values) else ""


def _candidate_parameters(workbook: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = []
    for sheet in workbook.get("sheet_results") or []:
        for role, parameter in [
            ("primary", sheet.get("primary_parameter")),
            *(('secondary', item) for item in sheet.get("secondary_parameters") or []),
        ]:
            key = _parameter_key(parameter)
            if not key:
                continue
            parameters.append({
                "parameter_key": key,
                "source_sheet": sheet.get("sheet_name"),
                "role": role,
            })
    return sorted(
        {
            (item["parameter_key"], str(item.get("source_sheet") or ""), item["role"]): item
            for item in parameters
        }.values(),
        key=lambda item: (
            str(item.get("source_sheet") or ""),
            0 if item.get("role") == "primary" else 1,
            item["parameter_key"],
        ),
    )


def _warning_codes(workbook: dict[str, Any], units: list[dict[str, Any]]) -> list[str]:
    summary = workbook.get("workbook_summary") or {}
    codes = {
        str(code)
        for code in summary.get("warning_codes") or []
        if str(code).strip()
    }
    sheets = workbook.get("sheet_results") or []
    for sheet in sheets:
        codes.update(
            str(code)
            for code in sheet.get("warning_codes") or []
            if str(code).strip()
        )
        status = str(sheet.get("status") or "")
        if status in {"ambiguous", "needs_review"}:
            codes.add("AMBIGUOUS_SHEET_MAPPING")
        if status == "sheet_retrieval_recommended":
            codes.add("SHEET_RETRIEVAL_RECOMMENDATION")
        if status in EXCLUDED_SHEET_STATUSES:
            codes.add("EXCLUDED_NON_SUBSTANTIVE_SHEET")
    for unit in units:
        metadata = unit.get("metadata") or {}
        codes.update(
            str(code)
            for code in metadata.get("formula_error_codes") or []
            if str(code).strip()
        )
    return sorted(codes)


def _contains_raw_log_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in RAW_LOG_KEYS
            or _contains_raw_log_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_raw_log_key(item) for item in value)
    return False


def _sheet(
    workbook: dict[str, Any],
    name_fragment: str,
) -> dict[str, Any] | None:
    fragment = name_fragment.casefold()
    return next(
        (
            item for item in workbook.get("sheet_results") or []
            if fragment in str(item.get("sheet_name") or "").casefold()
        ),
        None,
    )


def _sheet_has_parameter(sheet: dict[str, Any] | None, detail_kode: str) -> bool:
    if not sheet:
        return False
    return any(
        str(parameter.get("detail_kode") or "") == detail_kode
        for parameter in [
            sheet.get("primary_parameter"),
            *(sheet.get("secondary_parameters") or []),
        ]
        if parameter
    )


def _supporting_fact_ids(
    workbook: dict[str, Any],
    detail_kode: str,
) -> set[int]:
    values: set[int] = set()
    for sheet in workbook.get("sheet_results") or []:
        for parameter in [
            sheet.get("primary_parameter"),
            *(sheet.get("secondary_parameters") or []),
        ]:
            if not parameter or str(parameter.get("detail_kode") or "") != detail_kode:
                continue
            values.update(
                int(value)
                for value in parameter.get("supporting_fact_ids") or []
                if str(value).isdigit()
            )
    return values


def _expectations(
    alias: str,
    result: dict[str, Any],
    workbook: dict[str, Any],
    warning_codes: list[str],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    sheets = workbook.get("sheet_results") or []
    parameters = _candidate_parameters(workbook)
    facts_by_id = {
        int(fact["id"]): fact
        for fact in result.get("facts") or []
        if str(fact.get("id") or "").isdigit()
    }

    def require(condition: bool, category: str, code: str) -> None:
        if not condition:
            failures.append({"category": category, "code": code})

    require(bool(sheets), "parser issue", "NO_SHEETS_PARSED")
    require(
        all(item.get("source_sheet") for item in parameters),
        "source attribution issue",
        "PARAMETER_WITHOUT_SOURCE_SHEET",
    )
    require(
        all(not bool(item.get("primary_allowed")) for item in result.get("grade_assessments") or []),
        "retrieval issue",
        "AUTOMATIC_GRADE_AUTHORITY_DETECTED",
    )
    require(
        not (result.get("controlled_upload_actions") or []),
        "UI issue",
        "CONTROLLED_UPLOAD_ACTION_DETECTED",
    )
    require(
        not _contains_raw_log_key(result.get("events") or [])
        and not _contains_raw_log_key(result.get("engines") or []),
        "data-quality warning",
        "RAW_CONTENT_KEY_IN_OPERATIONAL_LOG",
    )
    require(
        all(
            not sheet.get("primary_parameter")
            for sheet in sheets
            if str(sheet.get("status") or "") in EXCLUDED_SHEET_STATUSES
        ),
        "source attribution issue",
        "EXCLUDED_SHEET_PROMOTED_TO_PRIMARY",
    )

    if alias == "dit-rentek":
        register = next(
            (
                item for item in sheets
                if str(
                    (item.get("primary_parameter") or {}).get("detail_kode") or ""
                ) == "2.1.2"
                and str(item.get("evidence_role") or "") == "primary"
            ),
            None,
        )
        require(register is not None, "retrieval issue", "REGISTER_212_NOT_PRIMARY")
    elif alias == "dit-advoker":
        register = next(
            (
                item for item in sheets
                if str(
                    (item.get("primary_parameter") or {}).get("detail_kode") or ""
                ) == "2.1.2"
            ),
            None,
        )
        require(
            bool(register and str(register.get("status") or "") not in EXCLUDED_SHEET_STATUSES),
            "retrieval issue",
            "SUBSTANTIVE_REGISTER_212_MISSING",
        )
    elif alias == "dit-fpdd":
        require(
            _sheet_has_parameter(_sheet(workbook, "petris 2026"), "2.1.2"),
            "retrieval issue",
            "PETRIS_2026_212_MISSING",
        )
        require(
            _sheet_has_parameter(_sheet(workbook, "monitoring rtp set 2025"), "2.2.4"),
            "retrieval issue",
            "MONITORING_RTP_224_MISSING",
        )
        effectiveness_ids = _supporting_fact_ids(workbook, "2.2.5")
        if effectiveness_ids:
            has_reduction_fact = any(
                REDUCTION_RE.search(str(facts_by_id.get(fact_id, {}).get("claim") or ""))
                for fact_id in effectiveness_ids
            )
            require(
                has_reduction_fact,
                "retrieval issue",
                "EFFECTIVENESS_225_WITHOUT_RISK_REDUCTION_FACT",
            )
        require(
            len({item["parameter_key"] for item in parameters}) > 1,
            "retrieval issue",
            "WORKBOOK_COLLAPSED_TO_SINGLE_212",
        )
    elif alias == "draft-petris-pdp":
        require(
            bool(workbook.get("is_multi_evidence")),
            "source attribution issue",
            "MULTI_EVIDENCE_NOT_DETECTED",
        )
        require(
            "MIXED_PERIODS_ACROSS_SHEETS" in warning_codes,
            "data-quality warning",
            "MIXED_PERIOD_WARNING_MISSING",
        )
        parser_has_ref = any(
            "#REF!" in str(unit.get("text") or "").upper()
            for unit in result.get("document_units") or []
        )
        if parser_has_ref:
            require(
                "XLSX_FORMULA_REF_ERROR" in warning_codes,
                "parser issue",
                "FORMULA_REF_WARNING_MISSING",
            )
    elif alias == "setditjen":
        organizations = {
            str(organization)
            for sheet in sheets
            for organization in (sheet.get("context") or {}).get("organizations") or []
            if str(organization).strip()
        }
        if len(organizations) > 1:
            require(
                "MIXED_ORGANIZATIONS_ACROSS_SHEETS" in warning_codes,
                "data-quality warning",
                "MIXED_ORGANIZATION_WARNING_MISSING",
            )
        inspector_sheet = _sheet(workbook, "inspektur iii")
        if inspector_sheet:
            inspector_orgs = {
                str(value).casefold()
                for value in (inspector_sheet.get("context") or {}).get("organizations") or []
            }
            require(
                not any("sekretariat direktorat jenderal" in value for value in inspector_orgs),
                "source attribution issue",
                "INSPECTOR_SHEET_SILENTLY_ASSIGNED_TO_SETDITJEN",
            )
        primary_keys = {
            _parameter_key(sheet.get("primary_parameter"))
            for sheet in sheets
            if sheet.get("primary_parameter")
        }
        require(
            bool(workbook.get("is_multi_evidence")) or len(primary_keys) != 1,
            "source attribution issue",
            "SETDITJEN_FORCED_TO_SINGLE_FINAL_RESULT",
        )
    return failures


def run_fixture(
    alias: str,
    path: Path,
    orchestrator: AnalysisOrchestrator,
    settings: Settings,
) -> dict[str, Any]:
    result = orchestrator.start(
        file_name=path.name,
        content_type=XLSX_CONTENT_TYPE,
        payload=path.read_bytes(),
        analysis_mode="full_audit",
        external_ai_allowed=False,
    )
    workbook = _workbook_view(result, orchestrator, settings)
    warning_codes = _warning_codes(
        workbook,
        result.get("document_units") or [],
    )
    failures = _expectations(alias, result, workbook, warning_codes)
    return {
        "alias": alias,
        "sheet_count": int(workbook.get("sheet_count") or 0),
        "substantive_sheet_count": int(workbook.get("substantive_sheet_count") or 0),
        "parameters": _candidate_parameters(workbook),
        "warning_codes": warning_codes,
        "status": "pass" if not failures else "fail",
        "mismatches": failures,
    }


def _fixtures(args: argparse.Namespace) -> list[tuple[str, Path]]:
    values: dict[str, Path] = {}
    if args.fixture_dir:
        root = Path(args.fixture_dir).expanduser().resolve()
        values.update({alias: root / name for alias, name in DEFAULT_FILENAMES.items()})
    for value in args.fixture or []:
        alias, separator, raw_path = value.partition("=")
        if not separator or alias not in DEFAULT_FILENAMES:
            raise ValueError("Fixture harus memakai format alias=/path dan alias yang dikenal.")
        values[alias] = Path(raw_path).expanduser().resolve()
    missing = [alias for alias in DEFAULT_FILENAMES if alias not in values]
    if missing:
        raise ValueError("Fixture belum lengkap: " + ", ".join(missing))
    missing_files = [alias for alias, path in values.items() if not path.is_file()]
    if missing_files:
        raise FileNotFoundError("Fixture tidak ditemukan: " + ", ".join(missing_files))
    return [(alias, values[alias]) for alias in DEFAULT_FILENAMES]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run content-minimized local acceptance for workbook multi-evidence.",
    )
    parser.add_argument("--fixture-dir")
    parser.add_argument("--fixture", action="append")
    parser.add_argument(
        "--output",
        default="/private/tmp/spip-workbook-multi-evidence-acceptance/report.json",
    )
    args = parser.parse_args()
    fixtures = _fixtures(args)
    output_path = Path(args.output).expanduser().resolve()
    if output_path == REPO_ROOT or REPO_ROOT in output_path.parents:
        raise ValueError("Report operasional harus disimpan di luar repository Git.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="spip-workbook-acceptance-") as directory:
        database_path = Path(directory) / "acceptance.db"
        database = Database(str(database_path))
        database.ensure_mapping()
        database.ensure_parameters()
        settings = _settings(database_path)
        orchestrator = AnalysisOrchestrator(database, settings)
        results = []
        for alias, path in fixtures:
            print(f"[{alias}] acceptance started", flush=True)
            item = run_fixture(alias, path, orchestrator, settings)
            results.append(item)
            print(f"[{alias}] {item['status']}", flush=True)

    mismatches = [
        {"alias": item["alias"], **mismatch}
        for item in results
        for mismatch in item["mismatches"]
    ]
    report = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content_minimized": True,
        "fixtures": results,
        "summary": {
            "fixture_count": len(results),
            "passed_count": sum(item["status"] == "pass" for item in results),
            "failed_count": sum(item["status"] == "fail" for item in results),
            "false_positive": [
                item for item in mismatches
                if item["code"] in {
                    "EFFECTIVENESS_225_WITHOUT_RISK_REDUCTION_FACT",
                    "EXCLUDED_SHEET_PROMOTED_TO_PRIMARY",
                    "INSPECTOR_SHEET_SILENTLY_ASSIGNED_TO_SETDITJEN",
                }
            ],
            "false_negative": [
                item for item in mismatches
                if item["code"] in {
                    "MONITORING_RTP_224_MISSING",
                    "PETRIS_2026_212_MISSING",
                    "REGISTER_212_NOT_PRIMARY",
                    "SUBSTANTIVE_REGISTER_212_MISSING",
                    "WORKBOOK_COLLAPSED_TO_SINGLE_212",
                }
            ],
            "human_review": [
                {
                    "alias": item["alias"],
                    "code": code,
                }
                for item in results
                for code in item["warning_codes"]
                if code in {
                    "AMBIGUOUS_SHEET_MAPPING",
                    "MIXED_ORGANIZATIONS_ACROSS_SHEETS",
                    "MIXED_PERIODS_ACROSS_SHEETS",
                    "SHEET_RETRIEVAL_RECOMMENDATION",
                }
            ],
        },
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Report: {output_path}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
