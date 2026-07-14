from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.analysis import PARSER_VERSION, PIPELINE_VERSION, PROMPT_VERSION, RULE_VERSION
from app.analysis.contracts import EngineResult, EngineStatus
from app.analysis.provider import (
    CompatibleChatStructuredProvider,
    CompatibleResponsesRAGQueryProvider,
)
from app.analysis.repository import AnalysisRepository
from app.config import Settings
from app.database import Database


class ModelUsageAccountingTests(unittest.TestCase):
    def test_responses_api_usage_is_attached_with_configured_cost(self) -> None:
        class FakeProvider(CompatibleResponsesRAGQueryProvider):
            def _responses_request(self, **kwargs):
                return {
                    "usage": {"input_tokens": 1200, "output_tokens": 300},
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{
                            "type": "output_text",
                            "text": '{"queries":["evaluasi kebijakan"],"warnings":[]}',
                        }],
                    }],
                }

        settings = Settings(
            _env_file=None,
            analysis_model_input_cost_per_million_usd=2.0,
            analysis_model_output_cost_per_million_usd=8.0,
        )
        response = FakeProvider(settings).expand_queries([
            {"fact_type": "evaluation", "claim": "Kebijakan dievaluasi."}
        ])

        self.assertEqual(response.usage_metrics["model_call_count"], 1)
        self.assertEqual(response.usage_metrics["usage_reported_count"], 1)
        self.assertEqual(response.usage_metrics["input_tokens"], 1200)
        self.assertEqual(response.usage_metrics["output_tokens"], 300)
        self.assertEqual(response.usage_metrics["estimated_cost_usd"], 0.0048)

    def test_chat_api_usage_aliases_are_supported(self) -> None:
        class FakeProvider(CompatibleChatStructuredProvider):
            def _request(self, body):
                return {
                    "usage": {"prompt_tokens": 80, "completion_tokens": 20},
                    "choices": [{
                        "message": {"content": '{"facts":[],"warnings":[]}'},
                    }],
                }

        response = FakeProvider(Settings(_env_file=None)).extract_facts([
            {"unit_key": "page-1", "text": "Kebijakan telah ditetapkan."}
        ])

        self.assertEqual(response.usage_metrics["input_tokens"], 80)
        self.assertEqual(response.usage_metrics["output_tokens"], 20)
        self.assertEqual(response.usage_metrics["estimated_cost_usd"], 0.0)

    def test_repository_rollup_is_idempotent_across_engine_upserts(self) -> None:
        with TemporaryDirectory() as directory:
            repository = AnalysisRepository(
                Database(str(Path(directory) / "usage.db")),
                settings=Settings(_env_file=None),
            )
            payload = b"usage-accounting"
            checksum = hashlib.sha256(payload).hexdigest()
            document = repository.upsert_document(
                file_name="usage.txt",
                content_type="text/plain",
                size_bytes=len(payload),
                sha256=checksum,
                payload=payload,
                ttl_hours=1,
            )
            run_id = repository.create_run(
                document_id=document["id"],
                analysis_mode="full_audit",
                pipeline_version=PIPELINE_VERSION,
                parser_version=PARSER_VERSION,
                rule_version=RULE_VERSION,
                prompt_version=PROMPT_VERSION,
                provider="sumopod",
                model="deepseek-v4-pro",
                configuration_hash="usage-test",
            )

            first = EngineResult(
                engine_name="advanced_rag_query_expansion",
                engine_version="usage-v1",
                status=EngineStatus.COMPLETED,
                input_checksum=checksum,
                metrics={
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "estimated_cost_usd": 0.001,
                },
            )
            second = EngineResult(
                engine_name="constrained_mapping_reasoning",
                engine_version="usage-v1",
                status=EngineStatus.COMPLETED,
                input_checksum=checksum,
                metrics={
                    "input_tokens": 200,
                    "output_tokens": 50,
                    "estimated_cost_usd": 0.003,
                },
            )
            repository.save_engine_result(run_id, first)
            repository.save_engine_result(run_id, second)
            repository.save_engine_result(run_id, first)

            with repository.db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT input_tokens, output_tokens, estimated_cost_usd
                    FROM analysis_runs WHERE id = ?
                    """,
                    (run_id,),
                ).fetchone()
            self.assertEqual(row["input_tokens"], 300)
            self.assertEqual(row["output_tokens"], 75)
            self.assertEqual(row["estimated_cost_usd"], 0.004)


if __name__ == "__main__":
    unittest.main()
