# ADR 0013 — One course from one source: partial success, and a self-describing bundle

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7);
  the milestone doc's Week 11 ("Integration & Publishing — integrate all modules into
  a unified workflow; publishing pipeline setup. Checkpoint: end-to-end workflow
  functional").

## Context

Modules A to D each answer one question well, and each is reachable on its own. A
teacher does not have four questions. They have a file, and a lesson to run on
Thursday.

Building a course today means five HTTP calls in a specific order, each feeding the
next, with the caller re-implementing the ordering and the error handling every
time. Module B already showed what that costs: the teacher controls worked in the
pipeline and were unreachable over HTTP for a fortnight, because the capability
existed but only along one path. A workflow that only exists as instructions is a
workflow half the callers will get wrong.

Two things then have to be decided, and neither is obvious.

**What happens when one stage of four fails.** This is not an edge case. A page of
photographs supports no grounded question; a document of headings supports no
lesson; a model is briefly rate-limited. Any of those can happen while the other
three stages are perfectly fine.

**What "publishing" produces.** Five packages across three formats, plus the data
behind them, is not something to hand over as five downloads.

## Decision

**A stage that fails does not fail the course.** Every stage is attempted, and each
reports for itself. The course carries whatever was produced and a `stages` list
saying what is absent and on whose account. A caller must never infer success from
a 200; the stage list is the answer, and it is never empty.

**Three outcomes, not two.** `produced`, `skipped` and `failed` stay distinct,
because "you did not ask for it" and "you asked and it could not be done" are
different facts and a teacher acts differently on each. Where a stage is impossible
for structural reasons — insights from pasted text, which has no parsed structure —
the report says *that*, not "not requested", which would imply a choice they do not
have.

**`Exception`, not a named set.** The narrow set of known errors is knowable today
and will not stay knowable: every stage reaches a model, a parser and a template.
`BaseException` is excluded so a cancellation still stops the build. Every catch
logs with its traceback and reports without it — the caller needs an action, we need
to know whether it was our bug, and a traceback serves neither.

**Publishing is one archive, and the manifest is the point.** A folder of files can
tell a teacher what succeeded; it cannot tell them what was attempted and did not,
which is the question actually asked later, usually by someone who was not there.
So the bundle carries `manifest.json` with every stage report, and a plain-text
README saying the same thing for whoever double-clicks it.

**Packaging is attempted per artefact.** A lesson can package as H5P and fail as
SCORM. That is one bad emitter, not a bad course, so each emission stands alone and
whatever succeeded still ships — named in the manifest, and surfaced in the response
headers so a caller learns of it without unzipping.

**Two shapes, as everywhere else.** `/course/…` returns data a teacher can read and
change; `/course/bundle/…` returns the archive. The bundle route accepts a course
object as well as a file, which is what makes "generate, fix a heading, publish" a
supported path rather than an accident.

## Consequences

- **The workflow exists once.** Ordering and failure handling live in one place
  instead of in every caller, which is the whole point of the week.
- **A 200 no longer means "everything worked".** That is a real contract change and
  it is deliberate; the alternative is a 500 that throws away three good artefacts.
  It is stated in the router's docstring, the schema's, and the README.
- **The orchestrator owns no generation logic.** Each stage is the module's own
  entry point, called as its own router would call it. A second implementation of
  "how a lesson is built" living one layer up is exactly the drift ADR-0011 warns
  about.
- **The bundle is reproducible**, by the same fixed timestamp and sorted archive
  order the H5P and SCORM writers use — which is what lets a test assert on a bundle
  rather than merely inspect one.
- **Insights, narration and assessment need a parsed document.** Transcript and text
  sources therefore produce a lesson and an honest explanation of the rest, rather
  than silently less.

## Notes

- `short_answer` is deliberately out of the default question mix: it is the slowest
  to generate and the likeliest to find nothing groundable, and a default should be
  the path that works. A caller who wants it asks for it.
- The course reuses the id its source already carries rather than minting a second
  one, so an upload has one identity across every artefact built from it.
- Verified end to end on a real document: all four stages produced, and the bundle
  contained all five packages plus the data behind them.
