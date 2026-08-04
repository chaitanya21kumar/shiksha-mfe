"""How the media and its captions are referenced, and the ways that went wrong.

Every defect guarded here fails *silently*: the package imports, the player opens,
and the learner sees an empty frame or a missing caption with nothing in any log to
say why. They were found by auditing the shipped H5P runtime rather than by any
test failing, which is why each one gets a test naming the failure it prevents.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.assessment.schema import Choice, MCQItem
from app.chaptering.schema import Chapter
from app.interactive_video.emit import emit_interactive_video
from app.interactive_video.schema import ChapterCheck, InteractiveVideoSpec, VideoSource
from app.transcription.schema import TranscriptSource


def _mcq(qid="q1") -> MCQItem:
    return MCQItem(
        id=qid,
        prompt="Which process returns water to the air?",
        choices=[
            Choice(id=f"{qid}-c1", text="Evaporation", is_correct=True),
            Choice(id=f"{qid}-c2", text="Freezing"),
        ],
    )


def _spec(video: VideoSource) -> InteractiveVideoSpec:
    return InteractiveVideoSpec(
        content_id="iv-1",
        source=TranscriptSource(filename="lesson.mp4", media_seconds=300.0),
        title="Water cycle",
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        chapters=[
            Chapter(index=1, start=0.0, end=150.0, title="Evaporation", text="a"),
            Chapter(index=2, start=150.0, end=300.0, title="Condensation", text="b"),
        ],
        checks=[ChapterCheck(chapter_index=1, questions=[_mcq()])],
        video=video,
    )


def _video(package) -> dict:
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("content/content.json"))["interactiveVideo"]["video"]


# --- the URL that looks fine and is not -----------------------------------------


@pytest.mark.parametrize(
    "padded",
    ["  https://cdn.test/a.mp4", "\thttps://cdn.test/a.mp4", "https://cdn.test/a.mp4  "],
)
def test_surrounding_whitespace_is_removed_from_the_media_url(padded):
    """The contract used to validate the stripped value and store the padded one.

    H5P decides whether a path is absolute with `/^[a-z0-9]+:\\/\\//i`. One leading
    space fails that test, so `getPath` treats the URL as relative to the package,
    finds nothing, and the learner gets an empty player with no error anywhere.
    """
    assert VideoSource(url=padded).url == "https://cdn.test/a.mp4"


def test_surrounding_whitespace_is_removed_from_the_subtitles_url():
    source = VideoSource(url="https://cdn.test/a.mp4", subtitles_url="  https://cdn.test/a.vtt ")
    assert source.subtitles_url == "https://cdn.test/a.vtt"


def test_a_non_http_url_is_still_refused():
    with pytest.raises(ValidationError):
        VideoSource(url="  ./local/a.mp4  ")


def test_a_non_http_subtitles_url_is_refused():
    with pytest.raises(ValidationError):
        VideoSource(url="https://cdn.test/a.mp4", subtitles_url="captions.vtt")


# --- the container type ---------------------------------------------------------


def test_the_container_type_reaches_the_package():
    """`mime` existed on the contract but no endpoint could set it, so it was always
    "video/mp4" — safe only because that happened to be the right default."""
    video = _video(emit_interactive_video(_spec(
        VideoSource(url="https://cdn.test/a.webm", mime="video/webm")
    )))
    assert video["files"][0]["mime"] == "video/webm"


def test_a_container_h5p_cannot_play_is_warned_about():
    """This warning was unreachable while `mime` was hard-coded: the only value it
    could ever hold was one of the three that never trigger it."""
    package = emit_interactive_video(_spec(
        VideoSource(url="https://cdn.test/a.mkv", mime="video/x-matroska")
    ))
    assert any("video/x-matroska" in w and "natively" in w for w in package.warnings)


@pytest.mark.parametrize("mime", ["video/mp4", "video/webm", "video/ogg"])
def test_a_container_h5p_plays_natively_produces_no_warning(mime):
    package = emit_interactive_video(_spec(VideoSource(url="https://cdn.test/a", mime=mime)))
    assert not any("natively" in w for w in package.warnings)


# --- captions -------------------------------------------------------------------


def test_no_subtitles_still_emits_the_key_the_runtime_dereferences():
    """`video.textTracks` must exist even when empty.

    The constructor merges its defaults with a *shallow* extend, so writing a
    `video` object at all replaces the default wholesale — and getCopyrights reads
    the key unguarded, which is a TypeError on the learner's page.
    """
    video = _video(emit_interactive_video(_spec(VideoSource(url="https://cdn.test/a.mp4"))))
    assert video["textTracks"] == {"videoTrack": []}


def test_a_subtitle_track_carries_every_field_the_runtime_reads():
    """`kind` in particular is not optional in practice: html5.js reads it off the
    first entry while building the <track> element and throws without it."""
    video = _video(emit_interactive_video(_spec(VideoSource(
        url="https://cdn.test/a.mp4",
        subtitles_url="https://cdn.test/a.vtt",
        subtitles_language="en",
    ))))
    track = video["textTracks"]["videoTrack"][0]
    assert set(track) == {"label", "kind", "srcLang", "track"}
    assert track["kind"] == "subtitles"
    assert track["srcLang"] == "en"
    assert track["track"]["path"] == "https://cdn.test/a.vtt"
    assert track["track"]["mime"] == "text/vtt"


def test_the_subtitle_language_is_carried_through():
    video = _video(emit_interactive_video(_spec(VideoSource(
        url="https://cdn.test/a.mp4",
        subtitles_url="https://cdn.test/a.vtt",
        subtitles_language="mr",
    ))))
    assert video["textTracks"]["videoTrack"][0]["srcLang"] == "mr"


def test_referencing_subtitles_warns_about_cross_origin_reads():
    """The track is fetched by the browser from another host, so a missing CORS
    header leaves the caption menu present and permanently empty."""
    package = emit_interactive_video(_spec(VideoSource(
        url="https://cdn.test/a.mp4", subtitles_url="https://cdn.test/a.vtt"
    )))
    assert any("cross-origin" in w for w in package.warnings)


# --- escaping -------------------------------------------------------------------


def test_the_media_url_is_escaped_like_every_other_string():
    """H5P.Video runs the path through `$cleaner.html(path).text()`, so an
    unescaped entity does not survive the round trip."""
    video = _video(emit_interactive_video(_spec(
        VideoSource(url="https://cdn.test/a.mp4?x=1&y=2")
    )))
    assert video["files"][0]["path"] == "https://cdn.test/a.mp4?x=1&amp;y=2"


# --- the mistake that started this ----------------------------------------------


def test_a_mistyped_field_is_refused_rather_than_dropped():
    """Found by making it: `ChapterCheck(chapter_index=1, question=...)` — singular —
    was silently accepted, leaving `questions` empty and producing a video with no
    checks at all, reported only as a warning. A caller POSTing the wrong key
    deserves a 422, not a quietly emptier package.
    """
    question = _mcq()
    with pytest.raises(ValidationError):
        ChapterCheck(chapter_index=1, question=question)


def test_the_correct_field_still_works():
    check = ChapterCheck(chapter_index=1, questions=[_mcq()])
    assert len(check.questions) == 1
