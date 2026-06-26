"""Application settings.

Values come from environment variables (prefixed ``AI_ENGINE_``) or a local
``.env`` file. Defaults target a local Ollama instance so the app runs offline
out of the box; point the model settings at a hosted provider to develop against
a managed API.
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

    # Model gateway — any OpenAI-compatible chat endpoint. The engine speaks the
    # OpenAI chat-completions contract, which a local Ollama instance and hosted
    # providers (Groq, OpenAI, OpenRouter, …) all support, so switching provider
    # is just configuration. Defaults point at a local Ollama so the app runs
    # offline; set these to a hosted provider that serves the same open models
    # (e.g. Groq with Llama 3) to develop against a managed API.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"  # placeholder; Ollama ignores it, hosted providers need a real key
    llm_model: str = "llama3.2:3b"
    llm_provider: str = "ollama"  # label recorded in the generated output's provenance

    # Generation tuning. The request timeout is generous because generation is
    # far slower than the readiness probe; a low temperature keeps output
    # faithful to the source and reproducible.
    llm_request_timeout: float = 120.0
    llm_temperature: float = 0.2
    max_source_chars: int = 24000


settings = Settings()
