from __future__ import annotations

from time import perf_counter

from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.provider import MappingReasoningProvider
from app.analysis.routing import ROUTING_POLICY_VERSION, mapping_key


MAX_RERANK_CANDIDATES = 3


class ConstrainedMappingReasoningEngine:
    name = "constrained_mapping_reasoning"
    version = ROUTING_POLICY_VERSION

    def __init__(self, provider: MappingReasoningProvider):
        self.provider = provider

    def run(
        self,
        identity: DocumentIdentity,
        mappings: list[dict],
        facts: list[dict],
        *,
        candidate_keys: list[str],
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        targeted = set(candidate_keys[:MAX_RERANK_CANDIDATES])
        facts_by_id = {
            int(fact["id"]): fact
            for fact in facts
            if fact.get("id") is not None
        }
        payload = []
        for item in mappings:
            key = mapping_key(item)
            if key not in targeted:
                continue
            evidence = []
            for fact_id in item.get("supporting_fact_ids") or []:
                fact = facts_by_id.get(int(fact_id))
                if not fact:
                    continue
                evidence.append({
                    "fact_type": fact.get("fact_type"),
                    "claim": str(fact.get("claim") or "")[:400],
                    "period": fact.get("period"),
                    "organization": fact.get("organization"),
                    "source_quotes": [
                        str(source.get("source_quote") or "")[:300]
                        for source in (fact.get("sources") or [])[:2]
                    ],
                })
            payload.append({
                "mapping_key": key,
                "official_parameter": {
                    "kk_id": item.get("kk_id"),
                    "kode": item.get("kode"),
                    "detail_kode": item.get("detail_kode"),
                    "uraian": str(item.get("uraian") or "")[:600],
                },
                "mapping_score": float(item.get("mapping_score") or 0),
                "evidence": evidence[:3],
            })
        known_keys = {item["mapping_key"] for item in payload}
        response_by_key = {}
        warnings: list[str] = []
        provider_failed = False
        provider_warning_count = 0
        invalid_item_count = 0
        usage_metrics: dict[str, int | float] = {}
        try:
            response = self.provider.review_mappings(payload)
            usage_metrics = response.usage_metrics
            provider_warning_count = len(response.warnings)
            if provider_warning_count:
                warnings.append(
                    f"Provider mengembalikan {provider_warning_count} warning; isi tidak dipersistenkan."
                )
            for item in response.items:
                if item.mapping_key not in known_keys or item.mapping_key in response_by_key:
                    invalid_item_count += 1
                    continue
                response_by_key[item.mapping_key] = item
        except Exception:
            provider_failed = True
            warnings.append(
                "Constrained mapping reasoning gagal; kandidat ambigu tetap needs_review."
            )

        advisory_ranks: dict[str, int] = {}
        seen_declared_ranks: set[int] = set()
        ranked_advisories = []
        for key, advisory in response_by_key.items():
            declared_rank = advisory.relevance_rank
            if declared_rank is None:
                continue
            if declared_rank in seen_declared_ranks:
                invalid_item_count += 1
                continue
            seen_declared_ranks.add(declared_rank)
            ranked_advisories.append((declared_rank, key))
        for normalized_rank, (_, key) in enumerate(sorted(ranked_advisories), start=1):
            advisory_ranks[key] = normalized_rank

        demoted_count = 0
        plausible_count = 0
        missing_count = 0
        updated = []
        for item in mappings:
            key = mapping_key(item)
            if key not in known_keys:
                updated.append(item)
                continue
            advisory = response_by_key.get(key)
            reason_code = None
            if not advisory:
                missing_count += 1
                reason_code = "model_mapping_advisory_missing"
            elif advisory.status == "plausible":
                plausible_count += 1
            else:
                reason_code = f"model_mapping_advisory_{advisory.status}"
            if reason_code:
                demoted_count += int(item.get("status") != "needs_review")
                updated.append({
                    **item,
                    "rag_rank": advisory_ranks.get(key, item.get("rag_rank")),
                    "rag_relevance": (
                        advisory.relevance_score
                        if advisory and advisory.relevance_score is not None
                        else item.get("rag_relevance")
                    ),
                    "rag_method": (
                        "deepseek_v4_pro_constrained_rerank_v1"
                        if advisory else item.get("rag_method")
                    ),
                    "status": "needs_review",
                    "reasons": [
                        *(item.get("reasons") or []),
                        "Constrained model advisory menahan kandidat untuk review manusia; skor dan grade tidak diubah.",
                    ],
                    "missing_evidence": [
                        *(item.get("missing_evidence") or []),
                        reason_code,
                    ],
                })
            else:
                updated.append({
                    **item,
                    "rag_rank": advisory_ranks.get(key, item.get("rag_rank")),
                    "rag_relevance": (
                        advisory.relevance_score
                        if advisory.relevance_score is not None
                        else item.get("rag_relevance")
                    ),
                    "rag_method": "deepseek_v4_pro_constrained_rerank_v1",
                    "reasons": [
                        *(item.get("reasons") or []),
                        "Constrained model advisory menilai kandidat plausible tanpa otoritas promosi atau grade.",
                    ],
                })
        updated.sort(key=lambda item: (
            int(item.get("rag_rank") or 10_000),
            -float(item.get("mapping_score") or 0),
            str(item.get("kk_id") or ""),
            str(item.get("detail_kode") or ""),
        ))
        updated = [
            {**item, "rag_rank": rank}
            for rank, item in enumerate(updated, start=1)
        ]
        processed = len(response_by_key)
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=(
                EngineStatus.FAILED
                if provider_failed
                else EngineStatus.PARTIAL
                if missing_count or invalid_item_count
                else EngineStatus.COMPLETED
            ),
            input_checksum=identity.sha256,
            input_refs=[f"mapping:{item['mapping_key']}" for item in payload],
            output_refs=[f"mapping-advisory:{key}" for key in sorted(response_by_key)],
            coverage={
                "required": len(payload),
                "processed": processed,
                "failed": int(provider_failed),
            },
            warnings=warnings[:20],
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "targeted_count": len(payload),
                "processed_count": processed,
                "plausible_count": plausible_count,
                "demoted_count": demoted_count,
                "missing_count": missing_count,
                "invalid_item_count": invalid_item_count,
                "provider_warning_count": provider_warning_count,
                "reranked_count": len(advisory_ranks),
                **usage_metrics,
            },
            output={
                "targeted_count": len(payload),
                "processed_count": processed,
                "plausible_count": plausible_count,
                "demoted_count": demoted_count,
                "missing_count": missing_count,
                "invalid_item_count": invalid_item_count,
                "provider_warning_count": provider_warning_count,
                "reranked_count": len(advisory_ranks),
                "authority": "rerank_and_demotion_only_no_mapping_promotion_or_grade_authority",
                "findings_content_persisted": False,
            },
            error_message=(
                "Mapping reasoning provider failed."
                if provider_failed else None
            ),
        ).finish()
        return updated, result
