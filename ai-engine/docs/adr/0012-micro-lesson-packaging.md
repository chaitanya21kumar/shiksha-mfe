# ADR 0012 — Packaging a micro-lesson: three targets, one renderer, and what each can honestly report

- **Status:** Accepted
- **Date:** 2026-08-13
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7),
  Module D — "Output formats: H5P Course Presentation, HTML5 slide deck, SCORM 1.2 package";
  the milestone doc's Week 10 ("H5P & SCORM Generation Pipeline"). Consumes the
  `MicroLesson` contract from [ADR-0011](0011-micro-lesson-structure.md).

## Context

Week 9 produced a `MicroLesson`: an ordered list of steps, each with a heading, the
points that go on screen, and the notes a teacher would say over them. It exists
only as data inside the engine. Nobody can open it and no LMS can import it.

Issue #7 names three output formats. They are not three renderings of the same
requirement — each answers a different question, and the differences drive
everything below:

| target | the question it answers |
|---|---|
| H5P Course Presentation | what does a Moodle or Sunbird teacher already know how to use |
| HTML5 slide deck | what works when there is no LMS, and no internet |
| SCORM 1.2 | what tells a gradebook that a learner did this |

## Decision

**1. One renderer serves HTML5 and SCORM.** `emit/deck.py` builds the whole deck;
the HTML5 target is that and nothing else, and the SCORM target is that plus a
reporting layer injected through two seams. Two renderers would look identical the
week they were written and drift within a month, and a teacher who downloaded both
would find the same lesson looking different in each. SCORM reaches the deck
through exactly one hook — `window.LessonDeck.onSlide` — so the presentation code
is byte-identical in both.

H5P is deliberately *not* folded into that sharing. Its slides are data in a JSON
file that someone else's player renders; there is no markup of ours involved. A
shared abstraction over "our HTML" and "H5P's content model" would be an
abstraction over two things that have nothing in common.

**2. Everything in the HTML5 deck is inlined — no exceptions.** No stylesheet, no
script, no font, no image fetched from anywhere. This is a requirement rather than
a nicety: the file has to open on a machine with no internet, and the SCORM version
is served by an LMS from its own origin, where a request out to a CDN is both a
privacy leak and a thing that breaks the moment a school's network filters it. Two
tests guard it — one scans every `src`/`href`, and one refuses any `http`, `https`
or `url(` anywhere in the document, which catches a CSS background the attribute
scan would miss.

**3. The teacher's notes go in Course Presentation's Comments field, and the flag
that makes them reachable is misnamed.** Course Presentation has **no
speaker-notes field** — no `notes`, no `presenterNotes`, nothing of the kind in its
semantics. The nearest real field is an element's `solution`, labelled *"Comments —
shown when the user displays the suggested answers for all slides"*, which the
runtime turns into a button that opens the text in a popup.

The trap is the accompanying flag. `alwaysDisplayComments` does not mean "show the
comment text"; it is the **only** thing that builds the button at all:

```js
void 0 !== e.alwaysDisplayComments && e.alwaysDisplayComments && t.showCPComments()
```

The first implementation set it to `false`, reasoning that a button the learner
clicks is better than a popup that opens itself. The tests passed, the package
imported, the slides rendered — and the notes were unreachable, because the only
other callers are on the show-solutions path, which a lesson never offers (there
are no questions, and the summary slide that carries the button is switched off).
Found by opening the finished package in a real H5P player and counting the buttons
on the slide: zero. It is now `true`, which renders the button; the popup still
only opens on click.

The cost, stated rather than hidden: these are Comments, so H5P's global
show-solutions action would reveal them together. For a lesson with no questions
that is harmless.

**4. No score is ever written to SCORM.** A lesson asks nothing, so there is
nothing to score. Writing `cmi.core.score.raw` of 0 out of 0 is not "no score", it
is a zero, and more than one LMS renders that as a failed attempt. Completion means
the last slide was reached — the only signal the content actually carries. Time is
not used as a proxy, because a tab left open is not a lesson read. `lesson_status`
moves `incomplete` → `completed` and never touches `passed` or `failed`, because
nothing here is being judged.

`adlcp:masteryscore` stays absent for the reason [ADR-0005](0005-scorm-12-packaging.md)
records, and it matters more here than it did there: with no score at all, an LMS
deriving pass/fail from a mastery threshold would be inventing a verdict out of
nothing.

**5. Every format gets both a review-first route and a one-call route.** `/…/h5p`
takes a `MicroLesson`, so a caller can generate it, read it, fix a heading and
*then* package. `/…/h5p/file` goes from upload to package in one call, because that
is the flow a teacher actually uses. Offering only the first is what left Module B's
teacher controls unreachable for half its callers, and that mistake is not worth
repeating in a new module.

**6. `l10n` is not emitted, and that is a checked decision.** Module C writes out
all 47 of Interactive Video's interface strings because that player defaults only
35, and the twelve it misses are exactly the ones on its submit path. The same check
against Course Presentation gives the opposite answer: its runtime extends a literal
of **52** keys over the supplied `l10n`, against **49** declared in its semantics —
every declared string is defaulted. Copying Module C's approach without re-running
the check would have shipped 49 keys of dead weight as a fix for a problem this
library does not have.

## Consequences

Good:

- One lesson reaches a learner three ways, and the two that share a renderer
  cannot diverge.
- The HTML5 file is genuinely portable — one file, no network, prints as a handout.
- What SCORM reports is narrow and true, so a gradebook is never shown an invented
  score or an invented verdict.
- Every claim about the H5P format was read out of the package the Hub serves, and
  the resulting artefacts were opened in a real player and a strict fake LMS before
  any of this was committed.

Costs and limits:

- **The notes are Comments, not notes.** A global show-solutions action would
  reveal them together. Harmless for a lesson, but it is not what the field is for.
- **A slide is text only.** Course Presentation accepts 22 element libraries and
  this emitter uses one, because a `MicroLesson` carries prose and nothing else.
  Images and embedded questions are a later decision, not an oversight.
- **Layout is fixed** — a title band and a body band, in percentages. A generated
  lesson has no basis for inventing per-slide geometry, and a wrong guess is worse
  than a consistent one.
- **SCORM completion is coarse.** Reaching the last slide is the honest signal
  available; it does not mean the learner read anything.

## Alternatives considered

**Put the notes on the slide as visible text.** Simpler, and it defeats the point
of a slide. Two to four sentences of narration under every set of bullets turns
each slide into the wall of text ADR-0011 was written to avoid.

**Invent a `notes` field in the H5P content.** The worst option available, and the
tempting one: it would import cleanly and look correct, because `H5PContentValidator`
drops keys it does not recognise **without raising**. The notes would simply never
appear, and nobody would find out until a teacher went looking.

**Ship the HTML5 deck as a ZIP with separate CSS and JS.** Tidier as source, worse
as a deliverable. The value of this format is that it is *one file* a teacher can
mail to themselves; an archive to unpack removes exactly that.

**Report elapsed time as progress in SCORM.** Rejected: it measures a tab being
open, not a lesson being read, and reporting it as progress would make the
gradebook confidently wrong.
