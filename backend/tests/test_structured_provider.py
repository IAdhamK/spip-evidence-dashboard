from __future__ import annotations

import unittest

from app.analysis.contracts import DocumentIdentity, EngineStatus
from app.analysis.facts import StructuredFactExtractionEngine, derive_evidence_role
from app.analysis.provider import StructuredFact, StructuredFactResponse
from app.analysis.provider import (
    CompatibleResponsesStructuredProvider,
    CompatibleChatStructuredProvider,
    CompatibleResponsesMappingProvider,
    CompatibleResponsesRAGQueryProvider,
    CompatibleResponsesVerificationProvider,
    configured_mapping_provider,
    configured_rag_query_provider,
    configured_vision_provider,
)
from app.config import Settings


class FakeProvider:
    def extract_facts(self, units):
        return StructuredFactResponse(
            facts=[
                StructuredFact(
                    unit_key="page-1",
                    claim="Evaluasi kinerja dilaksanakan setiap triwulan tahun 2026.",
                    source_quote="Evaluasi kinerja dilaksanakan setiap triwulan",
                    fact_type="evaluation",
                    evidence_role="primary",
                    period="2026",
                    confidence=0.91,
                ),
                StructuredFact(
                    unit_key="page-1",
                    claim="Klaim ini tidak mempunyai kutipan valid pada sumber dokumen.",
                    source_quote="kutipan yang tidak ada",
                    fact_type="unknown",
                    evidence_role="context",
                    confidence=0.5,
                ),
            ]
        )


class FailIfCalledProvider:
    def extract_facts(self, units):
        raise AssertionError("Provider tidak boleh menerima teks visual yang belum diverifikasi.")


