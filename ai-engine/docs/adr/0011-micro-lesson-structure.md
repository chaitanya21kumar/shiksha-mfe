# ADR 0011 — Micro-lessons: one shared section type, and a structure the model cannot change

- **Status:** Accepted
- **Date:** 2026-08-02
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7),
  Module D ("micro-learning modules generated from a document, a transcript, or
  free-form input"); the milestone doc's Week 9 ("Source selector, content
  extraction, script generation"). Applies the structure rule established in
  [ADR-0008](0008-auto-chaptering.md) to a third pipeline.

## Context

Module D turns a source into a short lesson: a handful of steps a learner works
through, each with a heading, a few on-screen points and what a teacher would say
over them. Issue #7 names three sources — a document, a transcript, free-form
input — and the milestone doc splits the week into a source selector, extraction
and script generation.

Two decisions had to be made before any of that could be written.

**Where the three sources converge.** Modules A, C and D each need "the teachable
units of this thing, in order". Module A's narration pipeline already had that
logic, written against `ParsedDocument` and living inside the narration module. A
second copy for lessons would have been the obvious move and the wrong one: the
two would drift, and a fix to page-splitting would land in one and not the other.

**Who decides how many steps there are.** A language model asked to "turn this
into a lesson" will happily decide the lesson has six steps this run and eight the
next, merging two sections it finds similar and splitting one it finds long. That
is the same non-determinism ADR-0008 rejected for chapter boundaries, and it costs
more here: a step is a slide in an H5P Course Presentation and a SCO's navigation
position in SCORM, so a step count that moves between runs means a package whose
shape moves between runs, and nothing downstream can be asserted in a test.

## Decision

**1. Sectioning is shared, in `app/ingestion/sections.py`.** The `Section` type
(`source_index`, `title`, `text`) and the document splitter are promoted out of
the narration module into ingestion, where both callers can reach them. There is
one implementation of "split this document into teachable units", so a fix reaches
every module that extracts them. The bounds helper — cap by count, cap by
characters, and say in the warnings what was left out — moves with it, because
silently truncating a source is the failure mode all of these share.

**2. All three sources produce that same `Section` list.** A document splits at
its headings, a transcript at the chapters Module C already computed, free-form
text at its blank lines. Everything after the extractor is identical no matter
what the caller uploaded, which is what makes the selector a selector rather than
three parallel pipelines. The transcript path inherits structure from where the
speaker actually paused, because ADR-0008 computed it from the timings.

**3. Extraction is deterministic; only the words are generated.** The number of
steps, their order and which unit each came from are a pure function of the
source. The model is given the sections already numbered and asked to write inside
them. A step for a section number that does not exist is discarded, with a
warning: a step with nothing behind it is one nobody can check. A section the
model returns nothing for falls back to its own source text rather than
disappearing, also with a warning. The lesson has exactly as many steps as the
source had usable units, always — that invariant is asserted directly against an
empty reply, a short reply, an exact reply and an over-long one.

**4. The author's heading wins over the model's.** If a section is called
"Evaporation", the step is called "Evaporation" — not "Evaporation Process". This
one came out of running the generator on ordinary notes and noticing the titles
had quietly changed. Retitling a section its author already named is a change
nobody asked for, and it breaks the match between the lesson and the document it
came from. The model's heading is the fallback for a section that had none.

**5. Pasted text honours both heading conventions.** A heading on the first line
of a block, and a heading standing alone with a blank line after it. Handling only
the first loses every standalone heading, because a lone word is below the
minimum length for a step — so the author's section titles vanish and the model
invents replacements. Also found by running it on real notes rather than fixtures.

**6. `source_index` on every step.** It is what lets a reviewer ask "where did
this come from" and get an answer, and it is what a later packaging step will use
to put a step back next to its slide. Not decoration.

## Consequences

Good:

- One sectioning implementation for narration and lessons, so they cannot drift.
- The same source always yields the same number of steps, in the same order, so
  the packaging targets in the rest of Module D have a stable shape to build on.
- Every step traces to a unit of source, and every departure from the source —
  a capped section, a skipped step, a discarded one — is in `warnings` rather
  than being silent.
- The three entry points share one code path after extraction, so a fix to
  assembly, bounds or fallbacks reaches all of them at once.

Costs and limits:

- Blank-line splitting is a convention, not parsing. Text written as one wall
  stays one step. That is deliberate — guessing at internal boundaries would put
  the structure back in the hands of a heuristic nobody can predict — but it means
  a badly formatted paste produces a one-step lesson rather than an error.
- The heading heuristic for pasted text (short, no sentence-ending punctuation)
  will occasionally take a short sentence for a heading. It costs a title, not a
  step, and the text is unchanged either way.
- A lesson can be at most `MAX_STEPS` (40) steps, matching the section cap the
  other pipelines use. A longer source is capped with a warning rather than
  rejected.

## Alternatives considered

**Let the model decide the structure.** Rejected for the reason above and in
ADR-0008: the output shape stops being testable, and every downstream package
inherits the drift.

**Copy the narration sectioning into Module D.** Faster to write, and the copies
would have diverged the first time either was fixed. Promoting the shared code was
a slightly larger change now against a class of bug that is hard to notice later.

**One endpoint with a `source_kind` flag.** A caller uploading a PDF and a caller
pasting notes want genuinely different request bodies; collapsing them gives a
body where most fields are always unused, and an OpenAPI schema that documents
none of the three cases properly. Four routes, each with its own shape.

**Generate the lesson title too.** The lesson takes the caller's title, else the
source's own first heading, and only falls back to the filename. A document's own
title is better than an invented one, and a caller who names their lesson should
get that name back.
