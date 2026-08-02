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
`AssessmentSet` — multiple-choice, match-the-pair, fill-in-the-blank, and
short-answer questions — exposed at `POST /assess` and `POST /assess/file`. Every question is
verified against the source and dropped if it cannot be grounded, so nothing is
hallucinated. The contract is neutral: it carries stable ids and structured
answers so it can be packaged as an H5P Question Set and a SCORM 1.2 course —
both of which now ship — and, later, as xAPI statements, without changing shape. See
[`docs/adr/0003`](docs/adr/0003-neutral-assessment-contract-and-grounding.md).

**Module B packaging** turns that `AssessmentSet` into an **H5P Question Set**
(`.h5p`) at `POST /assess/h5p` and `POST /assess/h5p/file`. Multiple-choice maps
to `H5P.MultiChoice`, fill-in-the-blank to `H5P.Blanks`, and match-the-pair to
`H5P.DragText` (H5P has no first-class matching type; Drag Text's gaps and
distractors express one cleanly). The rubric — per-question points, a mastery
threshold, and score bands — rides along, and LaTeX renders through H5P's
MathDisplay. The emitter is pure Python and writes the ZIP directly; see
[`docs/adr/0004`](docs/adr/0004-pure-python-h5p-packaging.md).

**Teacher controls** over the packaged quiz, both asked for by the mentors.
`solution_visibility` decides when a learner may see the correct answers — `always`,
`after_submission`, or `never` — and `time_limit_seconds` puts a countdown on the
whole attempt. Both are fields on the `AssessmentSet`, so a caller running the
two-step flow can set them while reviewing the questions, and both are query
parameters on every route that generates, so the one-call routes can set them too.

They land differently in the two targets, because the targets differ. H5P Question
Set has three real fields for answer visibility and **no timer of any kind** — that
was verified against the shipped `semantics.json` of all seven content types rather
than assumed — so a time limit on an H5P package is reported as unsupported in the
warnings instead of being written to an invented key that the validator would drop
in silence. SCORM carries its own player, so both controls work there: the deadline
is stored as an instant in `cmi.suspend_data` and survives the learner closing the
tab, and running out of time reports `cmi.core.exit` as `time-out`.

**Short answers** are the one type a learner writes in their own words. Because a
packaged quiz runs inside the LMS with no model available, they are marked by a
**points-based mark scheme** — two to four key points, each detected by exact,
case-insensitive phrase matching, each phrase quoted from the source. That is
automated marking of a real assessment instrument, not an essay grader: it does not
judge reasoning or coherence, so a learner who is correct in entirely different
words scores zero, and the results screen shows them which points were found and
the full model answer. The limits and why they are unavoidable are set out in
[`docs/adr/0006`](docs/adr/0006-short-answer-questions.md).

> **Prerequisite for import:** the target LMS must have the H5P content types
> installed (in Moodle: *Site administration → H5P → Manage H5P content types*),
> and MathDisplay enabled if your questions use LaTeX. The package declares its
> dependencies rather than bundling several MB of libraries into every file; the
> versions it targets are pinned in
> [`app/packaging/h5p/versions.py`](app/packaging/h5p/versions.py).

The same assessment also packages as a **SCORM 1.2** course at `POST /assess/scorm`
and `POST /assess/scorm/file`, importable into Moodle 4.x and Open edX. Where an
H5P package ships content and the LMS supplies the player, a SCORM package **ships
its own player** and the LMS supplies only a JavaScript API to report through — so
it needs no prerequisites at all, and it honours per-question `points` exactly
(H5P has no per-question weight and scores on its own scale). Multiple-choice,
match-the-pair and fill-in-the-blank are reported as `cmi.interactions`, which
Moodle surfaces in its Interactions report. LaTeX is shown as source rather than
typeset — a SCORM package would have to carry its own maths renderer, which is
tracked separately. See
[`docs/adr/0005`](docs/adr/0005-scorm-12-packaging.md).

**Module C** (multimedia intelligence) starts where a document ends: a recording.
`POST /transcribe` turns an audio or video upload into a **time-aligned
transcript**, rendered as WebVTT or SRT subtitles or plain text, through the same
OpenAI-compatible contract the text models use — the audio endpoint is
`/audio/transcriptions`, which Groq serves with `whisper-large-v3` and a local
`faster-whisper` serves identically. See
[`docs/adr/0007`](docs/adr/0007-transcription-provider-strategy.md).

`POST /chapter` divides that transcript into **titled, timed chapters**. The
division is deterministic — Python walks the transcript's own segment timings and
breaks at a natural pause once a chapter has run long enough — and the model only
writes the headings, because a model asked for timestamps invents them. See
[`docs/adr/0008`](docs/adr/0008-auto-chaptering.md).

`POST /interactive-video` composes the two into an **`H5P.InteractiveVideo`**: the
chapters become marks on the player's navigation bar, and each one ends with a
knowledge check that pauses the video. The questions come from the *existing*
assessment pipeline — every chapter is handed to it as its own small document — so
the grounding gate and the question-to-H5P mapping are shared rather than written
twice. The media is referenced by URL rather than bundled, which is what H5P's own
published content does and what keeps a lecture recording under an LMS upload
limit. `POST /interactive-video/file` runs the whole chain from one upload. See
[`docs/adr/0009`](docs/adr/0009-interactive-video-packaging.md).

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
- `POST /assess/scorm` — package an assessment as a SCORM 1.2 course (`.zip`)
- `POST /assess/scorm/file` — parse, generate and package as SCORM in one call
- `POST /transcribe` — transcribe an audio or video upload (JSON, WebVTT, SRT or plain text)
- `POST /chapter` — divide a transcript into titled, timed chapters
- `POST /chapter/file` — transcribe a recording and chapter it in one call
- `POST /interactive-video` — package a chaptered transcript as an H5P Interactive Video (`.h5p`)
- `POST /interactive-video/file` — transcribe, chapter, generate checks and package in one call
- `GET /` — service banner
- `GET /docs` — interactive API docs

## Test

```bash
pytest
```

## Configuration

All settings are environment variables prefixed `AI_ENGINE_` (or a local
`.env`). [`.env.example`](.env.example) carries the ones you are expected to set;
`app/config.py` is the complete list, and every field there is overridable with the
same `AI_ENGINE_` prefix.
