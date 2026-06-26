# ADR 0002 — Develop against hosted model APIs behind a provider-agnostic interface

- **Status:** Accepted
- **Date:** 2026-06-25
- **Deciders:** Siddhi Shinde (Senior Software Engineer / technical mentor, Tekdi); Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7); amends the inference part of [ADR-0001](0001-standalone-lms-agnostic-engine.md)

## Context

Issue #7 specifies that the platform run on self-hosted local models (Llama 3,
Mistral, Whisper) with no hard dependency on external cloud AI, and ADR-0001
followed that with a local-first default. In practice, self-hosting during
development needs more memory than the dev machines and the provided VM have, so
it blocked progress and would have required a costly VM upgrade.

## Decision

For the **development** phase, develop against **hosted, managed model APIs**
instead of self-hosting. Access them through a **provider-agnostic interface** —
the OpenAI-compatible chat-completions contract — so the provider is a
configuration choice (base URL, API key, model), not a code dependency.

Prefer a hosted provider that serves the **same open models** #7 mandates (e.g.
Groq, which hosts Llama 3 and Whisper), so what is built and validated in
development carries over faithfully when the models are self-hosted.

Local Ollama remains a first-class implementation of the same interface and the
offline / CI default.

Issue #7's self-hosted, no-cloud requirement stays the **production** goal;
self-hosting becomes a deployment concern, swapped in behind the same interface.

## Consequences

- **Unblocks development** — no model infrastructure to provision or manage; the
  16 GB VM upgrade is no longer needed for development.
- **Faithful swap** — using a hosted provider of the same open models keeps
  quality (and the no-hallucination requirement) representative of production.
- **Clean seam** — one OpenAI-compatible client; switching provider is config
  only, so the pipelines never change.
- **Guardrails (deferred, not dropped):** production quality and the performance
  NFRs (e.g. ≤30 s for a 50-page document) must still be validated on the actual
  self-hosted models and target hardware before release; #7's privacy / no-cloud
  clause means production cannot depend on external APIs for tenant data.

## Notes

- Configured via `AI_ENGINE_LLM_*` (see [`.env.example`](../../.env.example)).
  Default: local Ollama. Development: a hosted provider such as Groq.
