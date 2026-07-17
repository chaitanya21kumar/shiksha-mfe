# ADR 0005 — Packaging assessments as SCORM 1.2

- **Status:** Accepted
- **Date:** 2026-07-17
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7), Module B; consumes the contract from [ADR-0003](0003-neutral-assessment-contract-and-grounding.md) and the packaging layer from [ADR-0004](0004-pure-python-h5p-packaging.md)

## Context

The second of Module B's packaging targets, and the one issue #7 pins hardest:
*"SCORM packages import successfully into Moodle 4.x and Open edX."* Two LMSs, one
package.

SCORM is not H5P with different key names. In H5P the LMS owns the player and we
ship content; in SCORM **the package ships the player** and the LMS offers only a
JavaScript API to report through. That inversion drives most of what follows.

The other thing that drives it: **SCORM fails silently.** A malformed value is
refused with a numeric error code the SCO must explicitly go and ask for. Nothing
throws. So a wrong delimiter does not break the quiz — it produces a package that
runs perfectly and records garbage, or nothing.

Every rule below was read from a primary source: the ADL SCORM 1.2 Run-Time
Environment and Content Aggregation Model books, the official ADL schemas, Moodle
4.5's `scorm_12.js` and `scormlib.php`, and `openedx-scorm-xblock`. An earlier
research pass on this had *no* fetched SCORM sources and rested on recall; that
pass's draft manifest turned out to declare the **SCORM 1.1** namespace, which is
exactly the kind of error this one was meant to catch.

## Decision

**1. Target Moodle's strictness. It is a strict superset.** Moodle validates and
returns errors; Open edX accepts almost anything (`LMSGetLastError` is hardcoded
to `"0"`, every call returns `"true"`). So every Moodle-legal call is Open
edX-legal, and the reverse is badly false. It also means **Open edX cannot fail a
package and is worthless as a test oracle** — a package that "works" there proves
nothing.

**2. Omit `adlcp:masteryscore`; the SCO is the only authority on pass/fail.**
Counter-intuitive, and a reversal of the note left in ADR-0003. When
`masteryscore` is present, Moodle stops believing the `lesson_status` the SCO
wrote and derives pass/fail itself by comparing `score.raw` — gated on a setting
that defaults to *enabled*. Open edX implements no mastery-score path at all; its
pass/fail comes only from `lesson_status`. So declaring it means Moodle overrides
us and Open edX never marks success. Omitting it and writing `lesson_status`
ourselves makes both agree. `pass_percentage` is still honoured exactly — by our
grader. The threshold does not have to cross to the LMS for the LMS to record the
right outcome. *Cost: Moodle's activity UI will not display a mastery score.*

**3. Reimplement the API wrapper rather than vendor pipwerks.** The usual wrapper
ships **no LICENSE file** — the MIT-style grant exists only as an assertion in a
source header. Vendoring it would stamp a third-party copyright into every package
this engine emits into every tenant's LMS. The discovery algorithm is ADL's, is
published in the spec, and is ~90 lines. This also matches the house rule the
Python side already follows: stdlib only, reimplement rather than depend.

**4. The SCO grades itself, and `points` is honoured exactly.** This is a strict
improvement over the H5P path. H5P has no per-question weight, so `emit_h5p` must
warn that it scored the set out of a different total than intended. We own the
grader, so `max_points` is authoritative and no such warning exists. MCQ is
all-or-nothing; match and fill-in are proportional. `case_sensitive` and
`order_matters` are honoured here too — SCORM 1.2 can express neither.

**5. Reporting degrades; the assessment never does.** The other structural
divergence from `emit_h5p`, which must *drop* a question H5P cannot render. We own
the player, so every question always renders and always scores. When SCORM's
reporting cannot express something — more than 36 options, a pattern over 255
characters — we skip that **interaction** and warn. A truncated pattern would
report a wrong right answer, which is worse than reporting none.

**6. LaTeX is rendered as source, not dropped.** SCORM has no maths support and no
LMS supplies a renderer, so the only offline option is bundling MathJax into every
package (Apache-2.0, but ~2 MB). That is a real decision — package size, a
third-party asset in the repo — and it deserves its own PR and a mentor's call
rather than a silent default here. Until then a `has_latex` question is emitted,
rendered with its source shown, scored, and named in `warnings`. A CDN `<script>`
was rejected outright: it breaks offline use, dies behind an LMS CSP, and
contradicts issue #7's local-first goal.

## Consequences

- **One package serves both targets**, with no per-LMS build.
- **`app/packaging/scorm/` stays domain-agnostic**, like its H5P sibling, so
  Module D can emit its own SCOs through it.
- **The player ships inside the wheel** as package data — hence the new
  `[tool.setuptools.package-data]` entry. Without it a built wheel would emit
  packages with no JavaScript in them.
- **Trade-off — no mastery score in Moodle's UI.** The consequence of decision 2,
  and cosmetic.
- **Trade-off — LaTeX shows as source.** Known, warned, and not silent.
- **Residual risk — no package has been imported into a real LMS yet.** Everything
  is verified statically: both LMSs' real parsers and Moodle's verbatim regexes
  are executed against our output in the test suite, and the full CMI call
  sequence was proven against a fake LMS enforcing those regexes. That is strong,
  and it is not an import. A one-time Moodle 4.x import is the settlement.

## Notes — the traps, recorded because each is invisible

- **Never pretty-print `imsmanifest.xml`.** Open edX matches `^1.2$` against
  `<schemaversion>`'s text; indentation makes that `"\n  1.2\n"`, the match fails,
  the package is treated as SCORM 2004, the LMS injects `API_1484_11`, our SCO
  finds no `API` — and reports nothing while the quiz renders perfectly.
- **The `adlcp` prefix is load-bearing.** Moodle parses without namespace
  processing and matches the literal string `ADLCP:SCORMTYPE`. The correct URI on
  a different prefix is valid XML Moodle cannot see. Conversely a *wrong* URI is
  invisible to Moodle — which is why a SCORM 1.1 namespace would pass a Moodle
  smoke test and fail everywhere else.
- **`LMSInitialize("")` needs the literal empty string.** Moodle guards on
  `param == ""`, and `undefined == ""` is false → error 201. Open edX's shim takes
  no parameter and always returns `"true"`, so this mistake is invisible there and
  only ever bites on Moodle.
- **`"not attempted"` is readable but not writable** (405), so a SCO that reads
  `lesson_status` and echoes it back fails on its first write.
- **`cmi.core.exit = ""` is a normal exit**; `"normal"` is not a value at all.
- **`LMSGetLastError` returns the string `"0"`**, which is truthy — the obvious
  error check inverts itself. Parse it.
- **`score.raw` must be normalised 0–100.** Normative in 1.2, not a convention:
  Moodle enforces the range, and Open edX divides by 100 while ignoring
  `score.max`, so 850/1000 would grade as 850%.
- **Patterns are 1.2, not 2004:** a plain comma between responses, a plain period
  inside a matching pair. `[,]`, `[.]` and `{case_matters=}` are 2004 — and
  nothing catches them, because Moodle ships
  `CMIFeedback = CMIString256; // This must be redefined` and Open edX ignores
  interactions entirely. The encoder is the only guard.
- Endpoints: `POST /assess/scorm` (an `AssessmentSet`) and `POST /assess/scorm/file`.
