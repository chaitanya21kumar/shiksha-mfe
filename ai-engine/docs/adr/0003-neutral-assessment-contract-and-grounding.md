# ADR 0003 — A neutral, source-grounded assessment contract

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7), Module B; builds on the generative pattern of [ADR-0002](0002-hosted-model-apis-for-development.md)

## Context

Module B generates multiple-choice, match-the-pair, and fill-in-the-blank
questions from a parsed document. Issue #7 requires two things that pull in
different directions:

- The questions must be generated **strictly from the source material, with no
  hallucinations.**
- The same questions must later be packaged, in their own PRs, as a valid **H5P
  Question Set**, a **SCORM 1.2** package (importable into Moodle 4.x and Open
  edX), and **xAPI 1.0** statements — and reused by Module C for interactive-video
  knowledge-checks — with **LaTeX rendered via MathJax.**

If the contract were shaped around any one of those targets, adding the next
would force a breaking migration. The failure modes are concrete: xAPI and SCORM
build their response patterns from element *ids* (not display text) using
reserved `[,] [.] [:]` delimiters; H5P and xAPI disagree on default
case-sensitivity; SCORM needs a unique manifest id and a mastery score.

## Decision

**1. One neutral contract.** `AssessmentSet` holds a discriminated union of
question types (`MCQItem`, `MatchItem`, `FillBlankItem`). It is not coupled to
H5P, SCORM, or xAPI; each packaging module maps *from* it. The contract was
pressure-tested against every downstream target before it was written, and
carries exactly what lossless mapping needs and no more:

- **Engine-assigned, delimiter-safe ids** on every answerable element (choices,
  match terms, blanks). The pipeline assigns them, never the model, so they are
  unique and safe for xAPI/SCORM patterns.
- **Structured answers, never marked-up strings.** The H5P `*answer/alt:tip*`
  markup and the xAPI pattern are generated at emit time from the same data.
- Set-level `assessment_id` (SCORM manifest id / xAPI activity IRI), `language`
  (BCP-47), and `pass_percentage` (SCORM mastery / H5P pass / xAPI success).
- Per-item `source_index`, `points`, `has_latex`; math is emitted only with
  `\( \)` / `\[ \]` / `$$` delimiters, which is what H5P's MathDisplay renders.
- Interactive-video placement (timestamp, coordinates) is deliberately **out** of
  a question; Module C adds a thin wrapper.

**2. Grounding = prompt discipline + programmatic verification.** The prompt
requires the model to quote the exact source span that justifies each answer;
the pipeline then verifies that span actually appears in the source and **drops
any question it cannot ground, recording a warning.** The evidence span itself is
not stored — it is a generation-time check, not packaging data.

## Consequences

- **No breaking migration** — the packaging PRs (H5P, SCORM) and Module C map the
  same contract without changing its shape.
- **No-hallucination guarantee is enforced, not just requested** — an ungrounded
  question never ships; it is dropped and surfaced in `warnings`.
- **Leaner contract** — grounding evidence, per-type counts, and difficulty were
  kept out (counts are computed; difficulty is deferred) so the model maps
  cleanly to the five downstream formats.
- **Graceful degradation** — a type that comes back unusable is a warning, not a
  failed request; connectivity/timeout failures still fail fast.
- **Trade-off** — grounding by exact-quote matching can drop a valid question the
  model paraphrased instead of quoting. That is the safe direction for a
  no-hallucination requirement, and the prompt is written to make the model quote.

## Notes

- Endpoints: `POST /assess` (a `ParsedDocument`) and `POST /assess/file` (an
  upload), with `question_types`, `count`, and `language` parameters.
- Emit-time constraints recorded for the packaging PRs: SCORM `imsmanifest.xml`
  at the ZIP root with `adlcp:scormtype="sco"`; Open edX's SCORM XBlock scores on
  `score.raw / 100`; H5P `questions[]` are subcontent wrappers with a flattened,
  transitive `preloadedDependencies` list.
