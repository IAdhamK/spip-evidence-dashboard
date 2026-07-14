from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
from time import perf_counter

from app.analysis import PIPELINE_VERSION
from app.analysis.advanced_rag import (
    ADVANCED_RAG_VERSION,
    expand_domain_tokens,
    reciprocal_rank_fusion,
    sparse_semantic_vector,
    vector_cosine,
)
from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.document_family_registry import parameter_key as registry_parameter_key


STOPWORDS = {
    "ada", "atau", "atas", "bagi", "bahwa", "dalam", "dan", "dapat", "dengan",
    "dari", "di", "hal", "ini", "itu", "ke", "kepada", "nomor", "oleh", "pada", "para",
    "telah", "untuk", "yang",
}
MIN_RETRIEVAL_SCORE = 0.12
EVIDENCE_STAGE_TYPES = ("policy", "socialization", "implementation", "evaluation", "improvement")


class ParameterRetrievalEngine:
    name = "parameter_retrieval"
    version = PIPELINE_VERSION

    def __init__(self, *, advanced_rag_enabled: bool = False):
        self.advanced_rag_enabled = bool(advanced_rag_enabled)

    def run(
        self,
        identity: DocumentIdentity,
        facts: list[dict],
        parameters: list[dict],
        *,
        limit: int = 10,
        feedback_terms: list[dict] | None = None,
        query_expansions: list[str] | None = None,
        catalog_shortlist: list[dict] | None = None,
        parameter_scope: dict | None = None,
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        unscoped_parameter_count = len(parameters)
        scope = parameter_scope or {}
        scope_primary = set(scope.get("primary_parameter_keys") or [])
        scope_secondary = set(scope.get("secondary_parameter_keys") or [])
        scope_allowed = scope_primary | scope_secondary
        scope_exploratory = bool(scope.get("exploratory"))
        if parameter_scope is not None and not scope_exploratory:
            parameters = [
                item for item in parameters if registry_parameter_key(item) in scope_allowed
            ]
        file_name_text = re.sub(r"[_\-.]+", " ", str(identity.file_name or ""))
        query_text = " ".join([
            file_name_text,
            *(str(fact.get("claim") or "") for fact in facts),
        ])
        base_query_tokens = tokenize(query_text)
        parameter_sequences = {
            int(item["id"]): parameter_corpus_token_sequence(item) for item in parameters
        }
        parameter_tokens = {
            parameter_id: set(tokens) for parameter_id, tokens in parameter_sequences.items()
        }
        catalog_vocabulary = set().union(*parameter_tokens.values()) if parameter_tokens else set()
        accepted_expansion_tokens: set[str] = set()
        rejected_expansion_token_count = 0
        for token in tokenize(" ".join(query_expansions or [])):
            semantic_forms = expand_domain_tokens({token})
            if token in catalog_vocabulary or semantic_forms & catalog_vocabulary:
                accepted_expansion_tokens.add(token)
            else:
                rejected_expansion_token_count += 1
        query_tokens = base_query_tokens | accepted_expansion_tokens
        query_sequence = tokenize_sequence(query_text) + sorted(accepted_expansion_tokens)
        semantic_query_tokens = expand_domain_tokens(query_tokens)
        semantic_query_vector = sparse_semantic_vector(query_sequence)
        query_tokens_by_sha256 = {
            hashlib.sha256(token.encode("utf-8")).hexdigest(): token
            for token in query_tokens
        }
        query_frequencies = Counter(query_sequence)
        kk_context_scores = detect_kk_context(query_text)
        catalog_by_parameter = {
            (
                str(item.get("kk_id") or ""),
                str(item.get("kode") or ""),
                str(item.get("detail_kode") or ""),
            ): item
            for item in (catalog_shortlist or [])
            if item.get("kk_id") and item.get("kode") and item.get("detail_kode")
        }
        local_document_role = str(scope.get("evidence_role") or "") or infer_document_role(identity, facts)
        feedback_by_parameter: dict[tuple[str, str, str], list[dict]] = {}
        feedback_registry_sha256 = sorted({
            str(item.get("registry_sha256") or "")
            for item in (feedback_terms or [])
            if re.fullmatch(r"[a-f0-9]{64}", str(item.get("registry_sha256") or ""))
        })
        for item in feedback_terms or []:
            term_sha256 = str(item.get("term_sha256") or "").strip().lower()
            parameter_key = (
                str(item.get("kk_id") or ""),
                str(item.get("kode") or ""),
                str(item.get("detail_kode") or ""),
            )
            precision = float(item.get("precision") or 0)
            support = int(item.get("document_support") or 0)
            if (
                all(parameter_key)
                and re.fullmatch(r"[a-f0-9]{64}", term_sha256)
                and precision >= 0.8
                and support >= 2
            ):
                feedback_by_parameter.setdefault(parameter_key, []).append({
                    **item,
                    "term_sha256": term_sha256,
                })
        document_frequency = Counter(
            token
            for tokens in parameter_tokens.values()
            for token in set(tokens)
        )
        total_documents = max(1, len(parameters))
        average_document_length = (
            sum(len(tokens) for tokens in parameter_sequences.values()) / total_documents
        ) or 1.0
        raw_candidates = []
        for parameter in parameters:
            parameter_id = int(parameter["id"])
            parameter_key = (
                str(parameter.get("kk_id") or ""),
                str(parameter.get("kode") or ""),
                str(parameter.get("detail_kode") or ""),
            )
            tokens = parameter_tokens[parameter_id]
            overlap = sorted(query_tokens & tokens)
            semantic_tokens = expand_domain_tokens(tokens)
            semantic_overlap = sorted(semantic_query_tokens & semantic_tokens)
            parameter_feedback = feedback_by_parameter.get(parameter_key, [])
            catalog_match = catalog_by_parameter.get(parameter_key)
            serialized_parameter_key = "|".join(parameter_key)
            scope_tier = (
                "primary" if serialized_parameter_key in scope_primary
                else "secondary" if serialized_parameter_key in scope_secondary
                else "exploratory"
            )
            family_scope_bonus = (
                0.34 if scope_tier == "primary"
                else 0.16 if scope_tier == "secondary"
                else 0.0
            )
            feedback_matches = [
                {
                    **item,
                    "matched_query_term": query_tokens_by_sha256[item["term_sha256"]],
                }
                for item in parameter_feedback
                if item["term_sha256"] in query_tokens_by_sha256
            ]
            if not catalog_match and not overlap and not feedback_matches and not family_scope_bonus and not (
                self.advanced_rag_enabled and semantic_overlap
            ):
                continue
            idf = {
                token: math.log((total_documents + 1) / (document_frequency.get(token, 0) + 1)) + 1
                for token in query_tokens | tokens
            }
            weighted_overlap = sum(idf[token] ** 2 for token in overlap)
            query_weight = math.sqrt(sum(idf[token] ** 2 for token in query_tokens)) or 1
            document_weight = math.sqrt(sum(idf[token] ** 2 for token in tokens)) or 1
            cosine_score = weighted_overlap / (query_weight * document_weight)
            frequencies = Counter(parameter_sequences[int(parameter["id"])])
            document_length = max(1, sum(frequencies.values()))
            bm25_score = 0.0
            for token, query_frequency in query_frequencies.items():
                term_frequency = frequencies.get(token, 0)
                if not term_frequency:
                    continue
                bm25_idf = math.log(
                    1 + (total_documents - document_frequency.get(token, 0) + 0.5)
                    / (document_frequency.get(token, 0) + 0.5)
                )
                denominator = term_frequency + 1.2 * (
                    1 - 0.75 + 0.75 * document_length / average_document_length
                )
                bm25_score += bm25_idf * (
                    term_frequency * 2.2 / denominator
                ) * min(2, query_frequency)
            normalized_bm25 = 1 - math.exp(-bm25_score / 8.0)
            semantic_score = (
                vector_cosine(
                    semantic_query_vector,
                    sparse_semantic_vector(parameter_sequences[parameter_id]),
                )
                if self.advanced_rag_enabled
                else 0.0
            )
            phrase_bonus = _phrase_bonus(facts, parameter)
            uraian_overlap = query_tokens & tokenize(str(parameter.get("uraian") or ""))
            evidence_hint_overlap = query_tokens & tokenize(str(parameter.get("evidence_hint") or ""))
            hierarchy_overlap = query_tokens & tokenize(" ".join([
                str(parameter.get("unsur") or ""),
                str(parameter.get("matrix_subunsur_name") or ""),
                str(parameter.get("subunsur_name") or ""),
            ]))
            field_bonus = (
                min(0.18, len(uraian_overlap) * 0.035)
                + min(0.24, len(evidence_hint_overlap) * 0.06)
                + min(0.16, len(hierarchy_overlap) * 0.04)
            )
            domain_intent_adjustment = _domain_intent_adjustment(query_text, parameter)
            kk_bonus = kk_context_scores.get(str(parameter.get("kk_id") or ""), 0.0)
            feedback_bonus = min(
                0.18,
                sum(
                    0.08 + (float(item["precision"]) * 0.06)
                    for item in feedback_matches
                ),
            )
            learned_tokens = {
                item["matched_query_term"] for item in feedback_matches
            }
            raw_candidates.append(
                {
                    "parameter_id": parameter_id,
                    "kk_id": parameter["kk_id"],
                    "kode": parameter["kode"],
                    "detail_kode": parameter["detail_kode"],
                    "subunsur_name": parameter.get("subunsur_name"),
                    "matrix_subunsur_name": parameter.get("matrix_subunsur_name"),
                    "unsur": parameter.get("unsur"),
                    "kk_title": parameter.get("kk_title"),
                    "evidence_hint": parameter.get("evidence_hint"),
                    "uraian": parameter.get("uraian"),
                    "cara_pengujian": parameter.get("cara_pengujian"),
                    "grades": parameter.get("grades") or [],
                    "cosine_score": cosine_score,
                    "bm25_score": normalized_bm25,
                    "semantic_score": semantic_score,
                    "bonus_score": (
                        phrase_bonus
                        + field_bonus
                        + kk_bonus
                        + feedback_bonus
                        + domain_intent_adjustment
                        + family_scope_bonus
                    ),
                    "matched_terms": overlap[:20],
                    "matched_semantic_terms": semantic_overlap[:20],
                    "matched_feedback_terms": sorted(
                        item["matched_query_term"] for item in feedback_matches
                    )[:20],
                    "feedback_bonus": round(feedback_bonus, 4),
                    "domain_intent_adjustment": round(domain_intent_adjustment, 4),
                    "catalog_relevance": round(
                        float((catalog_match or {}).get("catalog_relevance") or 0), 4
                    ),
                    "document_role": conservative_document_role(
                        local_document_role,
                        str((catalog_match or {}).get("document_role") or ""),
                    ),
                    "family_scope_tier": scope_tier,
                    "family_scope_bonus": family_scope_bonus,
                    "corpus_tokens": sorted(
                        (semantic_tokens if self.advanced_rag_enabled else tokens)
                        | learned_tokens
                    ),
                }
            )
        rrf_scores = reciprocal_rank_fusion({
            "bm25": {item["parameter_id"]: item["bm25_score"] for item in raw_candidates},
            "cosine_idf": {item["parameter_id"]: item["cosine_score"] for item in raw_candidates},
            "semantic_vector": {
                item["parameter_id"]: item["semantic_score"] for item in raw_candidates
            },
        }) if self.advanced_rag_enabled else {}
        ranked = []
        for item in raw_candidates:
            rrf_score = rrf_scores.get(item["parameter_id"], 0.0)
            if self.advanced_rag_enabled:
                local_score = min(
                    1.0,
                    (item["bm25_score"] * 0.30)
                    + (item["cosine_score"] * 0.25)
                    + (item["semantic_score"] * 0.30)
                    + (rrf_score * 0.15)
                    + item["bonus_score"],
                )
                retrieval_components = {
                    "bm25": round(item["bm25_score"], 4),
                    "cosine_idf": round(item["cosine_score"], 4),
                    "semantic_vector": round(item["semantic_score"], 4),
                    "rrf": round(rrf_score, 4),
                }
            else:
                local_score = min(
                    1.0,
                    (item["cosine_score"] * 0.45)
                    + (item["bm25_score"] * 0.55)
                    + item["bonus_score"],
                )
                retrieval_components = {
                    "bm25": round(item["bm25_score"], 4),
                    "cosine_idf": round(item["cosine_score"], 4),
                }
            if catalog_by_parameter:
                score = min(
                    1.0,
                    (local_score * 0.65) + (item["catalog_relevance"] * 0.35),
                )
                retrieval_components["catalog_search"] = item["catalog_relevance"]
            else:
                score = local_score
            ranked.append({
                **{
                    key: value for key, value in item.items()
                    if key not in {"cosine_score", "bm25_score", "semantic_score", "bonus_score"}
                },
                "retrieval_score": round(score, 4),
                "retrieval_components": retrieval_components,
            })
        ranked.sort(key=lambda item: (-item["retrieval_score"], item["kk_id"], item["detail_kode"]))
        ranked = [item for item in ranked if item["retrieval_score"] >= MIN_RETRIEVAL_SCORE]
        ranked = _select_diverse_candidates(ranked, limit=max(1, limit))
        warnings = [] if ranked else ["Retrieval Engine tidak menemukan parameter yang cukup relevan; sistem abstain."]
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED if ranked else EngineStatus.PARTIAL,
            input_checksum=identity.sha256,
            input_refs=[f"fact:{fact.get('fact_key')}" for fact in facts],
            output_refs=[f"parameter:{item['parameter_id']}" for item in ranked],
            coverage={"required": len(parameters), "processed": len(parameters), "failed": 0},
            warnings=warnings,
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "parameter_pool_count": len(parameters),
                "unscoped_parameter_pool_count": unscoped_parameter_count,
                "incompatible_parameter_count": unscoped_parameter_count - len(parameters),
                "candidate_count": len(ranked),
                "active_feedback_term_count": sum(
                    len(items) for items in feedback_by_parameter.values()
                ),
                "feedback_assisted_candidate_count": sum(
                    bool(item.get("matched_feedback_terms")) for item in ranked
                ),
                "accepted_model_expansion_token_count": len(accepted_expansion_tokens),
                "rejected_model_expansion_token_count": rejected_expansion_token_count,
                "model_catalog_shortlist_count": len(catalog_by_parameter),
                "model_catalog_assisted_candidate_count": sum(
                    bool(item.get("catalog_relevance")) for item in ranked
                ),
            },
            output={
                "candidate_count": len(ranked),
                "query_token_count": len(query_tokens),
                "parameter_scope": (
                    scope.get("registry_version")
                    if parameter_scope is not None
                    else "all_kk_subunsur_and_parameters_without_grade"
                ),
                "document_family": scope.get("family") if parameter_scope is not None else None,
                "scope_exploratory": scope_exploratory if parameter_scope is not None else True,
                "primary_parameter_keys": sorted(scope_primary),
                "secondary_parameter_keys": sorted(scope_secondary),
                "kk_context_scores": kk_context_scores,
                "minimum_retrieval_score": MIN_RETRIEVAL_SCORE,
                "retrieval_method": (
                    "advanced_hybrid_bm25_cosine_semantic_rrf_feedback_v1"
                    if self.advanced_rag_enabled
                    else "hybrid_bm25_cosine_idf_feedback_v2"
                ),
                "advanced_rag_version": ADVANCED_RAG_VERSION if self.advanced_rag_enabled else None,
                "model_query_expansion_used": bool(accepted_expansion_tokens),
                "model_expansion_authority": "retrieval_only" if query_expansions else None,
                "model_catalog_search_used": bool(catalog_by_parameter),
                "model_catalog_authority": (
                    "candidate_recall_and_ranking_only" if catalog_by_parameter else None
                ),
                "document_role": local_document_role,
                "feedback_authority": "active_two_person_expert_gold_only",
                "feedback_registry_sha256": feedback_registry_sha256[0]
                if len(feedback_registry_sha256) == 1 else None,
            },
        ).finish()
        return ranked, result


