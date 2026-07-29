"""Module C.1 — Transcription.

Turns an uploaded audio or video file into a time-aligned `Transcript` through a
provider-agnostic speech-to-text gateway, and renders it as WebVTT or SRT
subtitles or plain text. See ADR-0007 for the provider strategy.
"""

__all__ = ["schema", "emit", "pipeline"]
