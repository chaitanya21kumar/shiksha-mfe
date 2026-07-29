"""Tests for the interactive video emitter (Module C.3).

Each case is named for the silent failure it guards against. Interactive Video is
the least forgiving target the engine has: a library outside its whitelist is not
rejected at import, it is *stripped*, so the video plays with the question quietly
missing — and an absent ``l10n`` block puts the word "undefined" on the player's
own controls.
"""

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.assessment.schema import Blank, Choice, FillBlankItem, KeyPoint, MCQItem, ShortAnswerItem
from app.chaptering.schema import Chapter
from app.interactive_video.emit import emit_interactive_video
from app.interactive_video.schema import ChapterCheck, InteractiveVideoSpec, VideoSource
from app.packaging.h5p.versions import ALLOWED_INTERACTION_LIBRARIES, INTERACTIVE_VIDEO_CLOSURE
from app.transcription.schema import TranscriptSource


def _mcq(qid="q1"):
    return MCQItem(
        id=qid,
        prompt="Which process turns liquid water into vapour?",
        points=1.0,
        choices=[
            Choice(id=f"{qid}-c1", text="Evaporation", is_correct=True),
            Choice(id=f"{qid}-c2", text="Condensation"),
        ],
    )


def _blank(qid="q2"):
    return FillBlankItem(
        id=qid, text="Vapour cools during [[1]].", points=1.0,
        blanks=[Blank(id=f"{qid}-b1", answers=["condensation"])],
    )


def _short(qid="q3"):
    return ShortAnswerItem(
        id=qid, prompt="Explain a sea breeze.", points=2.0,
        model_answer="land heats faster and sea to land",
        key_points=[
            KeyPoint(id=f"{qid}-k1", text="Land warms faster", accepted=["land heats"]),
            KeyPoint(id=f"{qid}-k2", text="Air moves inland", accepted=["sea to land"]),
        ],
    )


def _spec(checks=None, chapters=None, **overrides):
    base = dict(
        content_id="iv-1",
        source=TranscriptSource(filename="lecture.mp4", media_seconds=300.0),
        video=VideoSource(url="https://cdn.example.org/lecture.mp4"),
        title="Coastal Weather",
        language="en",
        chapters=chapters
        or [
            Chapter(index=1, start=0.0, end=90.0, title="The water cycle"),
            Chapter(index=2, start=90.0, end=300.0, title="Sea breezes"),
        ],
        checks=checks if checks is not None else [ChapterCheck(chapter_index=1, questions=[_mcq()])],
    )
    base.update(overrides)
    return InteractiveVideoSpec(**base)


def _content(package) -> dict:
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("content/content.json"))


def _manifest(package) -> dict:
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("h5p.json"))


# --- the package itself -------------------------------------------------------


def test_the_package_is_content_only_with_the_two_required_files():
    archive = zipfile.ZipFile(io.BytesIO(emit_interactive_video(_spec()).content))
    assert archive.namelist() == ["h5p.json", "content/content.json"]


def test_the_manifest_names_interactive_video_and_declares_its_whole_closure():
    manifest = _manifest(emit_interactive_video(_spec()))
    assert manifest["mainLibrary"] == "H5P.InteractiveVideo"
    declared = {(d["machineName"], d["majorVersion"], d["minorVersion"])
                for d in manifest["preloadedDependencies"]}
    assert declared == set(INTERACTIVE_VIDEO_CLOSURE)


def test_dragnbar_is_declared_because_interactions_do_not_render_without_it():
    # IV positions its interactions through DragNBar. Omitting it imports cleanly
    # and then shows no interactions at all.
    manifest = _manifest(emit_interactive_video(_spec()))
    assert any(d["machineName"] == "H5P.DragNBar" for d in manifest["preloadedDependencies"])


# --- the video source ---------------------------------------------------------


def test_the_video_is_referenced_by_url_not_bundled():
    content = _content(emit_interactive_video(_spec()))
    files = content["interactiveVideo"]["video"]["files"]
    assert files == [
        {"path": "https://cdn.example.org/lecture.mp4",
         "mime": "video/mp4",
         "copyright": {"license": "U"}}
    ]


def test_a_non_http_video_url_is_rejected_by_the_contract():
    # A relative or file:// path packages fine and then shows an empty player,
    # because the LMS serves the package from its own domain.
    with pytest.raises(ValidationError, match="http"):
        VideoSource(url="/var/media/lecture.mp4")


def test_a_mime_h5p_cannot_play_natively_is_warned_about():
    package = emit_interactive_video(_spec(video=VideoSource(url="https://x.test/a.mkv",
                                                             mime="video/x-matroska")))
    assert any("mime" in w for w in package.warnings)


# --- bookmarks are the chapter markers ---------------------------------------


