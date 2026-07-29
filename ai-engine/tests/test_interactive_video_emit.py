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
    orphaned = ChapterCheck(chapter_index=99, questions=[_mcq()])
    with pytest.raises(ValidationError, match="not in this video"):
        _spec(checks=[orphaned])


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
    # Two independently built packages from two independently built specs. This is
    # what lets the other tests assert on the artifact rather than on a
    # re-implementation of it, so it has to be a real comparison of two builds.
    first = emit_interactive_video(_spec()).content
    second = emit_interactive_video(_spec()).content
    assert first, "an empty package would make the comparison below meaningless"
    assert first == second


# --- the fields whose absence fails silently in the player --------------------


def test_text_tracks_is_emitted_even_though_it_is_empty():
    # The constructor merges its default with a SHALLOW $.extend, so writing the
    # `video` object at all replaces `{textTracks: {videoTrack: []}}` wholesale —
    # and the last line of getCopyrights dereferences it with no guard, which H5P
    # core calls whenever the rights dialog is built.
    video = _content(emit_interactive_video(_spec()))["interactiveVideo"]["video"]
    assert video["textTracks"] == {"videoTrack": []}


def test_an_endscreen_is_emitted_so_the_learner_can_submit():
    # hasStar = editor || undefined !== assets.endscreens && assets.endscreens.length
    # With no endscreen the star, the end card and the submit button are dead code.
    assets = _content(emit_interactive_video(_spec()))["interactiveVideo"]["assets"]
    assert len(assets["endscreens"]) == 1
    assert assets["endscreens"][0]["time"] == 300.0


def test_the_endcard_l10n_strings_are_present_because_the_runtime_omits_them():
    # The runtime's own $.extend defaults 38 of the 47 keys; the twelve endcard
    # strings are not in that block, and they are exactly what the submit path
    # above puts on screen.
    l10n = _content(emit_interactive_video(_spec()))["l10n"]
    for key in (
        "endcardTitle", "endcardInformation", "endcardSubmitButton",
        "endcardSubmitMessage", "endcardTableRowAnswered", "endcardTableRowScore",
        "endcardAnsweredScore", "endcardInformationNoAnswers",
        "endcardInformationMustHaveAnswer", "endcardInformationOnSubmitButtonDisabled",
        "endCardTableRowSummaryWithScore", "endCardTableRowSummaryWithoutScore",
    ):
        assert l10n[key], f"{key} is one of the twelve the runtime does not default"


# --- model-written text is markup by the time H5P renders it ------------------


def test_a_chapter_title_is_escaped_before_it_becomes_a_bookmark_label():
    # addBookmark builds '<div class="h5p-bookmark-text">' + label + '</div>'.
    spec = _spec(chapters=[
        Chapter(index=1, start=0.0, end=90.0, title='Water <img src=x onerror=alert(1)> & ions'),
    ], checks=[])
    label = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["bookmarks"][0]["label"]
    assert "<img" not in label
    assert "&lt;img" in label and "&amp; ions" in label


def test_a_question_prompt_is_escaped_before_it_becomes_an_interaction_label():
    question = _mcq()
    question.prompt = "Is a < b <script>alert(1)</script>?"
    spec = _spec(checks=[ChapterCheck(chapter_index=1, questions=[question])])
    label = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"][0]["label"]
    assert "<script>" not in label
    assert "&lt;script&gt;" in label


# --- placement, when the timeline does not agree with the chapters ------------


def test_the_last_chapters_check_is_reachable_when_the_media_length_is_unknown():
    # media_seconds is optional: an OpenAI-compatible STT gateway need not report
    # a duration. The final chapter's end IS the end of the recording, so without
    # a margin its check appears on the last frame and is never reachable.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=None),
        chapters=[Chapter(index=1, start=0.0, end=60.0, title="Only chapter")],
        checks=[ChapterCheck(chapter_index=1, questions=[_mcq()])],
    )
    at = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"][0]
    assert at["duration"]["from"] == 59.0


def test_chapters_running_past_the_media_are_warned_about_not_silently_collapsed():
    # The transcribed upload and the streamed URL are two different files, which
    # the two-parameter endpoint openly invites.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=10.0),
        chapters=[
            Chapter(index=1, start=0.0, end=90.0, title="One"),
            Chapter(index=2, start=90.0, end=180.0, title="Two"),
        ],
        checks=[
            ChapterCheck(chapter_index=1, questions=[_mcq("q1")]),
            ChapterCheck(chapter_index=2, questions=[_mcq("q2")]),
        ],
    )
    package = emit_interactive_video(spec)
    assert any("run to 180.0s" in w and "10.0s" in w for w in package.warnings)
    content = _content(package)["interactiveVideo"]["assets"]
    # Marks past the end are unreachable, so they are pulled back too.
    assert all(b["time"] <= 10.0 for b in content["bookmarks"])
    # Both checks clamp to the same instant, so their buttons must NOT coincide.
    spots = {(i["x"], i["y"]) for i in content["interactions"]}
    assert len(spots) == len(content["interactions"])


