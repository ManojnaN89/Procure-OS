from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    control_database_url: str = "postgresql+psycopg://p2p:change-me@127.0.0.1:5440/p2p_control"
    tenant_secret_key: str = ""
    jwt_secret: str = ""
    smtp_host: str = "127.0.0.1"  # not "localhost": on some machines that name adds 0.2 s to every new connection
    smtp_port: int = 1025
    mail_domain: str = "cbcpl.example"  # replies come back to name+TAG@this domain
    mailpit_url: str = "http://127.0.0.1:8025"
    imap_host: str = ""  # set these four to read a real mailbox instead of Mailpit
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    groq_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    nvidia_api_key: str = ""
    model_limits: str = (
        ""  # JSON: {"provider/model": {"rpm": 30, "tpm": 8000, "rpd": 1000}}; empty means the defaults in the gateway
    )
    model_routes: str = ""  # JSON: {"step name": [["provider", "model"], ...]}; empty means the defaults in the gateway
    extra_modules: str = ""  # comma-separated modules imported at startup; they register tools and executors
    storage_dir: str = ""  # empty means storage/ in the repository (git-ignored)
    reference_dir: str = ""  # empty means data/reference in the repository


@lru_cache
def get_settings() -> Settings:
    return Settings()
