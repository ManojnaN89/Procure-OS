from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    control_database_url: str = "postgresql+psycopg://p2p:change-me@localhost:5440/p2p_control"
    tenant_secret_key: str = ""
    jwt_secret: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    model_routes: str = ""  # JSON: {"step name": [["provider", "model"], ...]}; empty means the defaults in the gateway
    storage_dir: str = ""  # empty means storage/ in the repository (git-ignored)
    reference_dir: str = ""  # empty means data/reference in the repository


@lru_cache
def get_settings() -> Settings:
    return Settings()