class StructuredProviderTests(unittest.TestCase):
    def test_chat_adapter_applies_deepseek_thinking_contract(self) -> None:
        class CapturingProvider(CompatibleChatStructuredProvider):
            def _request_path(self, body, path):
                self.captured = body
                return {"ok": True}

        provider = CapturingProvider(Settings(
            _env_file=None,
            deepseek_thinking_mode="disabled",
        ))
        provider._request({"temperature": 0})
        self.assertEqual(provider.captured["thinking"], {"type": "disabled"})
        self.assertFalse(provider.captured["stream"])

    def test_vision_provider_requires_explicit_capability_validation(self) -> None:
        blocked = Settings(
            _env_file=None,
            vision_analysis_enabled=True,
            analysis_vision_provider_validated=False,
            sumopod_api_key="test-key",
        )
        self.assertIsNone(configured_vision_provider(blocked))
        validated = Settings(
            _env_file=None,
            vision_analysis_enabled=True,
            analysis_vision_provider_validated=True,
            sumopod_api_key="test-key",
        )
        self.assertIsNotNone(configured_vision_provider(validated))

    def test_mapping_provider_requires_explicit_flag_and_has_constrained_schema(self) -> None:
        blocked = Settings(
            _env_file=None,
            analysis_mapping_reasoning_enabled=False,
            sumopod_api_key="test-key",
        )
        self.assertIsNone(configured_mapping_provider(blocked))
        enabled = blocked.model_copy(
            update={"analysis_mapping_reasoning_enabled": True}
        )
        self.assertIsNotNone(configured_mapping_provider(enabled))

        class FakeMappingProvider(CompatibleResponsesMappingProvider):
            def _responses_request(self, **kwargs):
                return {
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": '{"items":[{"mapping_key":"KK3.1:3.1:3.1.1","status":"needs_human_review","findings":[]}],"warnings":[]}',
                        }],
                    }]
                }

        response = FakeMappingProvider(enabled).review_mappings([
            {"mapping_key": "KK3.1:3.1:3.1.1"}
        ])
        self.assertEqual(response.items[0].mapping_key, "KK3.1:3.1:3.1.1")
        self.assertEqual(response.items[0].status, "needs_human_review")

    def test_advanced_rag_enables_deepseek_query_and_mapping_adapters(self) -> None:
        settings = Settings(
            _env_file=None,
            analysis_advanced_rag_enabled=True,
            analysis_advanced_rag_deepseek_enabled=True,
            sumopod_api_key="test-key",
        )
        self.assertIsNotNone(configured_rag_query_provider(settings))
        self.assertIsNotNone(configured_mapping_provider(settings))

        class FakeRAGProvider(CompatibleResponsesRAGQueryProvider):
            def _responses_request(self, **kwargs):
                return {
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": '{"queries":["pemantauan implementasi kebijakan"],"warnings":[]}',
                        }],
                    }]
                }

        response = FakeRAGProvider(settings).expand_queries([
            {"fact_type": "evaluation", "claim": "Kebijakan dipantau berkala."}
        ])
        self.assertEqual(response.queries, ["pemantauan implementasi kebijakan"])

    def test_rejects_model_fact_without_exact_source_quote(self) -> None:
        identity = DocumentIdentity("a.pdf", "application/pdf", 1, "sha", "pdf")
        units = [{
            "id": 1,
            "unit_key": "page-1",
            "status": "processed",
            "text": "Evaluasi kinerja dilaksanakan setiap triwulan tahun 2026.",
            "source_location": {"page": 1},
        }]
        facts, result = StructuredFactExtractionEngine(FakeProvider()).run(identity, units)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["source"]["source_location"], {"page": 1})
        self.assertEqual(facts[0]["evidence_role"], "primary")
        self.assertEqual(facts[0]["evidence_role_method"], "structured_model_advisory_v1")
        self.assertEqual(result.status, EngineStatus.PARTIAL)
        self.assertEqual(result.metrics["rejected_fact_count"], 1)

    def test_pending_visual_semantics_is_not_sent_to_structured_model(self) -> None:
        identity = DocumentIdentity("foto.jpg", "image/jpeg", 1, "sha", "image")
        units = [{
            "id": 1,
            "unit_key": "image-1",
            "status": "partial",
            "text": "Teks layar rapat tampak seperti evaluasi tahun 2026.",
            "source_location": {"image": 1},
            "metadata": {
                "visual_semantics_status": "pending_review_or_vision",
            },
        }]
        facts, result = StructuredFactExtractionEngine(FailIfCalledProvider()).run(
            identity,
            units,
        )
        self.assertEqual(facts, [])
        self.assertEqual(result.status, EngineStatus.SKIPPED)
        self.assertEqual(result.output["visual_semantics_blocked_count"], 1)
        self.assertTrue(any("tidak dikirim" in item for item in result.warnings))

    def test_deterministic_evidence_role_is_advisory_and_contradiction_first(self) -> None:
        self.assertEqual(derive_evidence_role("Evaluasi telah dilaksanakan.", "evaluation"), "primary")
        self.assertEqual(derive_evidence_role("Kebijakan telah ditetapkan.", "policy"), "supporting")
        self.assertEqual(
            derive_evidence_role("Evaluasi belum dilaksanakan.", "evaluation"),
            "contradictory",
        )
        self.assertEqual(derive_evidence_role("Informasi latar belakang.", "unknown"), "context")

    def test_responses_adapter_reads_only_assistant_message_not_reasoning(self) -> None:
        class FakeResponses(CompatibleResponsesStructuredProvider):
            def _responses_request(self, **kwargs):
                return {
                    "output": [
                        {
                            "type": "reasoning",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "not valid json"}],
                        },
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{
                                "type": "output_text",
                                "text": '{"facts":[],"warnings":["abstain"]}',
                            }],
                        },
                    ]
                }

        provider = FakeResponses(Settings(_env_file=None))
        response = provider.extract_facts([{"unit_key": "page-1", "text": "Tidak cukup bukti."}])
        self.assertEqual(response.facts, [])
        self.assertEqual(response.warnings, ["abstain"])

    def test_responses_verifier_contract(self) -> None:
        class FakeVerifier(CompatibleResponsesVerificationProvider):
            def _responses_request(self, **kwargs):
                return {
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": '{"items":[{"mapping_candidate_id":7,"status":"needs_human_review","findings":["source lemah"]}],"warnings":[]}',
                        }],
                    }]
                }

        response = FakeVerifier(Settings(_env_file=None)).verify_mappings(
            [{"mapping_candidate_id": 7}]
        )
        self.assertEqual(response.items[0].mapping_candidate_id, 7)
        self.assertEqual(response.items[0].status, "needs_human_review")

    def test_responses_invalid_json_falls_back_to_chat_contract(self) -> None:
        class FallbackProvider(CompatibleResponsesStructuredProvider):
            def _responses_request(self, **kwargs):
                return {
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": '{"facts":['}],
                    }]
                }

            def _chat_fallback(self, units, warning):
                return StructuredFactResponse(facts=[], warnings=[warning])

        response = FallbackProvider(Settings(_env_file=None)).extract_facts(
            [{"unit_key": "page-1", "text": "evidence"}]
        )
        self.assertEqual(response.facts, [])
        self.assertEqual(response.warnings, ["responses_schema_fallback"])


if __name__ == "__main__":
    unittest.main()
