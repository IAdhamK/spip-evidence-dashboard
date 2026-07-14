from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from typing import Any

from app.analysis import PIPELINE_VERSION
from app.analysis.domain.retrieval import parameter_corpus_tokens, tokenize


RETRIEVAL_FEEDBACK_LEARNING_VERSION = "expert-gold-lexical-v1"
MINIMUM_FEEDBACK_DOCUMENT_SUPPORT = 3
MINIMUM_FEEDBACK_PRECISION = 0.80


def retrieval_parameter_catalog_sha256(parameters: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "kk_id": str(item.get("kk_id") or ""),
            "kode": str(item.get("kode") or ""),
            "detail_kode": str(item.get("detail_kode") or ""),
            "retrieval_tokens": sorted(parameter_corpus_tokens(item)),
        }
        for item in parameters
    ]
    canonical.sort(key=lambda item: (item["kk_id"], item["kode"], item["detail_kode"]))
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def compile_retrieval_feedback_registry(
    expert_items: list[dict[str, Any]],
    facts_by_run: dict[int, list[dict[str, Any]]],
    parameters: list[dict[str, Any]],
    *,
    dataset_sha256: str,
    minimum_document_support: int = MINIMUM_FEEDBACK_DOCUMENT_SUPPORT,
    minimum_precision: float = MINIMUM_FEEDBACK_PRECISION,
) -> dict[str, Any]:
    """Compile conservative lexical feedback from active two-person expert labels.

    Only novel alphabetic terms from facts explicitly selected by reviewers are
    considered. A term must recur across distinct documents and consistently map
    to the same official parameter. Organization/period tokens and terms already
    present in the official parameter corpus are excluded.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", str(dataset_sha256 or "")):
        raise ValueError("dataset_sha256 retrieval feedback tidak valid.")
    if int(minimum_document_support) < 2:
        raise ValueError("minimum_document_support minimal 2.")
    if not 0.5 <= float(minimum_precision) <= 1:
        raise ValueError("minimum_precision harus berada pada rentang 0.5-1.0.")

    parameters_by_key = {
        (str(item["kk_id"]), str(item["kode"]), str(item["detail_kode"])): item
        for item in parameters
    }
    parameter_catalog_sha256 = retrieval_parameter_catalog_sha256(parameters)
    official_vocabulary = set().union(
        *(parameter_corpus_tokens(item) for item in parameters)
    ) if parameters else set()
    observed_documents: dict[str, set[str]] = defaultdict(set)
    support_documents: dict[tuple[int, str], set[str]] = defaultdict(set)
    parameter_by_id = {int(item["id"]): item for item in parameters}
    source_label_count = 0

    for label in expert_items:
        if label.get("dataset_status") != "expert_gold":
            continue
        if (label.get("dataset_partition") or "evaluation") != "learning":
            continue
        if label.get("outcome") not in {"confirmed", "corrected"}:
            continue
        selected_ids = {int(value) for value in (label.get("selected_fact_ids") or [])}
        if not selected_ids:
            continue
        target_parameters = []
        for mapping in label.get("expected_mappings") or []:
            key = (
                str(mapping.get("kk_id") or ""),
                str(mapping.get("kode") or ""),
                str(mapping.get("detail_kode") or ""),
            )
            parameter = parameters_by_key.get(key)
            if parameter and parameter not in target_parameters:
                target_parameters.append(parameter)
        if not target_parameters:
            continue

        run_id = int(label["run_id"])
        selected_facts = [
            fact for fact in facts_by_run.get(run_id, [])
            if int(fact.get("id") or 0) in selected_ids
        ]
        if not selected_facts:
            continue
        exclusion_tokens = set()
        for fact in selected_facts:
            exclusion_tokens |= tokenize(str(fact.get("organization") or ""))
            exclusion_tokens |= tokenize(str(fact.get("period") or ""))
        candidate_terms = set().union(
            *(tokenize(str(fact.get("claim") or "")) for fact in selected_facts)
        )
        candidate_terms = {
            term for term in candidate_terms
            if 4 <= len(term) <= 32
            and re.fullmatch(r"[a-z]+", term)
            and term not in official_vocabulary
            and term not in exclusion_tokens
        }
        source_label_count += 1
        if not candidate_terms:
            continue

        document_sha256 = str(label.get("sha256") or f"run:{run_id}")
        for term in candidate_terms:
            observed_documents[term].add(document_sha256)
            for parameter in target_parameters:
                support_documents[(int(parameter["id"]), term)].add(document_sha256)

    terms = []
    for (parameter_id, term), supported_documents in support_documents.items():
        observed_count = len(observed_documents[term])
        support_count = len(supported_documents)
        precision = support_count / max(1, observed_count)
        if support_count < int(minimum_document_support) or precision < float(minimum_precision):
            continue
        parameter = parameter_by_id[parameter_id]
        terms.append({
            "parameter_id": parameter_id,
            "kk_id": parameter["kk_id"],
            "kode": parameter["kode"],
            "detail_kode": parameter["detail_kode"],
            "normalized_term": term,
            "term_sha256": hashlib.sha256(term.encode("utf-8")).hexdigest(),
            "document_support": support_count,
            "observed_document_count": observed_count,
            "precision": round(precision, 6),
        })
    terms.sort(key=lambda item: (
        item["kk_id"], item["kode"], item["detail_kode"], item["term_sha256"]
    ))
    canonical = {
        "dataset_sha256": dataset_sha256,
        "pipeline_version": PIPELINE_VERSION,
        "learning_version": RETRIEVAL_FEEDBACK_LEARNING_VERSION,
        "parameter_catalog_sha256": parameter_catalog_sha256,
        "minimum_document_support": int(minimum_document_support),
        "minimum_precision": float(minimum_precision),
    }
    checksum_terms = [
        {
            "kk_id": item["kk_id"],
            "kode": item["kode"],
            "detail_kode": item["detail_kode"],
            "term_sha256": item["term_sha256"],
            "document_support": item["document_support"],
            "observed_document_count": item["observed_document_count"],
            "precision": item["precision"],
        }
        for item in terms
    ]
    registry_sha256 = hashlib.sha256(
        json.dumps(
            {**canonical, "terms": checksum_terms},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **canonical,
        "terms": terms,
        "registry_sha256": registry_sha256,
        "source_label_count": source_label_count,
        "term_count": len(terms),
    }


class RetrievalFeedbackLearningEngine:
    name = "retrieval_feedback_learning"
    version = RETRIEVAL_FEEDBACK_LEARNING_VERSION

    def refresh(self, repository: Any) -> dict[str, Any]:
        dataset = repository.expert_dataset_summary()
        if int(dataset.get("partition_overlap_count") or 0):
            repository.deactivate_retrieval_feedback_snapshots()
            return {
                "active": False,
                "reason": "Dokumen yang sama terdapat pada partisi Evaluasi dan Learning.",
                "term_count": 0,
                "dataset_sha256": None,
            }
        dataset_sha256 = dataset.get("learning_dataset_sha256")
        if not dataset_sha256:
            repository.deactivate_retrieval_feedback_snapshots()
            return {
                "active": False,
                "reason": "Belum ada expert-gold partisi learning yang aktif.",
                "term_count": 0,
                "dataset_sha256": None,
            }
        expert_items = repository.list_expert_dataset_items()
        facts_by_run = {
            int(item["run_id"]): repository.list_facts(int(item["run_id"]))
            for item in expert_items
            if item.get("dataset_status") == "expert_gold"
            and (item.get("dataset_partition") or "evaluation") == "learning"
        }
        compiled = compile_retrieval_feedback_registry(
            expert_items,
            facts_by_run,
            repository.parameter_index(),
            dataset_sha256=str(dataset_sha256),
        )
        return repository.save_retrieval_feedback_snapshot(
            compiled,
            expert_gold_case_count=int(dataset.get("learning_gold_case_count") or 0),
        )

    def refresh_fail_closed(self, repository: Any) -> dict[str, Any]:
        try:
            return self.refresh(repository)
        except Exception as exc:
            repository.deactivate_retrieval_feedback_snapshots()
            return {
                "active": False,
                "term_count": 0,
                "reason": "Registry feedback gagal disegarkan dan dinonaktifkan.",
                "error_type": type(exc).__name__,
            }


class EvaluationLearningEngine:
    name = "evaluation_learning"
    version = PIPELINE_VERSION

    def promotion_readiness(
        self,
        reports: list[dict],
        *,
        approved_rule_count: int,
        total_rule_count: int,
        high_security_findings: int,
        vision_required: bool = False,
        vision_ready: bool = False,
        storage_ready: bool = True,
    ) -> dict:
        expert_reports = [
            item for item in reports
            if item.get("dataset_status") == "expert_gold"
            and item.get("pipeline_version") == PIPELINE_VERSION
            and bool(item.get("release_authority"))
        ]
        latest = expert_reports[0] if expert_reports else None
        metrics = (latest or {}).get("metrics") or {}
        case_count = int((latest or {}).get("case_count") or 0)
        quality_reasons = []
        if case_count < 50:
            quality_reasons.append(f"Expert gold baru {case_count}/50 kasus untuk shadow pilot.")
        if float(metrics.get("retrieval_recall_at_5") or 0) < 0.95:
            quality_reasons.append("Retrieval Recall@5 expert gold belum mencapai 95%.")
        if float(metrics.get("source_accuracy") or 0) < 0.95:
            quality_reasons.append("Source-location accuracy expert gold belum mencapai 95%.")
        if float(metrics.get("overgrade_rate") if metrics.get("overgrade_rate") is not None else 1) > 0.02:
            quality_reasons.append("Overgrade rate expert gold masih di atas 2%.")
        if float(metrics.get("grade_label_coverage") or 0) < 0.95:
            quality_reasons.append("Cakupan label grade expert gold belum mencapai 95%.")
        if float(metrics.get("grade_assessment_coverage") or 0) < 0.95:
            quality_reasons.append("Cakupan assessment grade V2 belum mencapai 95%.")
        if float(metrics.get("evidence_role_label_coverage") or 0) < 0.95:
            quality_reasons.append("Cakupan label peran evidence belum mencapai 95%.")
        if float(metrics.get("template_label_coverage") or 0) < 0.95:
            quality_reasons.append("Cakupan label status template belum mencapai 95%.")
        if float(metrics.get("template_detection_recall") or 0) < 0.95:
            quality_reasons.append("Recall deteksi template kosong belum mencapai 95%.")
        shadow_ready = not quality_reasons

        canary_reasons = list(quality_reasons)
        if approved_rule_count < total_rule_count or total_rule_count == 0:
            canary_reasons.append(
                f"Rule approval baru {approved_rule_count}/{total_rule_count}; seluruh rule aktif wajib disahkan."
            )
        if high_security_findings:
            canary_reasons.append(f"Masih ada {high_security_findings} high-severity security finding.")
        if vision_required and not vision_ready:
            canary_reasons.append(
                "Korpus memiliki unit OCR/vision, tetapi capability dan consent vision belum efektif."
            )
        if not storage_ready:
            canary_reasons.append(
                "Enkripsi volume payload/database produksi belum divalidasi."
            )
        canary_ready = not canary_reasons

        general_reasons = list(canary_reasons)
        if case_count < 200:
            general_reasons.append(f"Expert gold baru {case_count}/200 kasus untuk general release.")
        general_reasons.append("Dua release cycle stabil dan sign-off product/domain owner harus dicatat eksternal.")
        return {
            "engine_name": self.name,
            "engine_version": self.version,
            "latest_expert_report": latest,
            "shadow": {"ready": shadow_ready, "reasons": quality_reasons},
            "canary": {"ready": canary_ready, "reasons": canary_reasons},
            "general_release": {"ready": False, "reasons": general_reasons},
            "approved_rule_count": approved_rule_count,
            "total_rule_count": total_rule_count,
            "high_security_findings": high_security_findings,
            "vision_required": bool(vision_required),
            "vision_ready": bool(vision_ready),
            "storage_ready": bool(storage_ready),
        }
