# ADR 0001 — Build the AI engine standalone and LMS-agnostic

- **Status:** Accepted
- **Date:** 2026-06-12 (decided at Weekly Sync #1)
- **Deciders:** Dnyanesh Kulkarni (Project Manager, Tekdi); Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7)

## Context

The project (issue #7) is an AI engine that turns documents, slides, audio and
video into interactive micro-learning. The open question at kickoff was *how
tightly to couple it to the Shiksha / Sunbird platform* — build it as a
Sunbird-native service, or as a standalone service that any LMS can consume.

Coupling to Sunbird would require sandbox credentials and platform-specific
integration work up front, and would tie the engine's value to one platform.

## Decision

Build the engine **standalone and LMS-agnostic**. It exposes a plain HTTP API
and emits **portable, open standards** — H5P, SCORM and xAPI, plus structured
JSON — so it can be integrated with any LMS later, and reused across Tekdi's
other products rather than being locked to one.

Inference is **local-first**: self-hosted open models via Ollama, with no
external AI APIs in the default path.

## Consequences

- **Unblocks the core build** — no Sunbird sandbox credentials are needed to
  build and test the engine.
- **Portable output** — H5P / SCORM / xAPI / JSON consumed by Shiksha or any
  other LMS; LMS integration becomes a thin adapter, not a hard dependency.
- **Reusable** — the same engine can serve multiple Tekdi products.
- **Flexible deployment** — runs on a Tekdi server or a client server, decided
  per project.
- **Trade-off** — anything Shiksha-specific (e.g. publishing directly into a
  Sunbird content repository) becomes a later, separate integration layer
  rather than something baked in now.

## Notes

- This engine lives in its own folder (`ai-engine/`) inside the `shiksha-mfe`
  fork. Because it is self-contained, it can be split into its own repository
  later with little cost if the team prefers.
