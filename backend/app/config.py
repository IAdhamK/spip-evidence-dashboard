from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    lumbung_host: str = "https://lumbungfile.kemendesa.go.id"
    lumbung_share_token: str = ""
    scan_timeout_seconds: int = 30
    scan_max_depth: int = 4
    database_path: str = "data/evidence.db"
    app_env: str = "prod"

    smart_upload_enabled: bool = False
    smart_upload_allow_real_upload: bool = False
    smart_upload_require_confirmation: bool = True
    smart_upload_max_bytes: int = 0
    smart_upload_require_ai: bool = False

    analysis_pipeline_v2_enabled: bool = False
    analysis_pipeline_v2_shadow: bool = False
    legacy_smart_upload_enabled: bool = True
    vision_analysis_enabled: bool = False
    verification_pass_enabled: bool = True
    allow_partial_primary: bool = False
    analysis_pending_file_ttl_hours: int = 72
    analysis_payload_storage_backend: str = "database"
    analysis_payload_storage_path: str = "data/analysis-payloads"
    analysis_payload_storage_fsync: bool = True
    analysis_payload_storage_encryption_validated: bool = False
    analysis_storage_encryption_evidence_path: str = ""
    analysis_storage_encryption_key_path: str = ""
    analysis_worker_limit: int = 2
    analysis_job_lease_minutes: int = 15
    analysis_job_heartbeat_seconds: int = 30
    analysis_queue_backend: str = "sqlite"
    analysis_queue_redis_url: str = ""
    analysis_queue_redis_namespace: str = "spip:analysis:v2"
    analysis_queue_redis_connect_timeout_seconds: float = 2.0
    analysis_queue_redis_require_tls: bool = True
    analysis_expected_replicas: int = 1
    analysis_worker_leader_lease_seconds: int = 30
    analysis_worker_leader_heartbeat_seconds: int = 10
    analysis_prometheus_metrics_enabled: bool = True
    analysis_cost_alert_usd_per_hour: float = 10.0
    analysis_model_input_cost_per_million_usd: float = 0.0
    analysis_model_output_cost_per_million_usd: float = 0.0
    analysis_structured_model_enabled: bool = False
    analysis_mapping_reasoning_enabled: bool = False
    analysis_advanced_rag_enabled: bool = False
    analysis_advanced_rag_deepseek_enabled: bool = False
    analysis_advanced_rag_min_confidence: float = 0.68
    analysis_advanced_rag_ambiguity_margin: float = 0.08
    analysis_document_family_min_confidence: float = 0.70
    analysis_relevant_coverage_min_ratio: float = 0.70
    analysis_mapping_ambiguity_margin: float = 0.08
    analysis_decision_confidence_high_threshold: float = 0.80
    analysis_model_verifier_enabled: bool = False
    analysis_model_retry_attempts: int = 2
    analysis_routing_structured_min_complexity: float = 0.25
    analysis_routing_mapping_margin: float = 0.08
    analysis_routing_verifier_min_risk: float = 0.45
    analysis_api_surface: str = "responses"
    analysis_responses_max_output_tokens: int = 4096
    analysis_vision_max_units: int = 12
    analysis_pdf_render_dpi: int = 144
    analysis_pdf_retry_render_dpi: int = 288
    analysis_pdf_retry_max_units: int = 8
    analysis_office_rendering_enabled: bool = True
    analysis_office_render_max_pages: int = 24
    analysis_vision_provider_validated: bool = False
    analysis_local_ocr_enabled: bool = True
    analysis_local_ocr_provider: str = "auto"
    analysis_local_ocr_languages: str = "ind+eng"
    analysis_local_ocr_min_confidence: float = 0.45
    analysis_local_ocr_timeout_seconds: int = 30
    analysis_local_ocr_unit_budget_seconds: int = 180
    analysis_local_ocr_document_budget_seconds: int = 900
    analysis_local_ocr_max_attempts_per_unit: int = 24
    analysis_local_ocr_render_batch_units: int = 4
    analysis_local_ocr_max_units: int = 100
    analysis_local_ocr_tesseract_psm_modes: str = "6,3,11"
    analysis_local_ocr_preprocessing_enabled: bool = True
    analysis_local_ocr_max_image_pixels: int = 16_000_000
    analysis_local_ocr_max_tiles: int = 16
    analysis_rollout_stage: str = "development"
    analysis_canary_percentage: int = 0
    analysis_stable_release_cycles: int = 0
    analysis_require_reviewer_identity: bool = False
    analysis_reviewer_identity_header: str = "X-Reviewer-Identity"
    analysis_require_reviewer_role: bool = False
    analysis_reviewer_role_header: str = "X-Reviewer-Roles"
    analysis_max_redirects: int = 3
    analysis_batch_max_archive_bytes: int = 256 * 1024 * 1024
    analysis_batch_max_entries: int = 2000
    analysis_batch_max_files: int = 200
    analysis_batch_max_entry_bytes: int = 64 * 1024 * 1024
    analysis_batch_max_uncompressed_bytes: int = 512 * 1024 * 1024
    analysis_batch_max_compression_ratio: float = 100.0
    evidence_link_allowed_hosts: str = "docs.google.com,drive.google.com,googleusercontent.com"

    ai_reasoning_enabled: bool = False
    ai_provider: str = "sumopod"
    deepseek_api_key: str = ""
    sumopod_api_key: str = ""
    ai_api_key: str = ""
    deepseek_base_url: str = "https://ai.sumopod.com/v1"
    deepseek_chat_path: str = "/chat/completions"
    deepseek_responses_path: str = "/responses"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_thinking_mode: str = "disabled"
    ai_send_full_document: bool = False
    ai_max_candidates: int = 3
    ai_timeout_seconds: int = 90

    model_config = SettingsConfigDict(env_file=(".env", ".env.dev"), env_file_encoding="utf-8", extra="ignore")

    @property
    def has_share_token(self) -> bool:
        return bool(self.lumbung_share_token.strip())

    @property
    def has_ai_key(self) -> bool:
        return bool(self.resolved_ai_api_key)

    @property
    def resolved_ai_api_key(self) -> str:
        return (self.deepseek_api_key or self.sumopod_api_key or self.ai_api_key).strip()

    @property
    def evidence_link_host_allowlist(self) -> set[str]:
        return {
            item.strip().lower().rstrip(".")
            for item in self.evidence_link_allowed_hosts.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
