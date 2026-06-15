"""Application settings.

Values come from environment variables (prefixed ``AI_ENGINE_``) or a local
``.env`` file. Defaults match the dev setup (Ollama on :11434, the c4gt-postgres
and c4gt-redis containers), so the app runs out of the box for local development.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_ENGINE_",
        extra="ignore",
    )

    app_name: str = "lms-ai-engine"
    version: str = __version__
    environment: str = "development"

    # Local model gateway (Ollama). Keep inference offline — no external AI APIs.
    # Dev work runs on the small model; correctness is validated on the larger one.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_validate_model: str = "llama3:8b"

    # Backing services. In dev these are the c4gt-postgres / c4gt-redis containers.
    database_url: str = "postgresql+psycopg://c4gt:c4gt_dev@localhost:5433/lms_ai"
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()