def test_buttons_wrap_onto_rows_instead_of_stacking_past_the_fifth():
    # count=5 x three types on a single-chapter recording is fifteen questions on
    # one instant; clamping x at _MAX_X put every one past the fifth on one spot.
    questions = [_mcq(f"q{i}") for i in range(1, 16)]
    spec = _spec(
        chapters=[Chapter(index=1, start=0.0, end=90.0, title="One")],
        checks=[ChapterCheck(chapter_index=1, questions=questions)],
    )
    interactions = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"]
    assert len(interactions) == 15
    spots = {(i["x"], i["y"]) for i in interactions}
    assert len(spots) == 15, "every button must have its own place on the frame"


# --- the filename crosses into a header --------------------------------------


def test_a_non_ascii_source_filename_cannot_reach_the_content_disposition_header():
    # Starlette latin-1-encodes header values, so an unsanitised Hindi filename
    # raised UnicodeEncodeError *after* the package had been built successfully.
    spec = _spec(source=TranscriptSource(filename="व्याख्यान.mp4", media_seconds=300.0))
    filename = emit_interactive_video(spec).filename
    filename.encode("latin-1")  # must not raise
    assert filename == "interactive-video-interactive-video.h5p"


def test_a_filename_carrying_crlf_or_quotes_is_reduced_to_something_a_header_can_hold():
    spec = _spec(source=TranscriptSource(filename='a"; x="b\r\nX-Evil: 1.mp4', media_seconds=300.0))
    filename = emit_interactive_video(spec).filename
    assert '"' not in filename and "\r" not in filename and "\n" not in filename


def test_a_fill_blank_button_is_labelled_with_its_sentence_not_its_id():
    # `prompt` is optional on a fill-in-the-blanks question — the sentence carries
    # the instruction — so falling straight through to "Question q2" would put an
    # id on the learner's screen.
    spec = _spec(checks=[ChapterCheck(chapter_index=1, questions=[_blank()])])
    label = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"][0]["label"]
    assert label.startswith("Vapour cools during")


def test_an_interaction_window_never_runs_past_the_end_of_the_media():
    # InteractiveVideo.loaded rewrites any interaction whose duration.to exceeds
    # the real media length by PRESERVING the window and dragging `from`
    # backwards — so an unbounded `to` moves the check into earlier material.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=90.0),
        chapters=[
            Chapter(index=1, start=0.0, end=30.0, title="One"),
            Chapter(index=2, start=30.0, end=60.0, title="Two"),
            Chapter(index=3, start=60.0, end=90.0, title="Three"),
        ],
        checks=[
            ChapterCheck(chapter_index=i, questions=[_mcq(f"q{i}")]) for i in (1, 2, 3)
        ],
    )
    interactions = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"]
    assert [i["duration"]["from"] for i in interactions] == [30.0, 60.0, 89.0]
    assert all(i["duration"]["to"] <= 90.0 for i in interactions)
    # …and each window is still wide enough for the playhead to enter it.
    assert all(i["duration"]["to"] > i["duration"]["from"] for i in interactions)


def test_times_are_floored_so_rounding_cannot_push_them_past_the_media():
    # A 185.857-second recording rounds to 185.86 — 3 ms past the end, which is
    # exactly what the runtime's `duration.to > t` test fires on.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=185.85693324800002),
        chapters=[Chapter(index=1, start=0.0, end=185.85693324800002, title="Only")],
        checks=[ChapterCheck(chapter_index=1, questions=[_mcq()])],
    )
    content = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]
    media = 185.85693324800002
    assert content["interactions"][0]["duration"]["to"] <= media
    assert content["endscreens"][0]["time"] <= media
    assert all(b["time"] <= media for b in content["bookmarks"])


# --- the second audit: edges of the fixes above -------------------------------


def test_checks_whose_windows_overlap_do_not_share_a_button_position():
    # The first fix keyed the anti-stacking counter on the exact instant, which is
    # not the same question: two checks ten seconds apart share the screen for the
    # rest of their twenty-second windows, and both landed on the first slot.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=40.0),
        chapters=[
            Chapter(index=1, start=0.0, end=10.0, title="A"),
            Chapter(index=2, start=10.0, end=20.0, title="B"),
            Chapter(index=3, start=20.0, end=30.0, title="C"),
        ],
        checks=[ChapterCheck(chapter_index=i, questions=[_mcq(f"q{i}")]) for i in (1, 2, 3)],
    )
    interactions = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"]
    for i, one in enumerate(interactions):
        for other in interactions[i + 1 :]:
            share_screen = one["duration"]["from"] < other["duration"]["to"] and (
                other["duration"]["from"] < one["duration"]["to"]
            )
            if share_screen:
                assert (one["x"], one["y"]) != (other["x"], other["y"])


