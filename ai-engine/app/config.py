"""Application settings.

Values come from environment variables (prefixed ``AI_ENGINE_``) or a local
``.env`` file. Defaults target a local Ollama instance so the app runs offline
out of the box; point the model settings at a hosted provider to develop against
a managed API.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__

# The offline defaults: a local Ollama instance, whose key is ignored. Hoisted to
# constants because both the LLM and STT gateways default to them, and a literal
# repeated four times would trip the duplicated-string-literal check.
_OLLAMA = "ollama"
_OLLAMA_BASE_URL = "http://localhost:11434/v1"


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
    llm_base_url: str = _OLLAMA_BASE_URL
    llm_api_key: str = _OLLAMA  # placeholder; Ollama ignores it, hosted providers need a real key
    llm_model: str = "llama3.2:3b"
    llm_provider: str = _OLLAMA  # label recorded in the generated output's provenance

    # Generation tuning. The request timeout is generous because generation is
    # far slower than the readiness probe; a low temperature keeps output
    # faithful to the source and reproducible.
    llm_request_timeout: float = 120.0
    llm_temperature: float = 0.2
    max_source_chars: int = 24000

    # Hard ceiling on an uploaded file, enforced while streaming it to disk so an
    # oversized upload is rejected (413) before it can exhaust memory or fill the
    # disk. 25 MiB comfortably covers long PDFs and slide decks.
    max_upload_bytes: int = 25 * 1024 * 1024

    # Speech-to-text gateway (Module C). Transcription speaks the same
    # OpenAI-compatible contract as the model gateway, but on the
    # /audio/transcriptions endpoint, which Groq serves with whisper-large-v3.
    # It is a separate setting because one deployment may reach text and audio
    # through different providers — and because local Ollama serves text but not
    # audio, so unlike the LLM gateway there is no working offline default here:
    # transcription needs a hosted STT provider or a local faster-whisper
    # (ADR-0007). Tests mock the transport, so they need neither.
    stt_base_url: str = _OLLAMA_BASE_URL
    stt_api_key: str = _OLLAMA  # placeholder; a hosted STT provider needs a real key
    stt_model: str = "whisper-large-v3"
    stt_provider: str = _OLLAMA  # label recorded in the transcript's provenance
    # A spoken-language hint (ISO-639-1, e.g. "en"); None lets the model detect it.
    stt_language: str | None = None
    # Transcribing long media is far slower than a chat completion, so its own
    # timeout is much larger than the generation one.
    stt_request_timeout: float = 300.0
    # Media files dwarf documents. The provider may impose its own, smaller limit
    # (Groq's free tier caps at 25 MiB) — this is the ceiling we enforce first.
    max_audio_bytes: int = 40 * 1024 * 1024

    @field_validator("llm_base_url", "stt_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        # A trailing slash would produce a double slash when building request URLs.
        return value.rstrip("/")


settings = Settings()
