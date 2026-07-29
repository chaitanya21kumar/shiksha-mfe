"""Map an `InteractiveVideoSpec` onto an importable ``H5P.InteractiveVideo`` package.

Every field below was read out of the package the H5P Hub actually serves —
``H5P.InteractiveVideo 1.27``'s own ``semantics.json``, its shipped
``content/content.json``, and its runtime bundle — rather than from documentation,
for the reason ADR-0004 records: this format fails silently. The two rules that
bite hardest here:

- **The interaction whitelist is exact-string.** H5P's ``H5PContentValidator``
  checks ``in_array($value->library, $libraryNames)``, so a library outside the
  list is not an error the author sees — the interaction is *stripped* and the
  video imports and plays with the question quietly missing. ``H5P.Essay`` is not
  on Interactive Video's list, so short-answer questions cannot ride along.
- **``video.textTracks`` must be emitted even when empty.** The constructor does
  ``options = $.extend({video: {textTracks: {videoTrack: []}}, …}, interactiveVideo)``
  — a **shallow** merge, with no leading ``true``. Our ``video`` object therefore
  *replaces* that default wholesale, so the key only ever exists if we write it.
  One read of it is guarded; the one at the end of ``getCopyrights`` is not, and
  H5P core calls ``getCopyrights`` whenever the rights dialog is built, which is
  the platform default. Omitting the key is a ``TypeError`` on the learner's page.
- **``assets.endscreens`` is what turns the submit path on.**
  ``hasStar = editor || undefined !== assets.endscreens && assets.endscreens.length && …``
  — with no endscreen the star control, the end card and the submit button are all
  dead code, so a learner answers every check and is never offered "Submit
  Answers". H5P's own published content ships exactly one endscreen.
- **The twelve end-card ``l10n`` strings are not defaulted.** The runtime does
  default the other 38 (``l10n = $.extend({interaction: "Interaction", …}, l10n)``),
  but the ``endcard*``/``endCard*`` keys are absent from that block — and those are
  precisely the strings the submit path above puts on screen. The whole block is
  emitted anyway, from the library's own semantics: being explicit costs a few
  hundred bytes and removes a dependency on someone else's default.

``summary`` and ``goto`` are deliberately *not* emitted: the runtime guards both
(``hasMainSummary`` returns false when the group is absent, and ``goto`` is only
read behind ``&&``), and H5P's own published content omits them too.

Everything the model wrote — chapter titles, question prompts — is escaped on the
way in, for the reason ``app/assessment/emit/h5p.py`` records: H5P builds these
labels by string concatenation into the DOM.
"""

from __future__ import annotations

import math

from ..assessment.emit.h5p import UnrenderableQuestion, build_question_subcontent
from ..packaging.h5p import (
    ALLOWED_INTERACTION_LIBRARIES,
    INTERACTIVE_VIDEO,
    INTERACTIVE_VIDEO_CLOSURE,
    H5PPackage,
    build_manifest,
    escape_text,
    sanitise_filename,
    sanitise_title,
    write_h5p,
)
from .schema import KNOWN_VIDEO_MIMES, InteractiveVideoSpec

