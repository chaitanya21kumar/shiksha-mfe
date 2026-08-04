# ADR 0009 — Packaging an interactive video, and why the media is referenced

- **Status:** Accepted
- **Date:** 2026-07-29
- **Deciders:** Chaitanya Kumar (contributor)
- **Context ref:** Issue [tekdi/shiksha-mfe#7](https://github.com/tekdi/shiksha-mfe/issues/7),
  Module C ("H5P Interactive Video package with inline knowledge checks"); the milestone doc's
  Week 8. Builds on [ADR-0004](0004-pure-python-h5p-packaging.md) (H5P packaging),
  [ADR-0007](0007-transcription-provider-strategy.md) and [ADR-0008](0008-auto-chaptering.md).

## Context

The last piece of Module C composes what the engine already produces — a
transcript, its chapters, and questions grounded in a source — into an
`H5P.InteractiveVideo`: a recording whose navigation bar carries chapter marks
and which pauses to ask a question at the end of each chapter.

Every decision below was read out of the package the **H5P Hub actually serves**
(`H5P.InteractiveVideo 1.27`) — its `semantics.json`, its shipped
`content/content.json`, and its runtime bundle — for the reason ADR-0004 records:
this format fails silently. Interactive Video is the least forgiving target so
far, because it has a second exact-string whitelist that nobody warns you about.

## Decision

**1. The media is referenced by URL, not bundled.** The package carries
`{"path": "https://…/lecture.mp4", "mime": "video/mp4", "copyright": {"license": "U"}}`,
which is precisely the shape H5P's own published Interactive Video content uses.
Bundling would put an entire recording inside the `.h5p` and run straight into an
LMS upload limit — Moodle's default is far below a lecture video — for no benefit,
since the LMS streams the file either way. The consequence is a real API
requirement rather than a hidden one: the caller supplies the URL the learner's LMS
will stream from, and the endpoint asks for it explicitly instead of inventing one.

**2. Chapters become bookmarks; questions become interactions.** A chapter's start
is a `bookmarks[]` entry, which is exactly the chapter marker issue #7 asks for.
A question is an `interactions[]` entry whose `action` is the same
`{library, params, subContentId, metadata}` subcontent shape a Question Set child
uses — so the mapping is **shared with the existing emitter** rather than written
twice. `build_question_subcontent` in `assessment/emit/h5p.py` is that seam.

**3. A question is placed at the end of the chapter its evidence came from, and
each chapter is generated for separately.** Every chapter is handed to the
*existing* assessment pipeline as **its own single-page document**, which buys the
same no-hallucination grounding gate, the same question→H5P mapping and the same
warning discipline, with no second code path.

Per chapter rather than once for the whole recording, because `generate_assessment`
caps questions *per type across the document it is given*: handing it every chapter
at once produces `count` questions for the entire video, and the model decides where
they land. On a 40-minute lecture that means chapter 1 gets a check and the other
twenty-three get nothing — while the endpoint claims a check per chapter. Since the
promise of this module is a check at the end of *each* chapter, the loop over
chapters belongs in Python, where it is deterministic, and not in a prompt asking a
model to spread its output. It also makes grounding stricter: a question on chapter
7 cannot be evidenced by chapter 2's text, because chapter 2 was never in the
prompt. A chapter that yields nothing is named in the warnings rather than quietly
left bare.

**4. Short-answer questions cannot go into a video, and that is enforced.**
Interactive Video permits **18** libraries and `H5P.Essay` is not one of them.
This is not a soft failure: `H5PContentValidator` checks
`in_array($value->library, $libraryNames)` and sets `$value = NULL`, so a
non-whitelisted interaction is **stripped** — the video imports and plays perfectly
with the question quietly missing. Core does call `setErrorMessage`, but only on
the `filterParameters` path, and `H5PStorage::savePackage` never takes it, so
uploading the package reports nothing at all. `ALLOWED_INTERACTION_LIBRARIES` is checked in the shared seam, the
endpoint does not offer `short_answer` at all, and a short answer that arrives
anyway is dropped with a named warning.

**5. `l10n` is written out in full — all 47 strings — and twelve of them are
load-bearing.** The runtime does default most of this block
(`l10n = $.extend({interaction: "Interaction", …}, l10n)` — 36 keys, 35 of which are
`l10n` fields), but the twelve `endcard*`/`endCard*` strings are **not** in that
extend, and those are exactly what decision 6 below puts on the learner's screen. Emitting only the twelve would work;
emitting all 47 costs a few hundred bytes and removes a dependency on someone else's
default surviving a library upgrade. The values come from the library's own
`semantics.json`, which is where they are declared.

**5a. `video.textTracks` is emitted even though it is empty.** The constructor
merges its default with `$.extend({video: {textTracks: {videoTrack: []}}, …}, …)` —
a **shallow** merge, no leading `true` — so writing a `video` object at all replaces
that default wholesale. One read of the key is guarded; the one at the end of
`getCopyrights` is not, and H5P core calls `getCopyrights` whenever the rights
dialog is built, which is the platform default in Moodle. Omitting the key is a
`TypeError` on the learner's page, from a field that looks purely optional.

**6. One `endscreen` is emitted, because it is what turns the submit path on.**
`hasStar = editor || undefined !== assets.endscreens && assets.endscreens.length && …`.
With no endscreen `hasStar` is false for every package, and the star control, the
score bubble, the end card and the "Submit Answers" button are all dead code — the
learner answers every check and is never offered a way to hand them in. The runtime
clamps an endscreen time past the media length back to the duration, so one placed
at the end of the timeline is safe on a recording of any length. H5P's own published
content ships exactly one.

**7. `summary` and `goto` are deliberately omitted.** Both are read behind guards —
`hasMainSummary()` returns false when the group is absent, and `goto` is only
dereferenced after `&&` — so leaving them out is safe. The Hub's own sample content
*does* carry both, and that is not a counter-argument: it was authored by hand in
the editor, and nothing in this engine generates a summary task or branching logic.
Emitting an empty `summary` would add `H5P.Summary` to the closure and show the
learner a summary screen with nothing in it.

**8. The dependency closure is 15 libraries, and editor dependencies are excluded.**
Interactive Video's own runtime closure is eight; four of them were already pinned
for the Question Set path, so it adds four: `H5P.InteractiveVideo 1.27`,
`H5P.DragNBar 1.5`, `H5P.DragNDrop 1.1`, `H5P.DragNResize 1.2`. `DragNBar` is not
optional decoration — Interactive Video positions its interactions through it, so
omitting it yields a video that imports cleanly and shows no interactions at all.
The editor exclusion matters more here than anywhere: following
`editorDependencies` takes the closure from 15 to 53 and declares the whole
H5PEditor tree as a runtime requirement. The Hub's own package proves the rule —
53 library folders on disk, 33 entries in its `h5p.json`.

## Consequences

- **No new packaging machinery.** The manifest builder, the ZIP writer and the
  subcontent wrapper were already parameterised by ADR-0004; this passes a
  different main library and closure to the same functions. Three things that had
  been private to the assessment emitter became shared when a second content type
  needed them — the HTML escaper, the filename sanitiser and the package HTTP
  response — because each is silently wrong when re-implemented rather than reused.
- **Trade-off — one model call per chapter, not one per video.** Decision 3 buys
  guaranteed per-chapter coverage at the cost of N calls. They are independent, so
  they run concurrently under a small bound (four at a time), and chapters are
  capped at 24, which bounds a single request.
- **The three objective question types transfer unchanged.** Every library present
  in both whitelists is pinned at the *same* version in each — MultiChoice 1.16,
  Blanks 1.14, DragText 1.10 — so a question this engine already emits maps across
  without a second params path.
- **Byte-reproducible**, like the other H5P packages, so tests assert on the artifact.
- **Trade-off — the caller must host the media.** The engine transcribes an upload
  but cannot serve it; the URL is the caller's responsibility. Documented on the
  endpoint rather than guessed at.
- **Trade-off — no subjective questions in video.** A real capability gap, imposed
  by the format rather than chosen. Short answers remain available in the H5P
  Question Set and SCORM paths.
- **Residual risk — no package has been imported into a real Moodle yet.** Same
  standing risk as ADR-0004 and ADR-0005: the format rules are verified against
  H5P's own semantics, validator and runtime, which is strong, and it is still not
  an import.

## Deliberately deferred

- ~~**Subtitles as a text track.**~~ **Done, 2026-08-01.** `subtitles_url` on
  `VideoSource`, referenced rather than embedded for the same reason the media is:
  `getPath` returns anything matching `/^[a-z0-9]+:\/\//i` untouched, so an absolute
  URL resolves as written. Every field of the entry is emitted — `label`, `kind`,
  `srcLang`, `track` — because `kind` is optional in the semantics and mandatory in
  practice: `html5.js` reads it off the first entry while building the `<track>`
  element and throws on the learner's page without it. Referencing means the caption
  is fetched cross-origin, which a missing header turns into a permanently empty
  caption menu, so the emitter warns.
- **Adaptivity** (`requireCompletion`, branching on a wrong answer). The contract
  has room for it; nothing generates the branching logic yet.
- **`endscreens` and the end-of-video summary task**, for the reason in decision 6.

## Notes

- Endpoints: `POST /interactive-video` (a `ChapteredTranscript` + `video_url`) and
  `POST /interactive-video/file` (a media upload run through the whole chain).
- Versions and both whitelists live in `app/packaging/h5p/versions.py`, so re-pinning
  a tenant stays a one-file change.