def test_each_chapter_becomes_a_bookmark_at_its_start():
    content = _content(emit_interactive_video(_spec()))
    assert content["interactiveVideo"]["assets"]["bookmarks"] == [
        {"time": 0.0, "label": "The water cycle"},
        {"time": 90.0, "label": "Sea breezes"},
    ]


# --- interactions -------------------------------------------------------------


def test_a_question_is_placed_at_the_end_of_its_chapter_and_pauses_the_video():
    content = _content(emit_interactive_video(_spec()))
    interaction = content["interactiveVideo"]["assets"]["interactions"][0]
    assert interaction["duration"]["from"] == 90.0
    assert interaction["pause"] is True
    assert interaction["displayType"] == "button"


def test_every_emitted_interaction_uses_a_whitelisted_library():
    checks = [ChapterCheck(chapter_index=1, questions=[_mcq(), _blank()])]
    content = _content(emit_interactive_video(_spec(checks=checks)))
    for interaction in content["interactiveVideo"]["assets"]["interactions"]:
        assert interaction["action"]["library"] in ALLOWED_INTERACTION_LIBRARIES


def test_a_short_answer_is_left_out_because_essay_is_not_on_the_whitelist():
    # The failure this guards: H5P strips a non-whitelisted interaction at import,
    # so the video would play with the question silently missing.
    checks = [ChapterCheck(chapter_index=1, questions=[_mcq(), _short()])]
    package = emit_interactive_video(_spec(checks=checks))
    content = _content(package)
    libraries = [i["action"]["library"] for i in content["interactiveVideo"]["assets"]["interactions"]]
    assert "H5P.Essay 1.5" not in libraries
    assert len(libraries) == 1
    assert any("q3" in w and "Essay" in w for w in package.warnings)


def test_several_questions_on_one_chapter_do_not_stack_on_the_same_spot():
    checks = [ChapterCheck(chapter_index=1, questions=[_mcq("a"), _mcq("b"), _mcq("c")])]
    content = _content(emit_interactive_video(_spec(checks=checks)))
    xs = [i["x"] for i in content["interactiveVideo"]["assets"]["interactions"]]
    assert len(set(xs)) == len(xs)


def test_an_interaction_never_starts_at_or_past_the_final_frame():
    # A chapter ending exactly at the video end would place a check the learner
    # can never reach.
    chapters = [Chapter(index=1, start=0.0, end=300.0, title="All of it")]
    content = _content(emit_interactive_video(_spec(chapters=chapters)))
    assert content["interactiveVideo"]["assets"]["interactions"][0]["duration"]["from"] < 300.0


def test_each_interaction_carries_a_subcontent_id_and_metadata():
    content = _content(emit_interactive_video(_spec()))
    action = content["interactiveVideo"]["assets"]["interactions"][0]["action"]
    assert action["subContentId"]
    assert action["metadata"]["license"] == "U"


def test_a_video_with_no_placeable_questions_still_packages_with_a_warning():
    package = emit_interactive_video(_spec(checks=[]))
    content = _content(package)
    assert content["interactiveVideo"]["assets"]["interactions"] == []
    assert content["interactiveVideo"]["assets"]["bookmarks"]  # chapters still there
    assert any("chapters only" in w for w in package.warnings)


def test_a_check_naming_a_chapter_that_does_not_exist_is_rejected():
    with pytest.raises(ValidationError, match="not in this video"):
        _spec(checks=[ChapterCheck(chapter_index=99, questions=[_mcq()])])


# --- the fields the runtime reads without defaulting --------------------------


def test_l10n_is_written_out_in_full_so_the_player_never_shows_undefined():
    # h5p-interactive-video.js reads this.l10n.<key> with no fallback.
    content = _content(emit_interactive_video(_spec()))
    l10n = content["l10n"]
    assert len(l10n) == 47
    for key in ("play", "pause", "bookmarks", "endcardTitle", "videoProgressBar"):
        assert l10n[key]


def test_override_is_written_out_with_the_players_own_defaults():
    content = _content(emit_interactive_video(_spec()))
    assert content["override"] == {
        "autoplay": False,
        "loop": False,
        "showBookmarksmenuOnLoad": False,
        "showRewind10": False,
        "preventSkippingMode": "none",
        "deactivateSound": False,
    }


def test_summary_is_omitted_because_the_runtime_guards_it():
    # hasMainSummary() returns false when the group is absent, and H5P's own
    # published content omits it — emitting an empty one would add a library and
    # show the learner a summary with nothing in it.
    content = _content(emit_interactive_video(_spec()))
    assert "summary" not in content["interactiveVideo"]


def test_the_package_is_byte_reproducible():
    assert emit_interactive_video(_spec()).content == emit_interactive_video(_spec()).content