#: How long a knowledge check stays on screen once it appears, in seconds. The
#: video is paused for it, so this is only the window in which it is reachable.
_INTERACTION_WINDOW = 20.0
#: Keep an interaction clear of the very end of the media: an interaction whose
#: window starts at or after the final frame never becomes reachable.
_END_MARGIN = 1.0
#: Button positions are percentages of the frame. Several checks that appear at
#: the same instant are laid out on a grid so their buttons cannot sit on top of
#: each other — two buttons at identical coordinates means only the topmost is
#: clickable, and the questions underneath are silently unanswerable.
_FIRST_X, _X_STEP, _MAX_X = 20.0, 14.0, 80.0
_FIRST_Y, _Y_STEP = 40.0, 12.0
#: How many buttons fit on one row before wrapping to the next.
_PER_ROW = int((_MAX_X - _FIRST_X) // _X_STEP) + 1
#: What H5P calls each library in an interaction's ``libraryTitle``.
_CONTENT_TYPE_TITLES = {
    "H5P.MultiChoice": "Multiple Choice",
    "H5P.Blanks": "Fill in the Blanks",
    "H5P.DragText": "Drag the Words",
}

#: ``interactiveVideo.override`` — the six player defaults, verbatim from
#: ``H5P.InteractiveVideo-1.27/semantics.json``.
_OVERRIDE: dict[str, object] = {
    "autoplay": False,
    "loop": False,
    "showBookmarksmenuOnLoad": False,
    "showRewind10": False,
    "preventSkippingMode": "none",
    "deactivateSound": False,
}

#: ``interactiveVideo.l10n`` — all 47 strings the player reads, verbatim from the
#: same semantics file. The runtime defaults 38 of them; the twelve ``endcard*`` /
#: ``endCard*`` keys are **not** in that block, and those are exactly the strings
#: the submit path puts on screen. The whole set is written out rather than only
#: the twelve: it costs a few hundred bytes and it is one fewer thing that changes
#: meaning when the library is upgraded.
_L10N: dict[str, str] = {
    "interaction": "Interaction",
    "play": "Play",
    "pause": "Pause",
    "mute": "Mute, currently unmuted",
    "unmute": "Unmute, currently muted",
    "quality": "Video Quality",
    "captions": "Captions",
    "close": "Close",
    "fullscreen": "Fullscreen",
    "exitFullscreen": "Exit Fullscreen",
    "summary": "Open summary dialog",
    "bookmarks": "Bookmarks",
    "endscreen": "Submit screen",
    "defaultAdaptivitySeekLabel": "Continue",
    "continueWithVideo": "Continue with video",
    "more": "More player options",
    "playbackRate": "Playback Rate",
    "rewind10": "Rewind 10 Seconds",
    "navDisabled": "Navigation is disabled",
    "navForwardDisabled": "Navigating forward is disabled",
    "sndDisabled": "Sound is disabled",
    "requiresCompletionWarning": (
        "You need to answer all the questions correctly before continuing."
    ),
    "back": "Back",
    "hours": "Hours",
    "minutes": "Minutes",
    "seconds": "Seconds",
    "currentTime": "Current time:",
    "totalTime": "Total time:",
    "singleInteractionAnnouncement": "Interaction appeared:",
    "multipleInteractionsAnnouncement": "Multiple interactions appeared.",
    "videoPausedAnnouncement": "Video is paused",
    "content": "Content",
    "answered": "@answered answered",
    "endcardTitle": "@answered Question(s) answered",
    "endcardInformation": (
        "You have answered @answered questions, click below to submit your answers."
    ),
    "endcardInformationOnSubmitButtonDisabled": "You have answered @answered questions.",
    "endcardInformationNoAnswers": "You have not answered any questions.",
    "endcardInformationMustHaveAnswer": (
        "You have to answer at least one question before you can submit your answers."
    ),
    "endcardSubmitButton": "Submit Answers",
    "endcardSubmitMessage": "Your answers have been submitted!",
    "endcardTableRowAnswered": "Answered questions",
    "endcardTableRowScore": "Score",
    "endcardAnsweredScore": "answered",
    "endCardTableRowSummaryWithScore": (
        "You got @score out of @total points for the @question that appeared after "
        "@minutes minutes and @seconds seconds."
    ),
    "endCardTableRowSummaryWithoutScore": (
        "You have answered the @question that appeared after @minutes minutes and "
        "@seconds seconds."
    ),
    "videoProgressBar": "Video progress",
    "howToCreateInteractions": "Play the video to start creating interactions",
}


def _floor2(seconds: float) -> float:
    """Two decimals, always rounded **down**.

    Ordinary rounding is not safe for a time that must not exceed the media: a
    185.857-second recording rounds to 185.86, which is 3 ms *past* the end — and
    the runtime's ``duration.to > t`` test fires on exactly that. Times are only
    ever shortened here, never lengthened.
    """
    return math.floor(seconds * 100) / 100


def _timeline_end(spec: InteractiveVideoSpec) -> float:
    """How long the media is, as far as this package can tell.

    ``media_seconds`` is optional — an OpenAI-compatible STT gateway need not
    report a duration, and a caller can POST a `ChapteredTranscript` without one.
    The last chapter's end is then the best available answer, and using it matters:
    that chapter's check is the one that would otherwise be pinned to the final
    frame, where it never becomes reachable.
    """
    declared = spec.source.media_seconds
    if declared is not None and declared > 0:
        return declared
    return max((chapter.end for chapter in spec.chapters), default=0.0)


def _bookmarks(spec: InteractiveVideoSpec, limit: float) -> list[dict[str, object]]:
    """Chapter starts, as the marks in the player's navigation bar.

    H5P rounds a bookmark to the second when it seeks, so the times are emitted as
    the chapter's own start rather than nudged — a mark that sits a moment early is
    better than one that lands after the first sentence of its chapter. A mark past
    the end of the media is unreachable, so it is clamped the same way a check is.
    """
    return [
        {
            "time": _floor2(max(min(chapter.start, limit), 0.0)),
            "label": escape_text(chapter.title),
        }
        for chapter in spec.chapters
    ]


def _endscreens(limit: float) -> list[dict[str, object]]:
    """The single end screen that turns the submit path on.

    Without an entry here ``hasStar`` is false and the end card, the score bubble
    and the "Submit Answers" button are never built — the learner answers every
    check and is offered no way to hand them in. The runtime clamps a time past
    the media length to the duration and rewrites the label, so a value at the
    very end is safe on a recording of any length.
    """
    return [{"time": _floor2(max(limit, 0.0)), "label": "Submit screen"}]


def _placement(order: int) -> tuple[float, float]:
    """Where the nth simultaneous button sits, as frame percentages.

    Wrapping onto a new row rather than clamping at ``_MAX_X``: clamping put every
    button past the fifth on one spot, which hides all but the topmost.
    """
    return (
        _FIRST_X + (order % _PER_ROW) * _X_STEP,
        _FIRST_Y + (order // _PER_ROW) * _Y_STEP,
    )


def _appears_at(chapter_end: float, limit: float) -> float:
    """When a check appears: the chapter's end, kept clear of the final frame."""
    return _floor2(max(min(chapter_end, limit - _END_MARGIN), 0.0))


def _window_end(at: float, limit: float) -> float:
    """When the check stops being reachable, never past the end of the media.

    This is not cosmetic. ``H5P.InteractiveVideo.loaded`` does, for any interaction
    whose ``duration.to`` exceeds the real media length::

        s = to - from;  from = max(t - s, 0);  to = t

    — it *preserves the window* and drags the **start backwards**. A check placed at
    the end of the last chapter of a 10-minute lecture would be moved 20 seconds
    earlier, into material the learner has not been questioned on yet; on a
    recording shorter than the checks' combined windows, every check is dragged
    onto the same instant. Bounding ``to`` here means that branch never runs.
    """
    return _floor2(min(at + _INTERACTION_WINDOW, limit))


def _interaction(
    action: dict[str, object], *, at: float, limit: float, order: int, label: str
) -> dict[str, object]:
    """One knowledge check on the timeline, in the shape H5P's own content uses."""
    x, y = _placement(order)
    library = str(action["library"]).split(" ")[0]
    return {
        "x": x,
        "y": y,
        "width": 10,
        "height": 10,
        "duration": {"from": at, "to": _window_end(at, limit)},
        # Pause so the learner answers rather than the question sliding past.
        "pause": True,
        "displayType": "button",
        "buttonOnMobile": False,
        "label": label,
        "libraryTitle": _CONTENT_TYPE_TITLES.get(library, "Question"),
        "action": action,
    }


def _interactions(
    spec: InteractiveVideoSpec, limit: float, warnings: list[str]
) -> list[dict[str, object]]:
    """Every question, placed at the end of the chapter it belongs to."""
    ends = {chapter.index: chapter.end for chapter in spec.chapters}
    built: list[dict[str, object]] = []
    # Buttons are laid out per *instant*, not per chapter: two chapters whose ends
    # clamp to the same moment would otherwise stack their buttons on each other.
    order_at: dict[float, int] = {}

    for check in spec.checks:
        at = _appears_at(ends[check.chapter_index], limit)
        for question in check.questions:
            try:
                action = build_question_subcontent(
                    question, spec.content_id, allowed=ALLOWED_INTERACTION_LIBRARIES
                )
            except UnrenderableQuestion as unrenderable:
                warnings.append(
                    f"Left {question.id} out of the interactive video: {unrenderable.reason}."
                )
                continue
            order = order_at.get(at, 0)
            order_at[at] = order + 1
            built.append(
                _interaction(action, at=at, limit=limit, order=order, label=_label(question))
            )
    return built


def _label(question: object) -> str:
    """What the learner reads on the button, before they open the question.

    ``prompt`` is optional on a fill-in-the-blanks question — the sentence itself
    carries the instruction — so the text is the honest label there. The id is the
    last resort: a button reading "Question q3" is poor, but a blank one is worse.
    """
    for candidate in (getattr(question, "prompt", None), getattr(question, "text", None)):
        if candidate and candidate.strip():
            return escape_text(candidate.strip())[:120]
    return f"Question {getattr(question, 'id', '')}".strip()


def emit_interactive_video(spec: InteractiveVideoSpec) -> H5PPackage:
    """Build an importable ``.h5p`` Interactive Video from a chaptered transcript."""
    warnings = list(spec.warnings)

    if spec.video.mime not in KNOWN_VIDEO_MIMES:
        warnings.append(
            f"The video mime {spec.video.mime!r} is outside what H5P.Video plays natively "
            f"({', '.join(sorted(KNOWN_VIDEO_MIMES))}); the learner may see an empty player."
        )

    limit = _timeline_end(spec)
    declared = spec.source.media_seconds
    overrun = max((chapter.end for chapter in spec.chapters), default=0.0)
    if declared is not None and declared > 0 and overrun > declared:
        # The transcribed upload and the streamed URL are two different files here,
        # which the two-parameter endpoint openly invites. Say so rather than
        # quietly collapsing every over-running chapter onto the same instant.
        warnings.append(
            f"The chapters run to {overrun:.1f}s but the media is {declared:.1f}s long; "
            "marks and checks past the end have been pulled back to it."
        )

    interactions = _interactions(spec, limit, warnings)
    if not interactions:
        warnings.append("No knowledge checks were placed; the video has chapters only.")

    content: dict[str, object] = {
        "interactiveVideo": {
            "video": {
                "startScreenOptions": {
                    "title": sanitise_title(spec.title, fallback="Interactive video"),
                    "hideStartTitle": False,
                },
                # Emitted even though it is empty: the constructor's default for
                # this key is applied by a *shallow* extend, so writing `video` at
                # all removes it — and getCopyrights dereferences it unguarded.
                "textTracks": {"videoTrack": []},
                # Referenced, not bundled — the shape H5P's own published content
                # uses. "U" is Undisclosed: the recording is the tenant's, so the
                # engine is in no position to assert a licence on their behalf.
                "files": [
                    {
                        "path": spec.video.url,
                        "mime": spec.video.mime,
                        "copyright": {"license": "U"},
                    }
                ],
            },
            "assets": {
                "interactions": interactions,
                "bookmarks": _bookmarks(spec, limit),
                "endscreens": _endscreens(limit),
            },
        },
        "override": dict(_OVERRIDE),
        "l10n": dict(_L10N),
    }

    manifest = build_manifest(
        title=spec.title,
        language=spec.language,
        main_library=INTERACTIVE_VIDEO,
        dependencies=INTERACTIVE_VIDEO_CLOSURE,
    )
    stem = sanitise_filename(
        spec.source.filename.rsplit(".", 1)[0], fallback="interactive-video"
    )
    return H5PPackage(
        content=write_h5p(manifest=manifest, content=content),
        filename=f"{stem}-interactive-video.h5p",
        warnings=warnings,
    )
