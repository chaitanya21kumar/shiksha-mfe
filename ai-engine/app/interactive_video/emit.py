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
- **``l10n`` must be written out in full.** The player reads ``this.l10n.<key>``
  with no fallback, so an absent block puts the string "undefined" on the
  learner's controls. The defaults below are lifted from the library's own
  semantics, which is where those defaults are declared.

``summary`` and ``goto`` are deliberately *not* emitted: the runtime guards both
(``hasMainSummary`` returns false when the group is absent, and ``goto`` is only
read behind ``&&``), and H5P's own published content omits them too.
"""

from __future__ import annotations

from typing import NamedTuple

from ..assessment.emit.h5p import UnrenderableQuestion, build_question_subcontent
from ..packaging.h5p.manifest import build_manifest, sanitise_title
from ..packaging.h5p.package import write_h5p
from ..packaging.h5p.versions import (
    ALLOWED_INTERACTION_LIBRARIES,
    INTERACTIVE_VIDEO,
    INTERACTIVE_VIDEO_CLOSURE,
)
from .schema import KNOWN_VIDEO_MIMES, InteractiveVideoSpec

#: How long a knowledge check stays on screen once it appears, in seconds. The
#: video is paused for it, so this is only the window in which it is reachable.
_INTERACTION_WINDOW = 20.0
#: Keep an interaction clear of the very end of the media: an interaction whose
#: window starts at or after the final frame never becomes reachable.
_END_MARGIN = 1.0
#: Button positions are percentages of the frame. Several checks on one chapter
#: are laid out along a row so their buttons cannot sit on top of each other.
_FIRST_X, _X_STEP, _MAX_X, _BUTTON_Y = 20.0, 14.0, 80.0, 40.0
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
#: same semantics file. Emitted in full because the runtime never defaults them.
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


class H5PPackage(NamedTuple):
    """A built ``.h5p`` and anything the caller should know about it."""

    content: bytes
    filename: str
    warnings: list[str]


def _bookmarks(spec: InteractiveVideoSpec) -> list[dict[str, object]]:
    """Chapter starts, as the marks in the player's navigation bar.

    H5P rounds a bookmark to the second when it seeks, so the times are emitted as
    the chapter's own start rather than nudged — a mark that sits a moment early is
    better than one that lands after the first sentence of its chapter.
    """
    return [
        {"time": round(chapter.start, 2), "label": chapter.title} for chapter in spec.chapters
    ]


def _placement(order: int) -> tuple[float, float]:
    """Where the nth button on one chapter sits, as frame percentages."""
    return min(_FIRST_X + order * _X_STEP, _MAX_X), _BUTTON_Y


def _appears_at(chapter_end: float, media_seconds: float | None) -> float:
    """When a check appears: the chapter's end, kept clear of the final frame."""
    if media_seconds is None or media_seconds <= 0:
        return round(max(chapter_end, 0.0), 2)
    return round(max(min(chapter_end, media_seconds - _END_MARGIN), 0.0), 2)


def _interaction(
    action: dict[str, object], *, at: float, order: int, label: str
) -> dict[str, object]:
    """One knowledge check on the timeline, in the shape H5P's own content uses."""
    x, y = _placement(order)
    library = str(action["library"]).split(" ")[0]
    return {
        "x": x,
        "y": y,
        "width": 10,
        "height": 10,
        "duration": {"from": at, "to": round(at + _INTERACTION_WINDOW, 2)},
        # Pause so the learner answers rather than the question sliding past.
        "pause": True,
        "displayType": "button",
        "buttonOnMobile": False,
        "label": label,
        "libraryTitle": _CONTENT_TYPE_TITLES.get(library, "Question"),
        "action": action,
    }


def _interactions(spec: InteractiveVideoSpec, warnings: list[str]) -> list[dict[str, object]]:
    """Every question, placed at the end of the chapter it belongs to."""
    ends = {chapter.index: chapter.end for chapter in spec.chapters}
    media = spec.source.media_seconds
    built: list[dict[str, object]] = []

    for check in spec.checks:
        at = _appears_at(ends[check.chapter_index], media)
        order = 0
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
            built.append(
                _interaction(
                    action,
                    at=at,
                    order=order,
                    label=(question.prompt or "")[:120] or f"Question {question.id}",
                )
            )
            order += 1
    return built


def emit_interactive_video(spec: InteractiveVideoSpec) -> H5PPackage:
    """Build an importable ``.h5p`` Interactive Video from a chaptered transcript."""
    warnings = list(spec.warnings)

    if spec.video.mime not in KNOWN_VIDEO_MIMES:
        warnings.append(
            f"The video mime {spec.video.mime!r} is outside what H5P.Video plays natively "
            f"({', '.join(sorted(KNOWN_VIDEO_MIMES))}); the learner may see an empty player."
        )

    interactions = _interactions(spec, warnings)
    if not interactions:
        warnings.append("No knowledge checks were placed; the video has chapters only.")

    content: dict[str, object] = {
        "interactiveVideo": {
            "video": {
                "startScreenOptions": {
                    "title": sanitise_title(spec.title, fallback="Interactive video"),
                    "hideStartTitle": False,
                },
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
            "assets": {"interactions": interactions, "bookmarks": _bookmarks(spec)},
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
    stem = spec.source.filename.rsplit(".", 1)[0] or "interactive-video"
    return H5PPackage(
        content=write_h5p(manifest=manifest, content=content),
        filename=f"{stem}-interactive-video.h5p",
        warnings=warnings,
    )
