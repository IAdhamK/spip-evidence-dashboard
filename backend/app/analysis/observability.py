from __future__ import annotations

from app.analysis import PIPELINE_VERSION


class OperationalAlertEngine:
    name = "operational_alerts"
    version = PIPELINE_VERSION

    def __init__(self, *, cost_alert_usd_per_hour: float = 10.0):
        self.cost_alert_usd_per_hour = max(0.0, float(cost_alert_usd_per_hour))

    def evaluate(self, metrics: dict) -> dict:
        alerts = []
        queue_depth = int((metrics.get("queue_by_status") or {}).get("queued") or 0)
        if queue_depth > 20:
            alerts.append(self._alert("warning", "queue_depth", f"Queue depth {queue_depth} melebihi 20."))

        active_recovery_loops = int(
            (metrics.get("job_recovery") or {}).get("active_recovery_loop_count") or 0
        )
        if active_recovery_loops:
            alerts.append(self._alert(
                "warning",
                "job_recovery_loop",
                f"{active_recovery_loops} job aktif telah mencapai claim lease ketiga.",
            ))

        engines = metrics.get("engines") or {}
        parser_total = sum(
            int(count) for key, count in engines.items() if key.startswith("native_parsing:")
        )
        parser_failed = int(engines.get("native_parsing:failed") or 0)
        parser_failure_rate = parser_failed / parser_total if parser_total else 0.0
        if parser_failure_rate > 0.02:
            alerts.append(
                self._alert(
                    "critical",
                    "parser_failure_spike",
                    f"Parser failure rate {parser_failure_rate:.1%} melebihi 2%.",
                )
            )

        security = metrics.get("security_findings_by_severity") or {}
        severe_security = int(security.get("high") or 0) + int(security.get("critical") or 0)
        if severe_security:
            alerts.append(
                self._alert(
                    "critical",
                    "security_findings",
                    f"Terdapat {severe_security} high/critical security finding.",
                )
            )

        failed_model_calls = sum(
            int(count)
            for key, count in engines.items()
            if key.endswith(":failed")
            and key.split(":", 1)[0] in {
                "structured_fact_extraction", "visual_ocr", "model_second_pass_verification",
            }
        )
        if failed_model_calls:
            alerts.append(
                self._alert(
                    "warning",
                    "model_failures",
                    f"Terdapat {failed_model_calls} kegagalan model/schema.",
                )
            )

        upload_blocks = sum(
            int(total)
            for upload_status, total in (
                metrics.get("controlled_uploads_by_status") or {}
            ).items()
            if str(upload_status).startswith("blocked")
        )
        if upload_blocks >= 3:
            alerts.append(
                self._alert(
                    "warning",
                    "upload_failure_spike",
                    f"Controlled upload diblokir/gagal {upload_blocks} kali.",
                )
            )
        stale_upload_reservations = int(
            metrics.get("stale_controlled_upload_reservation_count") or 0
        )
        if stale_upload_reservations:
            alerts.append(
                self._alert(
                    "critical",
                    "controlled_upload_reservation_stale",
                    (
                        f"{stale_upload_reservations} reservation controlled upload "
                        "belum terminal setelah sepuluh menit."
                    ),
                )
            )
        unresolved_upload_ambiguities = int(
            metrics.get("unresolved_controlled_upload_ambiguity_count") or 0
        )
        if unresolved_upload_ambiguities:
            alerts.append(
                self._alert(
                    "critical",
                    "controlled_upload_ambiguity_unresolved",
                    (
                        f"{unresolved_upload_ambiguities} hasil controlled upload ambigu "
                        "belum mempunyai keputusan cocok dari dua reviewer."
                    ),
                )
            )
        average_duration = float(metrics.get("average_duration_seconds") or 0)
        if average_duration > 300:
            alerts.append(
                self._alert(
                    "warning",
                    "latency_anomaly",
                    f"Rata-rata durasi run {average_duration:.1f} detik melebihi 300 detik.",
                )
            )
        estimated_cost_last_hour = max(
            0.0, float(metrics.get("estimated_cost_usd_last_hour") or 0)
        )
        if estimated_cost_last_hour > self.cost_alert_usd_per_hour:
            alerts.append(
                self._alert(
                    "warning",
                    "cost_anomaly",
                    (
                        f"Estimasi biaya satu jam USD {estimated_cost_last_hour:.4f} "
                        f"melebihi budget USD {self.cost_alert_usd_per_hour:.4f}."
                    ),
                )
            )
        human_review_override_ratio = max(
            0.0,
            min(
                1.0,
                float(
                    (metrics.get("human_review") or {}).get("override_ratio") or 0
                ),
            ),
        )
        status = "critical" if any(item["severity"] == "critical" for item in alerts) else (
            "warning" if alerts else "ok"
        )
        return {
            "engine_name": self.name,
            "engine_version": self.version,
            "status": status,
            "alerts": alerts,
            "derived": {
                "queue_depth": queue_depth,
                "active_recovery_loops": active_recovery_loops,
                "parser_failure_rate": round(parser_failure_rate, 4),
                "severe_security_findings": severe_security,
                "failed_model_calls": failed_model_calls,
                "stale_upload_reservations": stale_upload_reservations,
                "unresolved_upload_ambiguities": unresolved_upload_ambiguities,
                "estimated_cost_usd_last_hour": round(estimated_cost_last_hour, 6),
                "cost_alert_usd_per_hour": round(self.cost_alert_usd_per_hour, 6),
                "human_review_override_ratio": round(human_review_override_ratio, 4),
            },
        }

    @staticmethod
    def _alert(severity: str, code: str, message: str) -> dict:
        return {"severity": severity, "code": code, "message": message}
