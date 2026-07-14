from __future__ import annotations

import math
import re


CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def render_prometheus_metrics(
    metrics: dict,
    alerting: dict,
    worker: dict,
    *,
    pipeline_version: str,
    cost_alert_usd_per_hour: float = 10.0,
    storage_encryption_attestation: dict | None = None,
) -> str:
    lines: list[str] = []

    def family(name: str, help_text: str, metric_type: str = "gauge") -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} {metric_type}"])

    def sample(name: str, value: object, labels: dict[str, object] | None = None) -> None:
        numeric = _finite_number(value)
        label_text = ""
        if labels:
            items = ",".join(
                f'{_label_name(key)}="{_escape_label(value)}"'
                for key, value in sorted(labels.items())
            )
            label_text = "{" + items + "}"
        lines.append(f"{name}{label_text} {numeric}")

    family("spip_analysis_info", "Document Intelligence pipeline build information.")
    sample("spip_analysis_info", 1, {"pipeline_version": pipeline_version})

    family("spip_analysis_queue_jobs", "Analysis jobs by queue status.")
    for status, total in sorted((metrics.get("queue_by_status") or {}).items()):
        sample("spip_analysis_queue_jobs", total, {"status": status})

    job_recovery = metrics.get("job_recovery") or {}
    family(
        "spip_analysis_job_recovery_total",
        "Stored content-free job recovery counters by event category.",
        "counter",
    )
    for event, key in (
        ("recovered_job", "recovered_job_count"),
        ("lease_retry_attempt", "lease_retry_attempt_count"),
        ("resume_lineage", "resume_lineage_job_count"),
    ):
        sample(
            "spip_analysis_job_recovery_total",
            job_recovery.get(key),
            {"event": event},
        )
    family(
        "spip_analysis_job_recovery_active_loops",
        "Active jobs that reached at least a third lease claim.",
    )
    sample(
        "spip_analysis_job_recovery_active_loops",
        job_recovery.get("active_recovery_loop_count"),
    )

    family("spip_analysis_runs_total", "Analysis runs by terminal/current status.", "counter")
    for status, total in sorted((metrics.get("runs_by_status") or {}).items()):
        sample("spip_analysis_runs_total", total, {"status": status})

    family("spip_analysis_engine_results_total", "Engine results by engine and status.", "counter")
    for key, total in sorted((metrics.get("engines") or {}).items()):
        engine, _, status = str(key).partition(":")
        sample(
            "spip_analysis_engine_results_total",
            total,
            {"engine": engine, "status": status or "unknown"},
        )

    scalar_metrics = {
        "spip_analysis_run_count": (metrics.get("run_count"), "Total stored analysis runs.", "counter"),
        "spip_analysis_run_duration_average_seconds": (
            metrics.get("average_duration_seconds"),
            "Average duration of completed analysis runs.",
            "gauge",
        ),
        "spip_analysis_complete_coverage_total": (
            metrics.get("complete_coverage_count"),
            "Runs with complete document coverage.",
            "counter",
        ),
        "spip_analysis_ocr_runs_total": (
            metrics.get("ocr_run_count"),
            "Runs containing OCR activity.",
            "counter",
        ),
        "spip_analysis_primary_blocked_total": (
            metrics.get("primary_blocked_count"),
            "Runs blocked from primary upload.",
            "counter",
        ),
        "spip_analysis_estimated_cost_usd_total": (
            metrics.get("estimated_cost_usd"),
            "Estimated model cost in USD.",
            "counter",
        ),
    }
    for name, (value, help_text, metric_type) in scalar_metrics.items():
        family(name, help_text, metric_type)
        sample(name, value)

    family(
        "spip_analysis_cost_budget_usd_per_hour",
        "Configured estimated model cost budget in USD per rolling hour.",
    )
    sample(
        "spip_analysis_cost_budget_usd_per_hour",
        max(0.0, _number(cost_alert_usd_per_hour)),
    )

    ocr_resource = metrics.get("ocr_resource") or {}
    family(
        "spip_analysis_ocr_resource_events_total",
        "Stored local OCR resource events by safe event category.",
        "counter",
    )
    for event, key in (
        ("attempt", "attempt_count"),
        ("timeout", "timeout_count"),
        ("budget_exhausted", "budget_exhausted_unit_count"),
        ("checkpoint_batch", "durable_checkpoint_batch_count"),
    ):
        sample(
            "spip_analysis_ocr_resource_events_total",
            ocr_resource.get(key),
            {"event": event},
        )
    family(
        "spip_analysis_ocr_budget_exhaustions_total",
        "Stored local OCR unit budget exhaustion events by content-free reason code.",
        "counter",
    )
    for reason, total in sorted(
        (ocr_resource.get("budget_exhaustion_reason_counts") or {}).items()
    ):
        sample(
            "spip_analysis_ocr_budget_exhaustions_total",
            total,
            {"reason": reason},
        )
    family(
        "spip_analysis_ocr_document_elapsed_seconds_total",
        "Cumulative wall-clock time recorded by local OCR document runs.",
        "counter",
    )
    sample(
        "spip_analysis_ocr_document_elapsed_seconds_total",
        float(ocr_resource.get("document_elapsed_ms") or 0) / 1000,
    )

    family(
        "spip_compute_routing_decisions",
        "Current stored compute-routing decisions by phase and selection outcome.",
    )
    family(
        "spip_compute_routing_average_score",
        "Average non-probabilistic compute-routing complexity and risk scores.",
    )
    for phase, values in sorted((metrics.get("compute_routing") or {}).items()):
        total = int(values.get("total") or 0)
        selected = int(values.get("selected") or 0)
        sample(
            "spip_compute_routing_decisions",
            selected,
            {"phase": phase, "selected": "true"},
        )
        sample(
            "spip_compute_routing_decisions",
            max(0, total - selected),
            {"phase": phase, "selected": "false"},
        )
        sample(
            "spip_compute_routing_average_score",
            values.get("average_complexity_score"),
            {"phase": phase, "score": "complexity"},
        )
        sample(
            "spip_compute_routing_average_score",
            values.get("average_risk_score"),
            {"phase": phase, "score": "risk"},
        )

    family("spip_analysis_verifications_total", "Verification results.", "counter")
    verification = metrics.get("verification") or {}
    sample("spip_analysis_verifications_total", verification.get("total"), {"result": "total"})
    sample("spip_analysis_verifications_total", verification.get("rejected"), {"result": "rejected"})

    human_review = metrics.get("human_review") or {}
    review_total = max(0, int(_number(human_review.get("total"))))
    review_corrected = max(0, int(_number(human_review.get("corrected"))))
    review_rejected = max(0, int(_number(human_review.get("rejected"))))
    review_approved = max(0, review_total - review_corrected - review_rejected)
    family(
        "spip_analysis_human_reviews_total",
        "Human review decisions by content-free outcome.",
        "counter",
    )
    for outcome, total in (
        ("approved", review_approved),
        ("corrected", review_corrected),
        ("rejected", review_rejected),
    ):
        sample("spip_analysis_human_reviews_total", total, {"outcome": outcome})
    family(
        "spip_analysis_human_review_override_ratio",
        "Fraction of human review decisions that corrected or rejected the proposed mapping.",
    )
    sample(
        "spip_analysis_human_review_override_ratio",
        (review_corrected + review_rejected) / review_total if review_total else 0,
    )

    family("spip_analysis_security_findings_total", "Security findings by severity.", "counter")
    for severity, total in sorted((metrics.get("security_findings_by_severity") or {}).items()):
        sample("spip_analysis_security_findings_total", total, {"severity": severity})

    controlled_uploads = metrics.get("controlled_uploads_by_status") or {}
    family(
        "spip_analysis_controlled_uploads_total",
        "Terminal controlled upload attempts by status.",
        "counter",
    )
    for upload_status, total in sorted(controlled_uploads.items()):
        if upload_status != "uploading":
            sample(
                "spip_analysis_controlled_uploads_total",
                total,
                {"status": upload_status},
            )
    family(
        "spip_analysis_controlled_upload_reservations",
        "Current controlled upload reservations awaiting a terminal result.",
    )
    sample(
        "spip_analysis_controlled_upload_reservations",
        controlled_uploads.get("uploading"),
    )
    family(
        "spip_analysis_controlled_upload_reservations_stale",
        "Controlled upload reservations older than ten minutes.",
    )
    sample(
        "spip_analysis_controlled_upload_reservations_stale",
        metrics.get("stale_controlled_upload_reservation_count"),
    )
    family(
        "spip_analysis_controlled_upload_ambiguities",
        "Controlled upload ambiguities by two-person reconciliation state.",
    )
    sample(
        "spip_analysis_controlled_upload_ambiguities",
        metrics.get("resolved_controlled_upload_ambiguity_count"),
        {"resolution": "resolved"},
    )
    sample(
        "spip_analysis_controlled_upload_ambiguities",
        metrics.get("unresolved_controlled_upload_ambiguity_count"),
        {"resolution": "unresolved"},
    )

    family("spip_legacy_pipeline_calls_total", "Content-free legacy V1 pipeline calls by kind and source.", "counter")
    for key, total in sorted((metrics.get("legacy_pipeline_calls_by_kind") or {}).items()):
        usage_kind, _, source = str(key).partition(":")
        sample(
            "spip_legacy_pipeline_calls_total",
            total,
            {"usage_kind": usage_kind, "source": source or "unknown"},
        )

    retrieval_feedback = metrics.get("retrieval_feedback") or {}
    family(
        "spip_retrieval_feedback_registry_active",
        "Whether the expert-gold retrieval feedback snapshot matches the active dataset.",
    )
    sample("spip_retrieval_feedback_registry_active", int(bool(retrieval_feedback.get("active"))))
    family(
        "spip_retrieval_feedback_terms",
        "One-way term fingerprints in the active expert-gold retrieval feedback registry.",
    )
    sample("spip_retrieval_feedback_terms", retrieval_feedback.get("term_count"))
    family(
        "spip_retrieval_feedback_source_labels",
        "Expert-gold labels contributing to the active retrieval feedback snapshot.",
    )
    sample("spip_retrieval_feedback_source_labels", retrieval_feedback.get("source_label_count"))

    family(
        "spip_evaluation_reports_total",
        "Immutable evaluation reports by release authority.",
        "counter",
    )
    for authority, total in sorted((metrics.get("evaluation_reports_by_authority") or {}).items()):
        sample("spip_evaluation_reports_total", total, {"authority": authority})

    storage_attestation = storage_encryption_attestation or {}
    family(
        "spip_storage_encryption_attestation_valid",
        "Whether signed storage encryption evidence is current and bound to runtime storage.",
    )
    sample(
        "spip_storage_encryption_attestation_valid",
        int(bool(storage_attestation.get("effective"))),
    )
    family(
        "spip_storage_encryption_validation_claimed",
        "Whether the deployment claims that platform storage encryption was validated.",
    )
    sample(
        "spip_storage_encryption_validation_claimed",
        int(bool((storage_attestation.get("checks") or {}).get("validation_flag_enabled"))),
    )
    family(
        "spip_storage_encryption_attestation_failed_checks",
        "Number of failed non-sensitive storage encryption evidence checks.",
    )
    sample(
        "spip_storage_encryption_attestation_failed_checks",
        len(storage_attestation.get("reasons") or []),
    )
    family(
        "spip_storage_encryption_attestation_seconds_until_expiry",
        "Seconds until the configured storage encryption attestation expires.",
    )
    sample(
        "spip_storage_encryption_attestation_seconds_until_expiry",
        storage_attestation.get("seconds_until_expiry"),
    )

    family("spip_analysis_alerts", "Currently active operational alerts.")
    active_alerts = alerting.get("alerts") or []
    if active_alerts:
        for alert in active_alerts:
            sample(
                "spip_analysis_alerts",
                1,
                {"code": alert.get("code") or "unknown", "severity": alert.get("severity") or "unknown"},
            )
    else:
        sample("spip_analysis_alerts", 0, {"code": "none", "severity": "none"})

    family("spip_analysis_workers", "Analysis worker state.")
    sample("spip_analysis_workers", worker.get("worker_count"), {"state": "configured"})
    sample("spip_analysis_workers", worker.get("alive_workers"), {"state": "alive"})
    sample(
        "spip_analysis_workers",
        int(bool(worker.get("stopping"))),
        {"state": "stopping"},
    )
    sample(
        "spip_analysis_workers",
        int(bool(worker.get("draining"))),
        {"state": "draining"},
    )
    sample(
        "spip_analysis_workers",
        int(bool(worker.get("accepting_jobs"))),
        {"state": "accepting_jobs"},
    )
    family(
        "spip_analysis_worker_drain_seconds",
        "Current elapsed graceful-shutdown drain time; zero outside draining state.",
    )
    sample(
        "spip_analysis_worker_drain_seconds",
        worker.get("draining_seconds"),
    )
    sample(
        "spip_analysis_workers",
        int(bool(worker.get("started"))),
        {"state": "started", "queue_backend": worker.get("queue_backend") or "unknown"},
    )
    family("spip_analysis_expected_replicas", "Configured application replica count.")
    sample("spip_analysis_expected_replicas", worker.get("expected_replicas") or 1)
    queue_adapter = worker.get("queue_adapter") or {}
    family(
        "spip_analysis_multi_instance_ready",
        "Whether queue coordination and canonical persistence are safe for multiple replicas.",
    )
    sample(
        "spip_analysis_multi_instance_ready",
        int(bool(worker.get("multi_instance_supported"))),
        {
            "queue_backend": worker.get("queue_backend") or "unknown",
            "adapter_known": str(bool(queue_adapter.get("adapter_known"))).lower(),
        },
    )
    family("spip_analysis_worker_leader_lease", "Single-leader queue lease state.")
    sample(
        "spip_analysis_worker_leader_lease",
        int(bool(worker.get("leader_lease_active"))),
        {
            "queue_backend": worker.get("queue_backend") or "unknown",
            "enforced": str(bool(worker.get("single_leader_enforced"))).lower(),
        },
    )

    return "\n".join(lines) + "\n"


def _finite_number(value: object) -> str:
    number = _number(value)
    return str(int(number)) if number.is_integer() else format(number, ".12g")


def _number(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return number


def _label_name(value: object) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", str(value or "label"))
    return normalized if not normalized[:1].isdigit() else "label_" + normalized


def _escape_label(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
