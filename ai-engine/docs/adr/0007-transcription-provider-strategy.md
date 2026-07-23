# ADR 0007 — Transcription, and how the speech-to-text provider is chosen

- **Status:** Accepted
- **Date:** 2026-07-23
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7),
  Module C. Extends [ADR-0002](0002-hosted-model-apis-for-development.md) (the model
  gateway) to audio.

## Context

Issue #7's Module C begins with transcription: an uploaded audio or video file →
"VTT + plain-text transcript", via Whisper. The milestone doc schedules it for
W7. This ADR covers that first slice (C.1); auto-chaptering and H5P Interactive
Video (C.2–C.3) build on the `Transcript` it produces.

ADR-0002 already settled the text story: talk to models through the
OpenAI-compatible contract so the provider is configuration, develop against a
hosted open model, keep local self-hosting as the production goal. Audio raises
the same question and one new constraint.

The new constraint is hardware. The development machine is an 8 GB M1
([hardware note in the project record]); `whisper-large-v3` is a ~3 GB model whose
local inference on that machine is slow enough to make an iteration loop painful.
So the text pattern — "local is the default that runs offline out of the box" —
does not transfer cleanly: there is no local audio default that is both faithful
to production *and* comfortable to develop on here.

## Decision

**1. Transcription speaks the OpenAI `/audio/transcriptions` contract**, the audio
sibling of chat-completions. Groq serves it with `whisper-large-v3`, OpenAI with
`whisper-1`, and a local `faster-whisper` fronted by the same shape works too. The
provider is again just base URL, key, and model — `app/transcription/stt_client.py`
is the audio counterpart of `summarization/llm_client.py`, with the same typed
error hierarchy so the transport layer maps failures to 503/504 identically.

**2. The STT gateway is configured separately from the model gateway.** A single
deployment may reach text and audio through different providers, and — unlike
text — there is **no working offline default**: Ollama serves text but not audio.
The `AI_ENGINE_STT_*` settings default to placeholders, and `.env.example` says
plainly that transcription needs a hosted provider or a local `faster-whisper`.
This is an honest asymmetry with ADR-0002, recorded rather than papered over.

**3. Development runs against hosted Groq `whisper-large-v3`; local
`faster-whisper` is the documented production/offline path**, kept an *optional*
dependency rather than a hard one, because forcing a multi-hundred-MB wheel and a
model download on every install — including CI, which never transcribes for real —
would be a poor trade on this hardware. Production self-hosting stays the goal, as
in ADR-0002.

**4. The request asks for `verbose_json` with per-segment timestamps**, so the
reply carries start/end times per cue. Without that the transcript would be one
flat string and could not become subtitles or, later, chapter markers. The
provider's segment array is mapped onto our own `TranscriptSegment` shape in one
place (`pipeline.py`), so a second provider with different field names is absorbed
there and the rest of Module C only ever sees a `Transcript`.

**5. Three output shapes from one transcript.** `Transcript` (structured JSON,
the default) carries timed segments plus `full_text`; `emit.py` renders **WebVTT**
and **SRT** on demand via `?format=vtt|srt`. The two subtitle formats differ in
exactly two easy-to-break ways — the millisecond separator (`.` for VTT, `,` for
SRT) and the header/numbering — both pinned by tests. "VTT + plain-text
transcript" is the #7 criterion; SRT is a cheap, widely-imported extra from the
same data.

## What this explicitly defers

- **Speaker diarisation.** #7 lists it; the milestone doc says only "transcription
  + subtitles", and it is one of the scope deltas pending a mentor decision. The
  contract reserves a per-segment `speaker` field (unset) so enabling it later
  needs no migration, exactly as `weight` was reserved on the short-answer key
  points. Not built in C.1.
- **Local ffmpeg / media probing.** Video containers are accepted and forwarded;
  the provider extracts the audio track. The engine does not shell out to ffmpeg,
  so it stays a pure-Python service (ADR-0001).
- **Auto-chaptering and H5P Interactive Video** (C.2–C.3): the next slices, built
  on this transcript.

## Consequences

- **One more provider-agnostic gateway, same failure contract.** 503 when the STT
  gateway is unreachable / key-rejected / rate-limited, 504 on timeout, 413/415 on
  the upload — uniform with every other endpoint.
- **Tests run fully offline.** The STT transport is mocked with an httpx
  `MockTransport`, exactly as the model gateway is, so no key and no audio model
  are needed to test the mapping, the emitters, and the error paths.
- **A documented deviation from "local Whisper" as written in #7**, for the
  hardware reason above — the same shape as ADR-0002's deviation, and one to fold
  into the ticket reconciliation.
- **Verified live, separately from the repo:** a real audio file transcribed
  through the hosted provider and rendered to VTT/SRT. That run is a one-off, not
  reproducible from the repo (it needs a key and a media file); the offline suite
  is what proves the logic.
