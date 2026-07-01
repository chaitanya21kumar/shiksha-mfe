"""Tests for application settings."""

from app.config import Settings


def test_llm_base_url_strips_trailing_slash():
    # A trailing slash must be removed so request URLs don't get a double slash.
    s = Settings(llm_base_url="https://api.groq.com/openai/v1/")
    assert s.llm_base_url == "https://api.groq.com/openai/v1"
