from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    lumbung_host: str = "https://lumbungfile.kemendesa.go.id"
    lumbung_share_token: str = ""
    scan_timeout_seconds: int = 30
    scan_max_depth: int = 4
    database_path: str = "data/evidence.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def has_share_token(self) -> bool:
        return bool(self.lumbung_share_token.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
