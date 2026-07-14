from __future__ import annotations

import hashlib

from app.analysis import PIPELINE_VERSION


ROLLOUT_STAGES = ("development", "shadow", "pilot", "canary", "general")


class RolloutGuardEngine:
    name = "rollout_guard"
    version = PIPELINE_VERSION

    def evaluate(
        self,
        *,
        requested_stage: str,
        canary_percentage: int,
        stable_release_cycles: int,
        promotion: dict,
    ) -> dict:
        stage = str(requested_stage or "development").strip().lower()
        reasons: list[str] = []
        if stage not in ROLLOUT_STAGES:
            reasons.append(f"Rollout stage '{stage}' tidak dikenal; development dipakai.")
            stage = "development"
        percentage = max(0, min(100, int(canary_percentage or 0)))
        shadow_ready = bool((promotion.get("shadow") or {}).get("ready"))
        canary_ready = bool((promotion.get("canary") or {}).get("ready"))
        general_ready = bool((promotion.get("general_release") or {}).get("ready"))
        if stage in {"shadow", "pilot", "canary", "general"} and not shadow_ready:
            reasons.append("Expert quality gate belum membuka shadow/pilot.")
        if stage in {"canary", "general"} and not canary_ready:
            reasons.append("Rule, security, atau expert quality gate belum membuka canary.")
        if stage == "canary" and percentage <= 0:
            reasons.append("Canary percentage masih 0%.")
        if stage == "general" and not general_ready:
            reasons.append("General-release gate belum disahkan.")
        if stage == "general" and int(stable_release_cycles or 0) < 2:
            reasons.append("Belum ada dua release cycle stabil.")
        return {
            "engine_name": self.name,
            "engine_version": self.version,
            "requested_stage": stage,
            "effective_stage": stage if not reasons else "development",
            "canary_percentage": percentage,
            "stable_release_cycles": max(0, int(stable_release_cycles or 0)),
            "ready": not reasons,
            "reasons": reasons,
        }

    @staticmethod
    def assigned_to_canary(document_sha256: str, percentage: int) -> bool:
        safe_percentage = max(0, min(100, int(percentage or 0)))
        if safe_percentage == 0:
            return False
        bucket = int(hashlib.sha256(document_sha256.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < safe_percentage
