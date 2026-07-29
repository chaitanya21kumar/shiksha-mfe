# ADR 0008 — Auto-chaptering, and why the boundaries are not generated

- **Status:** Accepted
- **Date:** 2026-07-29
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7),
  Module C ("auto-generated timestamped chapter markers"); the milestone doc's Week 8
  ("Auto-Chaptering"). Consumes the transcript from [ADR-0007](0007-transcription-provider-strategy.md).

## Context

Module C.1 produces a `Transcript` — timed segments of speech. C.2 turns that into
chapters: a handful of titled spans that give a recording a navigable structure.
Those spans become the bookmarks in an H5P Interactive Video's navigation bar
(C.3), so a chapter's start time is not a label, it is a seek target.

The obvious implementation is to ask a language model to divide the transcript.
That is also the wrong one, for the same reason it was wrong in narration and in
assessment: it makes the *structure* of the output non-deterministic. The number
of chapters, where each begins, and which segments belong to it would then vary
between runs on identical input, could not be asserted in a test, and — because
the model would be quoting timestamps back — could drift from the real media
positions. A bookmark that seeks to the wrong place is worse than no bookmark.

## Decision

**1. The division is computed in Python; only the titles are generated.** The
boundaries, the chapter count and segment membership are a pure function of the
transcript, so they are exactly testable. The model is asked for one thing it is
genuinely good at and that carries no correctness risk: a short heading.

**2. Chapters break at natural pauses, past a target length.** Speech has gaps
between segments; a chapter that ends at one lines up with how a person would
divide the recording. A chapter accumulates until it reaches the target
(90 seconds), then ends at the next gap of at least 0.6 s.

**3. An overshoot ceiling ends a chapter when no pause arrives.** Continuous
speech — a fast speaker, or a transcript whose segments are tightly packed — would
otherwise produce one chapter covering the whole recording. At 1.6× the target the
chapter ends regardless. This is the single most important guard here: without it,
the feature silently degrades to nothing on exactly the content that needs it most.

**4. The target stretches so the chapter cap holds by construction.** Rather than
generating hundreds of chapters for a long recording and trimming afterwards, the
target is raised to `total / 24` when that is larger. A four-hour lecture gets
proportionally longer chapters and never exceeds the cap — which also bounds the
single titling call.

**5. A short trailing chapter is folded into the one before it.** The final span
is whatever is left over, so it is the only one that can come out very short. A
five-second chapter at the end of a lecture is a stub in the navigation bar, not a
chapter.

**6. The contract refuses overlapping or out-of-order chapters.** `ChapteredTranscript`
validates that indexes run `1..n` and that no chapter starts before the previous one
ended. Both faults produce perfectly well-formed JSON and a player that seeks the
learner to the wrong place, so they are rejected at the boundary rather than trusted.

## Consequences

- **The structure is reproducible.** The same transcript always yields the same
  chapters, so `tests/test_chaptering_pipeline.py` asserts exact segment membership,
  exact break positions and exact counts — not "roughly this many".
- **A failed titling call degrades, it does not fail the request.** Unusable model
  output leaves every chapter with a `Chapter N` default and a recorded warning; the
  chapters themselves are unaffected because they never depended on the model.
- **Titles are matched back by number**, so a chapter the model skips gets the default
  rather than silently shifting every later title onto the wrong span.
- **The chapter carries its own text**, so C.3 can generate a knowledge check for a
  chapter without re-joining segments, and Module D can reuse a chapter directly.
- **Trade-off — the boundaries are acoustic, not semantic.** A pause is a good proxy
  for a topic change but it is not the same thing: a speaker who pauses mid-topic
  gets a break there, and one who changes topic without pausing does not. Titling
  makes this visible to the learner rather than hiding it. Semantic segmentation is
  a real improvement and is deliberately deferred — it would trade the determinism
  above for accuracy, and that is a decision to take with evidence, not by default.

## Deliberately deferred

- **Semantic (topic-shift) boundaries.** See the trade-off above. If adopted, the
  right shape is to keep the deterministic split as the fallback and let the model
  *adjust* boundaries within a bounded window, so the structure stays testable.
- **Per-chapter summaries.** The contract has room for them; nothing consumes one yet,
  and generating text no consumer reads is cost without benefit.
- **Speaker-aware chapters.** Depends on diarisation, which ADR-0007 defers.

## Notes

- Endpoints: `POST /chapter` (a `Transcript`) and `POST /chapter/file` (a media upload,
  transcribed and chaptered in one call).
- Constants live at the top of `app/chaptering/pipeline.py` so the behaviour can be
  retuned in one place; each carries the reason it exists.
