from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
from time import perf_counter

from app.analysis.contracts import DocumentIdentity, EngineResult, EngineStatus
from app.analysis.provider import RAGQueryExpansionProvider


ADVANCED_RAG_VERSION = "advanced-rag-v1"
RRF_K = 60


# Padanan istilah administrasi/SPIP yang konservatif. Daftar ini memperluas cara
# penulisan, bukan menambah fakta atau mengubah arti parameter resmi.
DOMAIN_TERM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("kebijakan", "pedoman", "ketentuan", "regulasi", "peraturan"),
    ("sosialisasi", "diseminasi", "penyampaian", "pengkomunikasian"),
    ("pelaksanaan", "implementasi", "penerapan", "dilaksanakan"),
    ("evaluasi", "monitoring", "pemantauan", "reviu", "penilaian"),
    ("perbaikan", "penyempurnaan", "tindaklanjut", "tindak", "lanjut"),
    ("risiko", "risk", "register", "peta", "mitigasi"),
    ("kinerja", "indikator", "iku", "sasaran", "capaian"),
    ("keuangan", "anggaran", "dipa", "pagu", "realisasi", "belanja"),
    ("aset", "bmn", "inventaris", "barang", "kib"),
    ("kepatuhan", "ketaatan", "compliance", "perundangan"),
    ("sdm", "pegawai", "kepegawaian", "personel", "kompetensi"),
    ("organisasi", "unit", "kelembagaan", "struktur"),
    ("dokumen", "laporan", "berita", "notulen", "rekapitulasi"),
)


def _normalized_token(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


_DOMAIN_LOOKUP = {
    _normalized_token(term): {
        _normalized_token(member) for member in group if _normalized_token(member)
    }
    for group in DOMAIN_TERM_GROUPS
    for term in group
}


def expand_domain_tokens(tokens: set[str] | list[str]) -> set[str]:
    expanded = {_normalized_token(token) for token in tokens if _normalized_token(token)}
    for token in list(expanded):
        expanded.update(_DOMAIN_LOOKUP.get(token, set()))
    return expanded


def sparse_semantic_vector(tokens: list[str] | set[str]) -> dict[str, float]:
    sequence = [_normalized_token(token) for token in tokens if _normalized_token(token)]
    expanded = expand_domain_tokens(sequence)
    vector = Counter({f"term:{token}": 1.0 for token in expanded})
    for left, right in zip(sequence, sequence[1:]):
        if left and right:
            vector[f"bigram:{left}_{right}"] += 1.35
    return dict(vector)


def vector_cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    overlap = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in overlap)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def reciprocal_rank_fusion(
    component_scores: dict[str, dict[int, float]],
    *,
    k: int = RRF_K,
) -> dict[int, float]:
    fused: dict[int, float] = Counter()
    active_components = 0
    for scores in component_scores.values():
        ranked = sorted(
            ((candidate_id, score) for candidate_id, score in scores.items() if score > 0),
            key=lambda item: (-item[1], item[0]),
        )
        if not ranked:
            continue
        active_components += 1
        for rank, (candidate_id, _) in enumerate(ranked, start=1):
            fused[candidate_id] += 1 / (k + rank)
    maximum = active_components / (k + 1) if active_components else 1.0
    return {candidate_id: min(1.0, score / maximum) for candidate_id, score in fused.items()}


def retrieval_needs_model_expansion(
    candidates: list[dict],
    *,
    minimum_confidence: float,
    ambiguity_margin: float,
) -> bool:
    if not candidates:
        return True
    top = float(candidates[0].get("retrieval_score") or 0)
    second = float(candidates[1].get("retrieval_score") or 0) if len(candidates) > 1 else 0.0
    return top < minimum_confidence or (len(candidates) > 1 and top - second < ambiguity_margin)


class AdvancedRAGQueryExpansionEngine:
    name = "advanced_rag_query_expansion"
    version = ADVANCED_RAG_VERSION

    def __init__(self, provider: RAGQueryExpansionProvider):
        self.provider = provider

    def run(
        self,
        identity: DocumentIdentity,
        facts: list[dict],
    ) -> tuple[list[str], EngineResult]:
        started = perf_counter()
        queries: list[str] = []
        provider_failed = False
        provider_warning_count = 0
        usage_metrics: dict[str, int | float] = {}
        warnings: list[str] = []
        try:
            response = self.provider.expand_queries(facts)
            usage_metrics = response.usage_metrics
            provider_warning_count = len(response.warnings)
            seen: set[str] = set()
            for query in response.queries[:6]:
                normalized = " ".join(str(query or "").split())[:240]
                fingerprint = normalized.lower()
                if len(re.findall(r"[a-zA-Z0-9]+", normalized)) < 2 or fingerprint in seen:
                    continue
                seen.add(fingerprint)
                queries.append(normalized)
            if provider_warning_count:
                warnings.append(
                    f"Provider mengembalikan {provider_warning_count} warning; isi warning tidak dipersistenkan."
                )
        except Exception:
            provider_failed = True
            warnings.append(
                "DeepSeek query expansion gagal; retrieval lokal tetap digunakan."
            )
        result = EngineResult(
            engine_name=self.name,
            engine_version=self.version,
            status=EngineStatus.FAILED if provider_failed else EngineStatus.COMPLETED,
            input_checksum=identity.sha256,
            input_refs=[f"fact:{fact.get('fact_key')}" for fact in facts],
            output_refs=[
                "rag-query:" + hashlib.sha256(query.encode("utf-8")).hexdigest()
                for query in queries
            ],
            coverage={"required": 1, "processed": int(not provider_failed), "failed": int(provider_failed)},
            warnings=warnings,
            metrics={
                "duration_ms": max(0, round((perf_counter() - started) * 1000)),
                "query_count": len(queries),
                "provider_warning_count": provider_warning_count,
                **usage_metrics,
            },
            output={
                "query_count": len(queries),
                "authority": "retrieval_expansion_only_no_fact_mapping_grade_or_upload_authority",
                "query_content_persisted": False,
            },
            error_message="Advanced RAG query provider failed." if provider_failed else None,
        ).finish()
        return queries, result
