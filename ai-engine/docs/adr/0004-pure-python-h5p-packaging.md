# ADR 0004 — Packaging assessments as H5P, in pure Python

- **Status:** Accepted
- **Date:** 2026-07-17
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7), Module B; consumes the contract from [ADR-0003](0003-neutral-assessment-contract-and-grounding.md)

## Context

ADR-0003 deliberately kept `AssessmentSet` neutral so that each packaging target
could map *from* it. This is the first of those targets: issue #7 requires every
question type to be **packaged as a valid H5P Question Set**, with **LaTeX
rendered via MathJax**, and the weekly plan adds **rubrics**.

`.h5p` is an unforgiving format. Almost every way of getting it wrong produces a
package that imports cleanly and then misbehaves in front of a learner, so the
decisions below were taken against primary sources — H5P's own validator
(`h5p-php-library/h5p.classes.php`), the `semantics.json` of each library, and
the package the H5P Hub actually serves — rather than from documentation prose.

## Decision

**1. A shared `app/packaging/` layer, not H5P code inside `app/assessment/`.**
Issue #7 has Module C emitting H5P Interactive Video and Module D emitting H5P
Course Presentation, HTML5 and SCORM. Three modules need this. Format knowledge
lives in `app/packaging/h5p/` (versions, manifest, subcontent, ZIP) and knows
nothing about assessments; the domain mapping lives in `app/assessment/emit/`.
Module D will map its own content type through the same primitives instead of
importing from Module B or duplicating it.

**2. Pure Python (`zipfile` + `json`), not `h5p-nodejs-library`.** Issue #7's
implementation notes name that library for the Assessment Service, but it is a
full H5P *platform* — storage, editor, user state — and it is Node, where this
engine is Python. What we need is to write a valid ZIP. Adding a second runtime
to the deployment for that is not a trade worth making, and ADR-0001 commits us
to a self-contained service. **This is a deliberate deviation from the stack named
in the ticket, and mentors should know it.**

**3. Reference the H5P libraries; do not bundle them.** A content-only package
(`h5p.json` + `content/content.json`) imports as long as the host has the
declared libraries, which is the normal Moodle setup step. Bundling would mean
vendoring 12 libraries and several MB into this repo and re-shipping them forever.

The cost of referencing is a real coupling: `questions[].library` is matched by
**exact string equality** against a whitelist baked into the *installed* Question
Set's `semantics.json`. So the versions are pinned to what the **Hub serves**,
because that is where an LMS installs its content types from — not to GitHub
master, which is routinely ahead of it. When this was pinned, master's Question
Set was 1.21 and the Hub's was 1.20; emitting master's version would have
declared a dependency no real Moodle has. All of it lives in one constants table
(`app/packaging/h5p/versions.py`) so re-pinning a tenant is a one-line change.

**4. `MatchItem` → `H5P.DragText`.** H5P has no first-class match-the-pair type.
Drag Text renders a text with `*gaps*` and shuffled draggables, so one line per
pair (`Term — *definition*`) *is* matching, and its `distractors` field gives our
unmatched targets a real home. `H5P.DragQuestion` is true drag-and-drop but its
drop zones need pixel geometry, which a document-derived question has no basis to
invent; decomposing into one MCQ per term would change the question count and
make `counts` lie.

**5. The rubric is points + mastery threshold + score bands.** Issue #7 does not
define "rubric", and every question type here is auto-graded, so we read it as the
scoring scheme. `points` and `pass_percentage` already existed; this adds
`score_bands` (a contiguous 0–100 tiling → feedback), which is exactly H5P's
`endGame.overallFeedback`, and makes `pass_percentage` settable on `/assess`.
Bands are derived in Python, not generated: their text is not drawn from the
source document, so the grounding gate could never verify it.

