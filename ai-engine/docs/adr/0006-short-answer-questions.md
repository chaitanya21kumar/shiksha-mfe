# ADR 0006 — Short-answer questions, and how they are marked

- **Status:** Accepted
- **Date:** 2026-07-19
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Requested by the mentors at the 2026-07-17 weekly sync. Extends
  [ADR-0003](0003-neutral-assessment-contract-and-grounding.md) (contract and grounding),
  [ADR-0004](0004-pure-python-h5p-packaging.md) (H5P) and [ADR-0005](0005-scorm-12-packaging.md) (SCORM).

## Context

Issue #7's Module B lists three question types, all of them objective: multiple
choice, match-the-pair, fill-in-the-blank. After the Week 6 demo the mentors asked
for **subjective / comprehension-type questions** as well — questions a learner
answers in their own words.

Generating such a question is easy. Marking one is not, and one constraint decides
the whole design:

> **A packaged quiz runs inside the LMS, offline. There is no model there.**

An H5P package is rendered by the LMS's own JavaScript; a SCORM package carries
its own player and is handed only an API to report a score through. Neither can
call out to a language model when a learner presses Check. So an LLM can build the
mark scheme at *generation* time, where we do have a model — but nothing can
"judge" an answer at *marking* time. Whatever marks the answer must be
deterministic and local.

## Decision

**1. The type is `short_answer`.** Not "essay": we do not grade essays, and the
name would promise something the marking cannot do. Not "subjective": the marking
is entirely objective — it is the *response format* that is constructed rather
than selected. `short_answer` is the standard assessment term for a constructed
response of a sentence or two marked against a prescriptive scheme.

**2. The instrument is a points-based mark scheme.** Each question carries two to
four **key points**; each key point carries the phrases that count as having made
it; each is worth one mark. This is not an invention — it is what exam boards call
a points-based scheme, used wherever a salient point corresponds to a mark. What
we automate is the detection, not the judgement.

**3. Marking is exact, case-insensitive, word-isolated phrase matching**, ported
in `app/assessment/grading.py` from `H5P.Essay 1.5.13`'s own matcher. Three
implementations exist by necessity — Python, our SCORM player's JavaScript, and
H5P's library — and they must agree, or the same learner gets a different result
depending on which format their LMS imported.

**4. The mark scheme is grounded like everything else.** ADR-0003 protects against
a fabricated *source*; it does not by itself protect against a fabricated *rubric*,
because a genuinely quoted sentence can be paired with a key point it does not
support. So the gate has three stages: the evidence quote must appear in the
document; every accepted phrase must be a run of words from that quote; and the
model answer must score full marks against our own grader. A scheme its own answer
cannot satisfy is incoherent, and a learner who wrote exactly that answer would be
marked down by it.

**5. The mark is final and counts toward the score.** This is forced, not chosen.
H5P.Essay's `ignoreScoring: true` mode does not mean "record but do not score" — it
makes `getScore()` return `getMaxScore()`, i.e. **free full marks**, and
`isPassed()` unconditionally true. SCORM 1.2 has no help either: its writable
`lesson_status` vocabulary is `passed, completed, failed, incomplete, browsed`,
with no "needs grading" member. So a package containing a deliberately unmarked
item could only report `completed`, which in a Moodle gradebook is
indistinguishable from a finished quiz with no score. Between "counts, with a
documented limit" and "silently awards full marks", the first is the honest option.

## What this explicitly does not claim

The two failure modes are real, symmetric, and not fixed by this design. Both are
asserted in `tests/test_assessment_grading.py` so that a future "improvement" which
quietly changes the marking has to fail a test first.

**A learner who writes the right words in a nonsense order scores full marks.**
`"land heats sea to land reverses"` scores 3/3. Nothing in the algorithm inspects
ordering, syntax or coherence. `min_chars` raises the cost of doing this; it cannot
detect it, and saying otherwise would be false.

**A learner who is correct in entirely different words scores zero.** `"The ground
warms quicker than the water, pulling damp air inland"` is a good answer and scores
0/3. Recall comes from the accepted variants and is finite.

The mitigation for both is disclosure rather than cleverness: after submitting, the
learner is shown **which key points were found, which were missed, and the full
model answer**. Marking a learner cannot inspect is not marking they can learn
from — and someone who was right in their own words can at least see why they
scored what they did.

## Consequences

- **The engine's no-hallucination guarantee holds for the new type**, and the
  grounding is stricter here than for the objective types: every phrase that can
  earn a mark is a run of words from a verified quote of the tenant's own material.
- **The two packages agree by construction.** The contract pins `points` to the sum
  of the key point weights, which is also H5P's `getMaxScore()` and our SCORM
  denominator — one number, three consumers. This is the only question type where
  H5P's scale and ours agree exactly, so it never triggers the scale warning the
  other three can.
- **One new H5P library.** `H5P.Essay 1.5` was already on the installed Question
  Set's whitelist, and its dependencies were already in our closure.
- **Trade-off — LaTeX short answers are dropped from the H5P package**, with a
  warning. The learner answers in a plain textarea: there is nothing for MathDisplay
  to typeset and no way to type LaTeX into it. They still ship in SCORM, where the
  source renders as written per ADR-0005.
- **Trade-off — a learner who skips H5P's Check button contributes 0** to the set
  total, because `showSolutions()` in 1.5.13 shows the sample without grading.
  Version-specific, not fixable by any parameter, and an argument for the version
  pin `versions.py` already provides.

## Deliberately deferred

- **Typo tolerance.** H5P.Essay's `forgiveMistakes` works, but porting its sliding
  Levenshtein window bit-exactly into our player is precisely the near-miss that
  would score the same answer differently in the two packages. Off in both.
- **Wildcards.** Rejected by the contract rather than merely unused: H5P's wildcard
  character class covers Latin, Greek, Cyrillic, kana, CJK and Thai but **not**
  Devanagari or the other Indic scripts. On a multi-tenant Indian LMS one would
  work in English content and silently fail in Hindi, which is worse than not
  having the feature.
- **Server-side LLM evaluation.** Our API does have a model, so a "provisional
  score plus feedback" endpoint is buildable — but neither package could consume
  it, and neither LMS gives a teacher a reliable view of the learner's free text
  (Open edX discards interactions entirely). Building a review workflow with no
  last mile would be shipping a shape rather than a feature. If mentors want
  assisted marking, that is the conversation to have first.
- **Marks above one per key point.** `weight` accepts 1–5 so the contract needs no
  migration later; the pipeline always assigns 1.

## Notes

- `"short_answer"` is accepted by `question_types` on all six assessment endpoints
  and is generated by default alongside the other three.
- Verified end to end: the `.h5p` renders against the real `H5P.Essay` library and
  **its own grader agrees with ours on all eight test cases**, including the
  whitespace quirk and both failure modes; the SCORM package runs in a fake LMS
  enforcing Moodle's own regexes with **44 calls and zero rejections**, reporting
  partial credit correctly.
- Open question for the mentors, raised on the PR: whether a short answer should
  count toward the score at all. Decision 5 explains why the platforms leave little
  choice, but the pedagogical call is theirs.
