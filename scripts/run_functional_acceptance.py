from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from zipfile import ZipFile


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.analysis.orchestrator import AnalysisOrchestrator
from app.config import Settings
from app.database import Database


ACCEPTANCE_VERSION = "document-intelligence-functional-acceptance-v1"
REQUIRED_ENGINE_NAMES = {
    "file_intake_security",
    "file_router",
    "native_parsing",
    "visual_ocr",
    "document_structure",
    "unitization_coverage",
    "template_completeness",
    "fact_extraction",
    "compute_routing_fact",
    "parameter_retrieval",
    "spip_mapping",
    "compute_routing_mapping",
    "domain_rule_grade",
    "independent_verification",
    "compute_routing_verification",
    "output_explainability",
}
OPERATIONAL_KINDS = ("pdf", "docx", "xlsx", "image")
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image": "image/jpeg",
    "text": "text/plain",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
TARGETED_TESTS = (
    "test_analysis_api.AnalysisApiIntegrationTests.test_async_analysis_dedupe_readiness_and_upload_gate",
    "test_analysis_api.AnalysisApiIntegrationTests.test_approved_evidence_reaches_controlled_upload_boundary",
    "test_analysis_api.AnalysisApiIntegrationTests.test_batch_zip_intake_is_safe_local_deduplicated_and_legacy_isolated",
    "test_analysis_orchestrator.AnalysisOrchestratorTests",
    "test_document_map_engines.DocumentMapEngineTests",
    "test_local_ocr.LocalOCREngineTests",
    "test_decision_engines.DecisionEngineTests",
    "test_cross_document.CrossDocumentTests",
    "test_visual_review.VisualReviewWorkflowTests",
    "test_evaluation_learning.EvaluationLearningTests",
    "test_template_detection.TemplateCompletenessTests",
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _canonical_sha256(value: Any) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _minimal_pptx() -> bytes:
    """Create a content-contained parser fixture; rendering may fail closed."""
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:sldSz cx="9144000" cy="6858000"/></p:presentation>""",
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r>
              <a:t>Evaluasi SPIP dilaksanakan berkala tahun 2026</a:t>
              </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>""",
        )
    return buffer.getvalue()