def test_a_slot_is_reused_once_its_window_has_closed():
    # The grid must not simply keep allocating: checks far apart in time should sit
    # in the same, most readable position rather than marching down the frame.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=400.0),
        chapters=[
            Chapter(index=1, start=0.0, end=100.0, title="A"),
            Chapter(index=2, start=100.0, end=200.0, title="B"),
        ],
        checks=[ChapterCheck(chapter_index=i, questions=[_mcq(f"q{i}")]) for i in (1, 2)],
    )
    interactions = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"]
    assert [(i["x"], i["y"]) for i in interactions] == [(20.0, 40.0), (20.0, 40.0)]


def test_the_start_screen_title_is_escaped_like_every_other_text_field():
    # The third string that reaches an H5P text field, and the one the first
    # escaping pass missed — it comes straight off a query parameter.
    spec = _spec(title='Weather <img src=x onerror=alert(1)> & tides')
    title = _content(emit_interactive_video(spec))["interactiveVideo"]["video"]["startScreenOptions"]["title"]
    assert "<img" not in title
    assert "&lt;img" in title and "&amp; tides" in title


def test_a_long_label_is_truncated_before_it_is_escaped():
    # Escaping first and cutting second slices through an entity and leaves a
    # dangling "&lt" on the learner's button.
    question = _mcq()
    question.prompt = "a < b " * 40
    spec = _spec(checks=[ChapterCheck(chapter_index=1, questions=[question])])
    label = _content(emit_interactive_video(spec))["interactiveVideo"]["assets"]["interactions"][0]["label"]
    assert "&lt;" in label
    assert not label.rstrip().endswith(("&", "&l", "&lt", "&a", "&am", "&amp"))


def test_an_absurd_media_length_is_bounded_rather_than_crashing():
    # media_seconds is a caller-supplied float. Flooring 1e308 raises OverflowError,
    # which would turn a typo into a 500 after the package had been built.
    spec = _spec(source=TranscriptSource(filename="lecture.mp4", media_seconds=1e308))
    package = emit_interactive_video(spec)  # must not raise
    assets = _content(package)["interactiveVideo"]["assets"]
    assert all(i["duration"]["to"] <= 30 * 24 * 3600 for i in assets["interactions"])
    assert assets["endscreens"][0]["time"] <= 30 * 24 * 3600


def test_a_filename_longer_than_a_header_line_is_trimmed():
    spec = _spec(source=TranscriptSource(filename="l" * 5000 + ".mp4", media_seconds=300.0))
    filename = emit_interactive_video(spec).filename
    assert len(filename) < 200
    filename.encode("latin-1")


def test_every_button_stays_inside_the_video_frame():
    # The earlier grid test asserted only that coordinates differ, which an
    # unbounded y satisfies trivially by walking off the bottom of the screen —
    # distinct and unclickable is not better than stacked and unclickable.
    # Enough to exhaust the grid: three chapters that all clamp to the same instant
    # because the declared media is far shorter than they are, twelve checks each.
    spec = _spec(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=60.0),
        chapters=[Chapter(index=i, start=(i - 1) * 100.0, end=i * 100.0, title=f"C{i}") for i in (1, 2, 3)],
        checks=[
            ChapterCheck(
                chapter_index=i,
                questions=[_mcq(f"q{i}-{j}") for j in range(12)],
            )
            for i in (1, 2, 3)
        ],
    )
    package = emit_interactive_video(spec)
    interactions = _content(package)["interactiveVideo"]["assets"]["interactions"]
    assert interactions
    for one in interactions:
        assert one["x"] + one["width"] <= 100, "button runs off the right of the frame"
        assert one["y"] + one["height"] <= 100, "button runs off the bottom of the frame"


def test_the_caller_is_told_when_too_many_checks_share_the_screen():
    # Reusing a position is a real loss of function, so it is reported rather than
    # left for someone to discover in the player.
    questions = [_mcq(f"q{i}") for i in range(1, 26)]
    spec = _spec(
        chapters=[Chapter(index=1, start=0.0, end=90.0, title="One")],
        checks=[ChapterCheck(chapter_index=1, questions=questions)],
    )
    assert any("share the screen at once" in w for w in emit_interactive_video(spec).warnings)
