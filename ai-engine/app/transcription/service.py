"""Shared transcription logic: turn an uploaded media file into a `Transcript`.

The mirror of ``ingestion.service`` for audio and video: reject an unsupported
extension (415) or an over-ceiling file (413) with a clean HTTP error, spool the
upload to a temp file, then hand the file to the STT gateway. Keeping it here
means the router stays thin and the same guards apply however transcription is
called.

A note on the size ceiling: an honest client's ``Content-Length`` is checked
*before* the body is read, so an oversized upload is refused up front. A client
that lies about (or omits) the length still cannot exhaust memory — the body is
spooled to disk in bounded chunks, and our own copy stops at the ceiling — but the
last line of defence against a spoofed multi-gigabyte body filling the disk is a
body-size limit at the server / reverse-proxy layer, which production must set.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import IO

import httpx
from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..config import settings
from .pipeline import TranscriptionConfig, transcribe
from .schema import Transcript

# The container/codec extensions OpenAI-compatible STT providers accept. Video
# containers are allowed because the provider extracts the audio track; the
# engine does not need ffmpeg for that.
AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac", ".ogg", ".oga"}
)

_COPY_CHUNK = 1024 * 1024  # 1 MiB


class MediaTooLargeError(Exception):
    """The upload exceeded the configured audio size ceiling."""


def _copy_capped(upload: UploadFile, dst: IO[bytes], limit: int) -> None:
    """Copy the upload to ``dst`` in chunks, aborting once it exceeds ``limit``."""
    copied = 0
    while chunk := upload.file.read(_COPY_CHUNK):
        copied += len(chunk)
        if copied > limit:
            raise MediaTooLargeError(f"File exceeds the maximum audio size of {limit} bytes.")
        dst.write(chunk)


def _spool_to_tempfile(upload: UploadFile, suffix: str) -> str:
    """Stream the upload to a temp file and return its path. The caller unlinks it.

    On any failure while streaming (notably an oversized file) the half-written
    temp file is removed here, so a rejected upload never leaks a file — only the
    success path hands a live path back for the caller to clean up.
    """
    upload.file.seek(0)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        _copy_capped(upload, tmp, settings.max_audio_bytes)
    except BaseException:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    tmp.close()
    return tmp.name


def _reject_if_declared_too_large(file: UploadFile) -> None:
    """413 up front when the client's declared Content-Length exceeds the ceiling."""
    declared = file.size  # Starlette populates this from the part's Content-Length
    if declared is not None and declared > settings.max_audio_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum audio size of {settings.max_audio_bytes} bytes.",
        )


async def transcribe_upload(
    file: UploadFile, config: TranscriptionConfig, client: httpx.AsyncClient
) -> Transcript:
    """Transcribe an uploaded media file, or raise the right HTTP error.

    415 if the extension is unsupported; 413 if the file is larger than the
    ceiling. STT gateway failures (503/504) are raised by the pipeline and mapped
    by the app-level handlers.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported media type '{suffix or 'unknown'}'. "
                f"Supported: {', '.join(sorted(AUDIO_EXTENSIONS))}."
            ),
        )
    _reject_if_declared_too_large(file)

    try:
        temp_path = await run_in_threadpool(_spool_to_tempfile, file, suffix)
    except MediaTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        return await transcribe(client, temp_path, file.filename or f"audio{suffix}", config)
    finally:
        Path(temp_path).unlink(missing_ok=True)