def _select_operational_cases(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for kind in OPERATIONAL_KINDS:
        candidates = [item for item in results if item.get("file_kind") == kind]
        if not candidates:
            continue
        # Prefer a small document so the acceptance run is bounded. For PDF,
        # prefer a fully covered case; visual formats intentionally exercise
        # the review-required/fail-closed path.
        candidates.sort(
            key=lambda item: (
                0 if kind != "pdf" or item.get("coverage_status") == "completed" else 1,
                int(item.get("size_bytes") or 0),
                str(item.get("document_id") or ""),
            )
        )
        selected.append(candidates[0])
    return selected


def _select_ocr_rescue_case(
    results: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> dict[str, Any] | None:
    selected_names = {str(item.get("file_name") or "") for item in selected}
    candidates = [
        item
        for item in results
        if item.get("file_kind") == "image"
        and str(item.get("file_name") or "") not in selected_names
        and int((item.get("local_ocr") or {}).get("review_candidates") or 0) > 0
    ]
    return min(candidates, key=lambda item: int(item.get("size_bytes") or 0)) if candidates else None


def _summarize_run(case_id: str, file_kind: str, result: dict[str, Any]) -> dict[str, Any]:
    run = result["run"]
    engine_names = [str(item.get("engine_name") or "") for item in result.get("engines") or []]
    facts = result.get("facts") or []
    sources = [source for fact in facts for source in fact.get("sources") or []]
    missing_engines = sorted(REQUIRED_ENGINE_NAMES - set(engine_names))
    source_locations_complete = bool(sources) and all(
        bool(source.get("source_quote_verified")) and bool(source.get("source_location"))
        for source in sources
    )
    coverage_status = str(run.get("coverage_status") or "")
    incomplete_fail_closed = bool(
        coverage_status == "complete" or run.get("primary_blocked") is True
    )
    terminal_status = str(run.get("status") or "") in {
        "completed",
        "review_required",
        "blocked",
        "uploaded",
    }
    passed = bool(
        terminal_status
        and int(run.get("total_units") or 0) > 0
        and not missing_engines
        and incomplete_fail_closed
        and (not facts or source_locations_complete)
    )
    return {
        "case_id": case_id,
        "file_kind": file_kind,
        "operational_document": case_id.startswith("operational-"),
        "run_status": run.get("status"),
        "coverage_status": coverage_status,
        "primary_blocked": bool(run.get("primary_blocked")),
        "total_units": int(run.get("total_units") or 0),
        "processed_units": int(run.get("processed_units") or 0),
        "failed_units": int(run.get("failed_units") or 0),
        "ocr_required_units": int(run.get("ocr_required_units") or 0),
        "fact_count": len(facts),
        "mapping_count": len(result.get("mappings") or []),
        "grade_assessment_count": len(result.get("grade_assessments") or []),
        "verification_count": len(result.get("verification_results") or []),
        "engine_count": len(engine_names),
        "missing_required_engines": missing_engines,
        "source_locations_complete": source_locations_complete if facts else None,
        "incomplete_fail_closed": incomplete_fail_closed,
        "passed": passed,
    }


def _run_targeted_tests() -> dict[str, Any]:
    env = os.environ.copy()
    python_path = [str(BACKEND_ROOT), str(BACKEND_ROOT / "tests")]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    command = [
        sys.executable,
        "-W",
        "error::ResourceWarning",
        "-m",
        "unittest",
        "-q",
        *TARGETED_TESTS,
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        check=False,
    )
    match = re.search(r"Ran\s+(\d+)\s+tests?", completed.stdout)
    return {
        "passed": completed.returncode == 0,
        "test_count": int(match.group(1)) if match else None,
        "scenario_count": len(TARGETED_TESTS),
        "covers": [
            "async upload, dedupe, retry, and checkpoint reuse",
            "sandbox controlled upload boundary",
            "safe ZIP batch intake",
            "PDF/DOCX/XLSX/PPTX/image/text routing and parsing",
            "local OCR budgets and visual review",
            "retrieval, mapping, grade, verification, and cross-document synthesis",
            "expert evaluation and template detection",
        ],
        "failure_output": completed.stdout[-4000:] if completed.returncode else None,
    }


def _corpus_evidence(summary: dict[str, Any], queue: list[dict[str, Any]]) -> dict[str, Any]:
    format_counts = dict(summary.get("file_kind_counts") or {})
    queue_kinds = sorted({str(item.get("file_kind") or "") for item in queue})
    expected_empty = sum(
        not item.get("expected_mappings") or not item.get("expected_source_locations")
        for item in queue
    )
    passed = bool(
        int(summary.get("document_count") or 0) >= 50
        and all(int(format_counts.get(kind) or 0) > 0 for kind in OPERATIONAL_KINDS)
        and len(queue) >= 50
        and summary.get("local_only") is True
        and summary.get("external_ai_used") is False
        and int(summary.get("office_render_failure_count") or 0) == 0
    )
    return {
        "passed": passed,
        "document_count": int(summary.get("document_count") or 0),
        "format_counts": format_counts,
        "review_queue_case_count": len(queue),
        "review_queue_kinds": queue_kinds,
        "unlabelled_case_count": expected_empty,
        "local_ocr_processed_unit_count": int(
            summary.get("local_ocr_processed_unit_count") or 0
        ),
        "local_ocr_remaining_unit_count": int(
            summary.get("local_ocr_remaining_unit_count") or 0
        ),
        "ocr_rescue_candidate_count": int(
            summary.get("local_ocr_review_candidate_count") or 0
        ),
        "visual_review_pending_count": int(
            summary.get("local_ocr_visual_semantics_pending_count") or 0
        ),
        "office_render_failure_count": int(
            summary.get("office_render_failure_count") or 0
        ),
        "local_only": bool(summary.get("local_only")),
        "external_ai_used": bool(summary.get("external_ai_used")),
    }


def run_acceptance(
    *,
    archive_path: Path,
    corpus_dir: Path,
    database_path: Path,
    run_tests: bool = True,
) -> dict[str, Any]:
    summary = json.loads((corpus_dir / "corpus_summary.json").read_text(encoding="utf-8"))
    results = _load_jsonl(corpus_dir / "document_results.jsonl")
    review_queue = _load_jsonl(corpus_dir / "expert_review_queue_50.jsonl")
    selected = _select_operational_cases(results)
    selected_kinds = {str(item.get("file_kind") or "") for item in selected}
    if selected_kinds != set(OPERATIONAL_KINDS):
        missing = sorted(set(OPERATIONAL_KINDS) - selected_kinds)
        raise ValueError("Korpus tidak memiliki format operasional: " + ", ".join(missing))
    if database_path.exists():
        raise FileExistsError(
            f"Database acceptance sudah ada; pilih path baru agar bukti immutable: {database_path}"
        )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(database_path))
    db.ensure_mapping()
    db.ensure_parameters()
    settings = Settings(
        _env_file=None,
        database_path=str(database_path),
        app_env="functional_acceptance",
        analysis_pipeline_v2_enabled=True,
        analysis_pipeline_v2_shadow=False,
        legacy_smart_upload_enabled=True,
        smart_upload_allow_real_upload=False,
        analysis_worker_limit=1,
        analysis_expected_replicas=1,
        analysis_structured_model_enabled=False,
        analysis_mapping_reasoning_enabled=False,
        analysis_model_verifier_enabled=False,
        vision_analysis_enabled=False,
        analysis_vision_provider_validated=False,
        analysis_local_ocr_enabled=False,
        analysis_office_rendering_enabled=True,
        analysis_office_render_max_pages=8,
        deepseek_api_key="",
        sumopod_api_key="",
        ai_api_key="",
    )
    orchestrator = AnalysisOrchestrator(db, settings)
    ocr_settings = settings.model_copy(
        update={
            "analysis_local_ocr_enabled": True,
            "analysis_local_ocr_max_units": 2,
            "analysis_local_ocr_unit_budget_seconds": 60,
            "analysis_local_ocr_document_budget_seconds": 120,
            "analysis_local_ocr_max_attempts_per_unit": 8,
        }
    )
    ocr_orchestrator = AnalysisOrchestrator(db, ocr_settings)
    cases: list[dict[str, Any]] = []
    with ZipFile(archive_path) as archive:
        archive_names = set(archive.namelist())
        for index, item in enumerate(selected, start=1):
            member_name = str(item["file_name"])
            if member_name not in archive_names:
                raise FileNotFoundError(f"Member korpus tidak ditemukan: {member_name}")
            kind = str(item["file_kind"])
            case_orchestrator = ocr_orchestrator if kind == "image" else orchestrator
            result = case_orchestrator.start(
                file_name=Path(member_name).name,
                content_type=CONTENT_TYPES[kind],
                payload=archive.read(member_name),
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            cases.append(_summarize_run(f"operational-{kind}-{index}", kind, result))
        rescue_item = _select_ocr_rescue_case(results, selected)
        if rescue_item:
            rescue_name = str(rescue_item["file_name"])
            if rescue_name not in archive_names:
                raise FileNotFoundError(f"Member OCR Rescue tidak ditemukan: {rescue_name}")
            rescue_result = ocr_orchestrator.start(
                file_name=Path(rescue_name).name,
                content_type=CONTENT_TYPES["image"],
                payload=archive.read(rescue_name),
                analysis_mode="full_audit",
                external_ai_allowed=False,
            )
            cases.append(
                _summarize_run(
                    "operational-image-ocr-rescue",
                    "image",
                    rescue_result,
                )
            )

    synthetic_cases = (
        (
            "fixture-text",
            "text",
            "functional-acceptance.txt",
            b"Kebijakan indikator ditetapkan, dilaksanakan, dan dievaluasi berkala tahun 2026.",
        ),
        ("fixture-pptx", "pptx", "functional-acceptance.pptx", _minimal_pptx()),
    )
    for case_id, kind, file_name, payload in synthetic_cases:
        result = orchestrator.start(
            file_name=file_name,
            content_type=CONTENT_TYPES[kind],
            payload=payload,
            analysis_mode="full_audit",
            external_ai_allowed=False,
        )
        cases.append(_summarize_run(case_id, kind, result))

    corpus = _corpus_evidence(summary, review_queue)
    tests = _run_targeted_tests() if run_tests else {
        "passed": None,
        "test_count": None,
        "scenario_count": 0,
        "covers": [],
        "failure_output": None,
    }
    automation_passed = bool(
        corpus["passed"]
        and all(case["passed"] for case in cases)
        and (tests["passed"] is True if run_tests else True)
    )
    human_gates = {
        "status": "pending_human",
        "machine_must_not_autofill": True,
        "visual_review_pending_count": corpus["visual_review_pending_count"],
        "ocr_rescue_candidate_count": corpus["ocr_rescue_candidate_count"],
        "manual_transcription_count": max(
            0,
            corpus["local_ocr_remaining_unit_count"]
            - corpus["ocr_rescue_candidate_count"],
        ),
        "evaluation_queue_case_count": corpus["review_queue_case_count"],
        "evaluation_unlabelled_case_count": corpus["unlabelled_case_count"],
        "domain_rule_contract_count": 920,
        "domain_parameter_count": 184,
    }
    report: dict[str, Any] = {
        "acceptance_version": ACCEPTANCE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "v2_functional_acceptance_sandbox",
        "production_security_hardening_in_scope": False,
        "local_only": True,
        "external_ai_used": False,
        "real_upload_attempted": False,
        "automated_status": "passed" if automation_passed else "failed",
        "functional_acceptance_status": (
            "pending_human" if automation_passed else "automation_failed"
        ),
        "corpus_evidence": corpus,
        "end_to_end_cases": cases,
        "targeted_regression": tests,
        "sandbox_controlled_upload": {
            "passed": tests.get("passed") is True if run_tests else None,
            "transport": "mocked_legacy_bridge_in_temporary_database",
            "real_external_write": False,
        },
        "human_gates": human_gates,
        "database_path": str(database_path),
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    cases = report["end_to_end_cases"]
    lines = [
        "# V2 Functional Acceptance",
        "",
        f"Contract: `{report['acceptance_version']}`  ",
        f"Generated: `{report['generated_at']}`  ",
        f"Automated status: **{report['automated_status']}**  ",
        f"Final functional status: **{report['functional_acceptance_status']}**",
        "",
        "## End-to-end cases",
        "",
        "| Case | Format | Run | Coverage | Units | Facts | Mappings | Source | Fail-closed | Result |",
        "|---|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for case in cases:
        source = case["source_locations_complete"]
        lines.append(
            "| {case_id} | {file_kind} | {run_status} | {coverage_status} | {total_units} | "
            "{fact_count} | {mapping_count} | {source} | {fail_closed} | {result} |".format(
                **case,
                source="n/a" if source is None else ("valid" if source else "invalid"),
                fail_closed="yes" if case["incomplete_fail_closed"] else "no",
                result="passed" if case["passed"] else "failed",
            )
        )
    corpus = report["corpus_evidence"]
    human = report["human_gates"]
    lines.extend(
        [
            "",
            "## Operational corpus",
            "",
            f"- Documents: {corpus['document_count']}",
            f"- Local OCR processed/remaining: {corpus['local_ocr_processed_unit_count']}/{corpus['local_ocr_remaining_unit_count']}",
            f"- Visual review pending: {human['visual_review_pending_count']}",
            f"- OCR Rescue candidates: {human['ocr_rescue_candidate_count']}",
            f"- Evaluation queue/unlabelled: {human['evaluation_queue_case_count']}/{human['evaluation_unlabelled_case_count']}",
            "",
            "## Interpretation",
            "",
            "Automation, format routing, engine sequencing, fail-closed behavior, resume, and the sandbox upload boundary may pass without declaring expert truth. Human visual review, expected mappings/source locations, grades, and domain-rule approval remain pending and are never auto-filled by this runner.",
            "",
            f"Report SHA-256: `{report['report_sha256']}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local V2 functional acceptance without production writes."
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    report = run_acceptance(
        archive_path=args.archive.expanduser().resolve(),
        corpus_dir=args.corpus_dir.expanduser().resolve(),
        database_path=args.database.expanduser().resolve(),
        run_tests=not args.skip_tests,
    )
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        markdown = args.markdown.expanduser().resolve()
        markdown.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(markdown, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["automated_status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