class SPIPMappingEngine:
    name = "spip_mapping"
    version = PIPELINE_VERSION

    def run(
        self,
        identity: DocumentIdentity,
        facts: list[dict],
        retrieved: list[dict],
    ) -> tuple[list[dict], EngineResult]:
        started = perf_counter()
        mappings = []
        for local_rank, parameter in enumerate(retrieved, start=1):
            corpus_tokens = set(parameter.get("corpus_tokens") or [])
            supporting = []
            strongest_fact_score = 0.0
            for fact in facts:
                fact_tokens = tokenize(str(fact.get("claim") or ""))
                overlap = fact_tokens & corpus_tokens
                if not overlap:
                    continue
                score = len(overlap) / max(1, min(len(fact_tokens), len(corpus_tokens)))
                strongest_fact_score = max(strongest_fact_score, score)
                supporting.append(
                    {
                        "fact_id": fact.get("id"),
                        "fact_key": fact.get("fact_key"),
                        "fact_type": fact.get("fact_type"),
                        "evidence_role": fact.get("evidence_role"),
                        "overlap": sorted(overlap)[:12],
                        "score": round(score, 4),
                    }
                )
            supporting.sort(key=lambda item: -item["score"])
            selected_supporting, diversified_stages = _select_stage_diverse_supporting_facts(
                supporting,
                limit=12,
            )
            retrieval_score = float(parameter.get("retrieval_score") or 0)
            mapping_score = min(1.0, (retrieval_score * 0.7) + (strongest_fact_score * 0.3))
            support_ids = [
                int(item["fact_id"]) for item in selected_supporting if item.get("fact_id")
            ]
            reasons = []
            if parameter.get("matched_terms"):
                reasons.append(
                    f"Parameter-first retrieval cocok pada: {', '.join(parameter['matched_terms'])}."
                )
            if parameter.get("matched_feedback_terms"):
                reasons.append(
                    "Vocabulary feedback expert-gold membantu retrieval; grade tetap ditentukan rule engine."
                )
            if parameter.get("matched_semantic_terms"):
                reasons.append(
                    "Advanced RAG menemukan kesepadanan istilah administrasi/SPIP; hasil tetap harus didukung fakta bersumber."
                )
            if parameter.get("catalog_relevance"):
                reasons.append(
                    "DeepSeek V4 Pro menelusuri katalog KK, subunsur, dan parameter resmi; hasil tetap advisory tanpa otoritas Grade."
                )
            if diversified_stages:
                reasons.append(
                    "Bukti pendukung dilengkapi lintas tahap agar kebijakan, pelaksanaan, evaluasi, dan tindak lanjut tidak terputus."
                )
            mappings.append(
                {
                    "parameter_id": parameter["parameter_id"],
                    "kk_id": parameter["kk_id"],
                    "kode": parameter["kode"],
                    "detail_kode": parameter["detail_kode"],
                    "subunsur_name": parameter.get("subunsur_name"),
                    "matrix_subunsur_name": parameter.get("matrix_subunsur_name"),
                    "unsur": parameter.get("unsur"),
                    "kk_title": parameter.get("kk_title"),
                    "uraian": parameter.get("uraian"),
                    "grades": parameter.get("grades") or [],
                    "retrieval_score": round(retrieval_score, 4),
                    "mapping_score": round(mapping_score, 4),
                    "rag_rank": local_rank,
                    "rag_relevance": round(retrieval_score, 4),
                    "rag_method": (
                        "deepseek_v4_pro_global_catalog_and_constrained_rerank_v2"
                        if parameter.get("catalog_relevance")
                        else "advanced_rag_local_v2"
                        if parameter.get("retrieval_components", {}).get("semantic_vector") is not None
                        else "parameter_retrieval_v2"
                    ),
                    "document_role": parameter.get("document_role") or "context",
                    "status": "candidate" if support_ids else "needs_review",
                    "supporting_fact_ids": support_ids,
                    "supporting_facts": selected_supporting,
                    "supporting_stage_coverage": sorted({
                        str(item.get("fact_type"))
                        for item in selected_supporting
                        if item.get("fact_type") in EVIDENCE_STAGE_TYPES
                    }),
                    "reasons": reasons,
                    "missing_evidence": [] if support_ids else ["Belum ada fakta bersumber yang mendukung parameter."],
                }
            )
        mappings.sort(key=lambda item: (-item["mapping_score"], item["kk_id"], item["detail_kode"]))
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.COMPLETED if mappings else EngineStatus.PARTIAL,
            input_checksum=identity.sha256,
            input_refs=[
                *[f"fact:{fact.get('fact_key')}" for fact in facts],
                *[f"parameter:{item.get('parameter_id')}" for item in retrieved],
            ],
            output_refs=[f"mapping:{item['kk_id']}:{item['detail_kode']}" for item in mappings],
            coverage={"required": len(retrieved), "processed": len(retrieved), "failed": 0},
            warnings=[] if mappings else ["Mapping Engine tidak menghasilkan kandidat."],
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "mapping_count": len(mappings),
            },
            output={"mapping_count": len(mappings)},
        ).finish()
        return mappings, result


