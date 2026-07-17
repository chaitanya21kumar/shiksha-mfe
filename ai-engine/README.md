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
| **A — Document Ingestion** | PDF, PPTX, DOCX, CSV, TXT, Markdown, HTML → structured JSON; summaries, glossary, narration scripts |
| **B — Assessment Suite** | MCQ / match-the-pair / fill-in-the-blanks → H5P + SCORM |
| **C — Multimedia Intelligence** | Whisper transcription, chaptering → H5P Interactive Video |
| **D — Micro-Learning Studio** | Assemble H5P / SCORM / HTML5 lessons; tenant branding; review gate |

Phase 1 was the FastAPI gateway and system probes. **Module A.1** (document
ingestion — PDF, PPTX, DOCX, CSV, TXT, Markdown and HTML into one structured
JSON shape, exposed at `POST /ingest`) is built on top of it. **Module A.2**
(summarisation) adds a layer over a parsed document, deriving a summary, key
takeaways, a glossary, and a course outline via a configurable OpenAI-compatible
model gateway, exposed at `POST /summarize` and `POST /summarize/file`. **Module
A.3** (narration) turns the same parsed document into a spoken `NarrationScript`
— one speakable segment per slide or section, each with a word count and duration
estimate — exposed at `POST /narrate` and `POST /narrate/file`.

**Module B** (assessment) turns a parsed document into a source-grounded
`AssessmentSet` — multiple-choice, match-the-pair, and fill-in-the-blank
questions — exposed at `POST /assess` and `POST /assess/file`. Every question is
verified against the source and dropped if it cannot be grounded, so nothing is
hallucinated. The contract is neutral: it carries stable ids and structured
answers so it can be packaged, in later PRs, as an H5P Question Set, a SCORM 1.2
package, and xAPI statements without changing shape. See
[`docs/adr/0003`](docs/adr/0003-neutral-assessment-contract-and-grounding.md).

**Module B packaging** turns that `AssessmentSet` into an **H5P Question Set**
(`.h5p`) at `POST /assess/h5p` and `POST /assess/h5p/file`. Multiple-choice maps
to `H5P.MultiChoice`, fill-in-the-blank to `H5P.Blanks`, and match-the-pair to
`H5P.DragText` (H5P has no first-class matching type; Drag Text's gaps and
distractors express one cleanly). The rubric — per-question points, a mastery
threshold, and score bands — rides along, and LaTeX renders through H5P's
MathDisplay. The emitter is pure Python and writes the ZIP directly; see
[`docs/adr/0004`](docs/adr/0004-pure-python-h5p-packaging.md). The later modules
follow.

> **Prerequisite for import:** the target LMS must have the H5P content types
> installed (in Moodle: *Site administration → H5P → Manage H5P content types*),
> and MathDisplay enabled if your questions use LaTeX. The package declares its
> dependencies rather than bundling several MB of libraries into every file; the
> versions it targets are pinned in
> [`app/packaging/h5p/versions.py`](app/packaging/h5p/versions.py).

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
- `POST /ingest` — parse a document (PDF, PPTX, DOCX, CSV, TXT, Markdown, HTML) into structured JSON
- `POST /summarize` — derive insights from an already-parsed document
- `POST /summarize/file` — parse a document and derive insights in one call
- `POST /narrate` — derive a spoken narration script from an already-parsed document
- `POST /narrate/file` — parse a document and derive a narration script in one call
- `POST /assess` — generate a source-grounded assessment from an already-parsed document
- `POST /assess/file` — parse a document and generate an assessment in one call
- `POST /assess/h5p` — package an assessment as an H5P Question Set (`.h5p`)
- `POST /assess/h5p/file` — parse, generate and package in one call
- `GET /docs` — interactive API docs

## Test

```bash
pytest
```

## Configuration

All settings are environment variables prefixed `AI_ENGINE_` (or a local
`.env`). See [`.env.example`](.env.example) for the full list and defaults.