**6. LaTeX needs no emitter branch.** `H5P.MathDisplay` is `runnable: 0` with an
`addTo` regex; H5P core injects it into any content whose text matches. Declaring
it as a dependency is wrong. Its trigger regex is not DOTALL, so we assert instead
that math never spans a newline — otherwise it silently renders as raw LaTeX.

**7. Everything the model wrote is HTML-escaped.** H5P injects these fields as
HTML (Drag Text does `span.innerHTML = text`; tips go through jQuery `.html()`),
and the text originates in a tenant's uploaded document — an unescaped stem is a
script-injection path into their LMS. Escaping round-trips: `innerHTML` decodes
for display, Blanks' `parseSolution` decodes before grading, and Drag Text
compares draggable and solution with both sides escaped.

**8. Unrepresentable questions are dropped and reported, never mangled.**
H5P.Blanks and H5P.DragText carry answers inside `*…*` in a plain string and
**neither parser has an escape mechanism**: a `/` splits alternatives, a `:`
starts a tip, a stray `*` re-pairs the gaps. A ratio (`3:4`) or a unit (`m/s`) is
enough. Rather than emit a package that grades wrongly, we drop the question and
say so in `warnings` — the same discipline ADR-0003 uses for ungrounded questions.

## Consequences

- **Module C and Module D inherit the packaging layer** rather than re-deriving
  the H5P format, and neither has to depend on Module B.
- **One runtime.** No Node in the image; the emitter is stdlib-only.
- **Byte-reproducible packages.** Deterministic `uuid5` subcontent ids plus fixed
  ZIP timestamps mean the same assessment always emits the same bytes, so tests
  assert on the artifact instead of re-implementing the emitter.
- **A tenant prerequisite, documented rather than hidden:** the target LMS must
  have the H5P content types installed, and MathDisplay enabled for LaTeX. A
  content-only package cannot ship either.
- **Playing a package is not the same as importing one, and only the second is
  the real test.** A JS player renders `content.json` as written; Moodle first
  runs H5P's *PHP* validator over it, which rewrites fields. Anything declared
  without `tags` in its semantics goes through `htmlspecialchars`, so markup in
  such a field reaches the learner as literal characters — and no amount of
  browser testing reveals it. Before changing what we emit, run the output
  through the PHP validator, not just the player.
- **Semantics defaults are not runtime defaults.** The H5P editor applies a
  field's semantics default; a machine-written `content.json` never goes through
  the editor. So any field a library's JS *reads* without defaulting itself must
  be emitted explicitly, or it resolves to `undefined` in front of a learner.
  That is why `behaviour.type`, `singlePoint`, `randomAnswers`, `caseSensitive`
  and the MultiChoice `UI` labels are all written out even where they look
  redundant.
- **Residual risk:** a tenant running an older Question Set has a different
  whitelist, and the package would be rejected. That is what the constants table
  absorbs, and it is the trigger to revisit bundling — the counter-argument is
  recorded here deliberately.
- **Trade-off — H5P scores on its own scale.** H5P has no per-question weight
  (`params.weight` is absent from semantics and gets stripped), so it counts one
  mark per choice question and one per blank or pair. `max_points` stays
  authoritative for SCORM and xAPI; when the totals diverge the emitter warns.
  Mastery is unaffected because `pass_percentage` is a percentage.
- **Trade-off — the Question Set UI strings are English.** `language` reaches the
  manifest, but H5P's own button labels here are not localised. Worth revisiting
  when a non-English tenant is real.

## Notes

- Endpoints: `POST /assess/h5p` (an `AssessmentSet`) and `POST /assess/h5p/file`
  (an upload, parsed and generated in one call). The body is a file, so dropped
  questions are reported in the `X-Package-Warnings` header.
- Versions pinned from the Hub on 2026-07-17: Question Set 1.20, MultiChoice
  1.16, Blanks 1.14, DragText 1.10; transitive closure of 12 libraries, editor
  dependencies excluded (H5P's own exporter skips them).
- SCORM 1.2 packaging is the next PR and reuses this contract and this layer.
