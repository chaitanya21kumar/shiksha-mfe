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

## Addendum, 20 August 2026 — a hosted model can be withdrawn under you

Groq retired `llama-3.1-8b-instant`, the model this engine was pinned to, on
**16 August 2026**. From that morning the gateway answered every request with
`404 model_not_found`, and the engine produced nothing for four days.

The re-pin itself was trivial (`openai/gpt-oss-20b`, the provider's own
replacement, which serves JSON mode and is a little faster). The finding worth
recording is why the configured fallback did not cover it.

`_status_error` sorted a 404 into `LLMBadResponse`, and `chat_json_for` fails over
only on `LLMUnavailable`. That narrowness was deliberate — retrying a bad reply on
a second gateway hides a real fault behind a slower one — but it put a retired
model on the wrong side of the line. A model the gateway no longer serves is a
fact about the **gateway**, exactly like the rejected key that was already handled
there: no retry helps, no wait helps, and the fallback can answer immediately.

So the classifier now recognises it, and the failover carries it. Two details are
deliberate:

- **Matched on the body, not the status.** A bare 404 is far more often a mistyped
  base URL, and failing over on that would hide a typo behind a fallback that
  quietly works — the same class of silent-success failure this change exists to
  end. Only a 400 or 404 whose body actually says the model is unknown counts.
- **The warning names the model and quotes the provider.** The operator's next
  action is to re-pin a version, not to restart something, and a generic
  "gateway unavailable" would not tell them that.

This is a development-time exposure by construction: #7's production target is
self-hosted models, which cannot be withdrawn by a vendor. It is recorded because
the same shape returns whenever a provider deprecates — the answer is that the
engine must **degrade loudly**, never silently.
