# LMS AI Engine

A local-first, **LMS-agnostic** service that turns documents, slides, audio and
video into structured, interactive micro-learning — emitted as portable open
standards (**H5P / SCORM / xAPI** + JSON) so any LMS can consume it.

Inference goes through an **OpenAI-compatible model interface**, so it can run
against a local [Ollama](https://ollama.com) (the default — offline, no keys) or
a hosted provider during development, and move to **self-hosted** open models for
production (Llama 3, Whisper) without changing the pipeline. See
[`docs/adr/0002`](docs/adr/0002-hosted-model-apis-for-development.md).

> Implements [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7).
> See [`docs/adr/0001-standalone-lms-agnostic-engine.md`](docs/adr/0001-standalone-lms-agnostic-engine.md)
> for why this is standalone.

## Modules (per the project plan)

| Module | Scope |
|---|---|
| **A — Document Ingestion** | PDF / PPT → structured JSON; summaries, glossary, narration scripts |
| **B — Assessment Suite** | MCQ / match-the-pair / fill-in-the-blanks → H5P + SCORM |
| **C — Multimedia Intelligence** | Whisper transcription, chaptering → H5P Interactive Video |
| **D — Micro-Learning Studio** | Assemble H5P / SCORM / HTML5 lessons; tenant branding; review gate |

Phase 1 was the FastAPI gateway and system probes. **Module A.1** (document
ingestion — PDF and PPTX into structured JSON, exposed at `POST /ingest`) is
built on top of it. **Module A.2** (summarisation) adds a local-LLM layer over a
parsed document, deriving a summary, key takeaways, a glossary, and a course
outline via Ollama, exposed at `POST /summarize` and `POST /summarize/file`. The
later modules follow.

## Requirements

- Python 3.11+ (developed on 3.12)
- An OpenAI-compatible model endpoint — either a local [Ollama](https://ollama.com)
  with `llama3.2:3b` (the default), or a hosted provider such as Groq (configure
  `AI_ENGINE_LLM_*`; see [`.env.example`](.env.example))

## Run it

```bash
cd ai-engine
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # or: pip install fastapi "uvicorn[standard]" pydantic-settings httpx pytest

cp .env.example .env           # optional; defaults already match the dev setup
uvicorn app.main:app --reload --port 8000
```

Then:

- `GET /health` — liveness (no dependencies touched)
- `GET /ready` — readiness (checks the configured model gateway is reachable)
- `POST /ingest` — parse a PDF/PPTX into structured JSON
- `POST /summarize` — derive insights from an already-parsed document
- `POST /summarize/file` — parse a PDF/PPTX and derive insights in one call
- `GET /docs` — interactive API docs

## Test

```bash
pytest
```

## Configuration

All settings are environment variables prefixed `AI_ENGINE_` (or a local
`.env`). See [`.env.example`](.env.example) for the full list and defaults.