def _select_stage_diverse_supporting_facts(
    supporting: list[dict],
    *,
    limit: int,
) -> tuple[list[dict], list[str]]:
    """Keep the strongest evidence while preventing one stage from crowding out the chain."""
    if not supporting:
        return [], []
    selected = list(supporting[:limit])
    selected_ids = {item.get("fact_id") for item in selected}
    strongest = float(supporting[0].get("score") or 0)
    minimum_stage_score = max(0.20, strongest * 0.25)
    diversified: list[str] = []
    for stage in EVIDENCE_STAGE_TYPES:
        if any(item.get("fact_type") == stage for item in selected):
            continue
        candidate = next(
            (
                item for item in supporting
                if item.get("fact_type") == stage
                and item.get("evidence_role") != "contradictory"
                and float(item.get("score") or 0) >= minimum_stage_score
            ),
            None,
        )
        if not candidate or candidate.get("fact_id") in selected_ids:
            continue
        if len(selected) < limit:
            selected.append(candidate)
        else:
            stage_counts = Counter(str(item.get("fact_type") or "unknown") for item in selected)
            replacement_index = next(
                (
                    index for index in range(len(selected) - 1, -1, -1)
                    if selected[index].get("fact_type") not in EVIDENCE_STAGE_TYPES
                    or stage_counts[str(selected[index].get("fact_type") or "unknown")] > 1
                ),
                None,
            )
            if replacement_index is None:
                continue
            selected_ids.discard(selected[replacement_index].get("fact_id"))
            selected[replacement_index] = candidate
        selected_ids.add(candidate.get("fact_id"))
        diversified.append(stage)
    selected.sort(key=lambda item: (-float(item.get("score") or 0), int(item.get("fact_id") or 0)))
    return selected[:limit], diversified


def tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1 and token not in STOPWORDS
    }


def parameter_corpus_tokens(parameter: dict) -> set[str]:
    return set(parameter_corpus_token_sequence(parameter))


def parameter_corpus_token_sequence(parameter: dict) -> list[str]:
    text = " ".join(
        str(parameter.get(key) or "")
        for key in (
            "kk_id", "kk_title", "kode", "detail_kode", "matrix_subunsur_name",
            "subunsur_name", "unsur", "evidence_hint", "uraian", "cara_pengujian",
        )
    )
    return tokenize_sequence(text)


def tokenize_sequence(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


def _phrase_bonus(facts: list[dict], parameter: dict) -> float:
    combined = " ".join(str(fact.get("claim") or "") for fact in facts).lower()
    uraian = " ".join(str(parameter.get("uraian") or "").lower().split())
    if uraian and len(uraian) >= 12 and uraian in combined:
        return 0.25
    return 0.0


KK_CONTEXT_KEYWORDS = {
    "KK3.1": (
        "kesekretariatan", "sekretariat", "kinerja organisasi", "iku",
        "tujuan organisasi", "sasaran organisasi", "tata laksana", "sumber daya manusia",
    ),
    "KK3.2": (
        "keuangan", "anggaran", "dipa", "pagu", "realisasi anggaran", "spm", "sp2d",
        "laporan keuangan", "rekonsiliasi keuangan", "belanja",
    ),
    "KK3.3": (
        "aset", "bmn", "barang milik negara", "inventaris", "pemeliharaan aset",
        "pengelolaan aset", "kib", "stock opname",
    ),
    "KK3.4": (
        "ketaatan", "kepatuhan", "peraturan", "perundang-undangan", "regulasi",
        "hukum", "audit kepatuhan",
    ),
}


def detect_kk_context(value: str) -> dict[str, float]:
    lowered = " ".join(str(value or "").lower().split())
    raw_scores = {
        kk_id: sum(1 for keyword in keywords if keyword in lowered)
        for kk_id, keywords in KK_CONTEXT_KEYWORDS.items()
    }
    best = max(raw_scores.values(), default=0)
    if best <= 0:
        return {}
    return {
        kk_id: round(min(0.35, (score / best) * 0.35), 4)
        for kk_id, score in raw_scores.items()
        if score > 0
    }


def infer_document_role(identity: DocumentIdentity, facts: list[dict]) -> str:
    """Classify a document conservatively without allowing the model to promote evidence."""
    primary_stages = {"implementation", "evaluation", "improvement"}
    if any(
        fact.get("evidence_role") == "primary"
        and fact.get("fact_type") in primary_stages
        for fact in facts
    ):
        return "primary"
    normalized_name = " ".join(str(identity.file_name or "").lower().split())
    supporting_name_markers = (
        "nota dinas", "surat pengantar", "penyampaian", "undangan", "notulen",
        "disposisi", "memorandum",
    )
    if any(marker in normalized_name for marker in supporting_name_markers):
        return "supporting"
    fact_types = {str(fact.get("fact_type") or "unknown") for fact in facts}
    evidence_roles = {str(fact.get("evidence_role") or "context") for fact in facts}
    if fact_types & {"policy", "socialization"} or "supporting" in evidence_roles:
        return "supporting"
    if facts:
        return "context"
    return "not_evidence"


def conservative_document_role(local_role: str, model_role: str) -> str:
    order = {"not_evidence": 0, "context": 1, "supporting": 2, "primary": 3}
    if model_role not in order:
        return local_role
    return min((local_role, model_role), key=lambda role: order.get(role, 0))


def _domain_intent_adjustment(query_text: str, parameter: dict) -> float:
    """Preserve distinctions that generic token overlap otherwise flattens."""
    query = " ".join(str(query_text or "").lower().split())
    target = " ".join(
        str(parameter.get(key) or "").lower()
        for key in (
            "matrix_subunsur_name", "subunsur_name", "evidence_hint", "uraian",
            "cara_pengujian",
        )
    )
    uraian = " ".join(str(parameter.get("uraian") or "").lower().split())
    evidence_hint = " ".join(str(parameter.get("evidence_hint") or "").lower().split())
    adjustment = 0.0
    risk_register_query = any(phrase in query for phrase in (
        "peta risiko", "matriks risiko", "register risiko", "profil risiko",
    ))
    risk_register_uraian = any(phrase in uraian for phrase in (
        "peta risiko", "register risiko", "profil risiko",
    ))
    risk_register_hint = any(phrase in evidence_hint for phrase in (
        "peta risiko", "register risiko", "profil risiko",
    ))
    if risk_register_query and risk_register_uraian:
        adjustment += 0.50
    elif risk_register_query and risk_register_hint:
        adjustment += 0.16

    risk_monitoring_query = any(phrase in query for phrase in (
        "laporan mr", "laporan manajemen risiko", "pemantauan risiko",
        "monitoring risiko",
    ))
    risk_monitoring_uraian = any(phrase in uraian for phrase in (
        "pemantauan terhadap risiko", "pemantauan risiko", "monitoring risiko",
        "monitoring terhadap risiko",
    ))
    risk_review_uraian = any(phrase in uraian for phrase in (
        "manajemen risiko telah direviu", "reviu manajemen risiko",
    ))
    risk_monitoring_hint = any(phrase in evidence_hint for phrase in (
        "laporan monitoring", "pemantauan risiko", "monitoring risiko",
    ))
    if risk_monitoring_query and risk_monitoring_uraian:
        adjustment += 0.50
    elif risk_monitoring_query and risk_review_uraian:
        adjustment += 0.30
    elif risk_monitoring_query and risk_monitoring_hint:
        adjustment += 0.15

    partnership_target = any(phrase in target for phrase in (
        "kemitraan", "kerja sama", "kerjasama", "mou", "pks",
    ))
    partnership_query = any(phrase in query for phrase in (
        "kemitraan", "kerja sama", "kerjasama", "mou", "pks",
    ))
    if partnership_target and not partnership_query:
        adjustment -= 0.30
    return max(-0.35, min(0.55, adjustment))


def _select_diverse_candidates(ranked: list[dict], *, limit: int) -> list[dict]:
    """Prevent one repeated subunsur/KK variant from hiding other plausible concepts."""
    if len(ranked) <= limit:
        return ranked
    per_detail_limit = max(1, min(4, math.ceil(limit * 0.20)))
    per_subunsur_limit = max(2, min(8, math.ceil(limit * 0.40)))
    detail_counts: Counter[str] = Counter()
    subunsur_counts: Counter[str] = Counter()
    selected: list[dict] = []
    selected_ids: set[int] = set()
    for item in ranked:
        detail = str(item.get("detail_kode") or "")
        subunsur = str(item.get("kode") or "")
        if (
            detail_counts[detail] >= per_detail_limit
            or subunsur_counts[subunsur] >= per_subunsur_limit
        ):
            continue
        selected.append(item)
        selected_ids.add(int(item["parameter_id"]))
        detail_counts[detail] += 1
        subunsur_counts[subunsur] += 1
        if len(selected) >= limit:
            return selected
    for item in ranked:
        if int(item["parameter_id"]) in selected_ids:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected
