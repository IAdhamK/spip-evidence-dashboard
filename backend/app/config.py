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

    ai_reasoning_enabled: bool = False
    ai_provider: str = "deepseek"
    deepseek_api_key: str = ""
    sumopod_api_key: str = ""
    ai_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_path: str = "/chat/completions"
    deepseek_model: str = "deepseek-v4-flash"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
