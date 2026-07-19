"""Tests for mapping an `AssessmentSet` onto a SCORM 1.2 package.

The recurring hazard here is that **nothing validates an interaction pattern**.
Moodle literally ships ``CMIFeedback = CMIString256; // This must be redefined``
and Open edX ignores interactions altogether — so a SCORM 2004-style pattern
raises no error anywhere. It is stored verbatim and rendered as junk in the
report. These encoders are the only guard, which is why the 2004-regression test
below exists.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone

import pytest

from app.assessment.emit import EmptyAssessmentError, emit_scorm
from app.assessment.schema import (
    AssessmentSet,
    AssessmentSource,
    Blank,
    Choice,
    FillBlankItem,
    MatchItem,
    MatchSource,
    MatchTarget,
    KeyPoint,
    MCQItem,
    ScoreBand,
    ShortAnswerItem,
)


def _set(questions, **overrides) -> AssessmentSet:
    kwargs = {
        "assessment_id": "a-1",
        "source": AssessmentSource(filename="lesson.pdf", title="Lesson", page_count=2),
        "generator": "test",
        "model": "m",
        "generated_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
        "questions": questions,
    }
    kwargs.update(overrides)
    return AssessmentSet(**kwargs)


def _mcq(**overrides) -> MCQItem:
    kwargs = {
        "id": "q1",
        "prompt": "Which one?",
        "choices": [
            Choice(id="q1-c1", text="Right", is_correct=True),
            Choice(id="q1-c2", text="Wrong"),
        ],
    }
    kwargs.update(overrides)
    return MCQItem(**kwargs)


def _match(**overrides) -> MatchItem:
    kwargs = {
        "id": "q1",
        "prompt": "Match them.",
        "points": 2.0,
        "sources": [
            MatchSource(id="q1-s1", text="Heart", target_id="q1-t3"),
            MatchSource(id="q1-s2", text="Lungs", target_id="q1-t1"),
        ],
        "targets": [
            MatchTarget(id="q1-t1", text="Gas exchange"),
            MatchTarget(id="q1-t2", text="Filtration"),
            MatchTarget(id="q1-t3", text="Pumps blood"),
        ],
    }
    kwargs.update(overrides)
    return MatchItem(**kwargs)


def _blank(**overrides) -> FillBlankItem:
    kwargs = {
        "id": "q1",
        "text": "The capital of India is [[1]].",
        "blanks": [Blank(id="q1-b1", answers=["New Delhi", "Delhi"])],
    }
    kwargs.update(overrides)
    return FillBlankItem(**kwargs)


def _payload(assessment: AssessmentSet) -> dict:
    package = emit_scorm(assessment)
    index = zipfile.ZipFile(io.BytesIO(package.content)).read("index.html").decode("utf-8")
    raw = re.search(r'id="assessment-data">(.*?)</script>', index, re.S).group(1)
    return json.loads(raw)


def _interactions(assessment: AssessmentSet) -> list[dict]:
    return [i for q in _payload(assessment)["questions"] for i in q["interactions"]]


# --- the patterns are 1.2, not 2004 ------------------------------------------


def test_a_single_answer_choice_is_one_character():
    # The correct choice is index 0 -> "a".
    assert _interactions(_set([_mcq()]))[0]["correct_responses"] == ["a"]


def test_a_multi_answer_choice_is_comma_separated_characters():
    item = _mcq(
        single_answer=False,
        choices=[
            Choice(id="q1-c1", text="a", is_correct=True),
            Choice(id="q1-c2", text="b"),
            Choice(id="q1-c3", text="c", is_correct=True),
        ],
    )
    assert _interactions(_set([item]))[0]["correct_responses"] == ["a,c"]


def test_a_matching_pattern_uses_a_period_within_a_pair_and_a_comma_between():
    # sources s1,s2 -> a,b ; targets t1,t2,t3 -> a,b,c.
    # s1 points at t3 ("a.c"), s2 points at t1 ("b.a").
    assert _interactions(_set([_match()]))[0]["correct_responses"] == ["a.c,b.a"]


def test_no_emitted_pattern_uses_scorm_2004_syntax():
    # [,] and [.] and {case_matters=} are 2004. In a 1.2 package they raise NO
    # error anywhere -- Moodle does not validate the format and Open edX ignores
    # interactions -- so they would simply be stored and rendered as junk. This is
    # the only thing standing between us and that.
    assessment = _set([_mcq(id="q1"), _match(id="q2"), _blank(id="q3")])
    rendered = json.dumps(_payload(assessment))
    assert "[,]" not in rendered
    assert "[.]" not in rendered
    assert "case_matters" not in rendered


def test_each_question_type_maps_to_its_scorm_interaction_type():
    assessment = _set([_mcq(id="q1"), _match(id="q2"), _blank(id="q3")])
    assert [i["type"] for i in _interactions(assessment)] == ["choice", "matching", "fill-in"]


# --- fill-in: one interaction per blank --------------------------------------


def test_a_blank_becomes_its_own_interaction_keyed_on_the_blanks_id():
    # SCORM 1.2's fill-in is a flat string with no construct for several blanks,
    # so N blanks become N interactions. The interaction count exceeding the
    # question count is correct, not a bug.
    item = _blank(
        text="[[1]] and [[2]].",
        blanks=[Blank(id="q1-b1", answers=["one"]), Blank(id="q1-b2", answers=["two"])],
    )
    interactions = _interactions(_set([item]))
    assert [i["id"] for i in interactions] == ["q1-b1", "q1-b2"]
    assert all(i["type"] == "fill-in" for i in interactions)


def test_every_accepted_answer_becomes_its_own_pattern_record():
    # This is the mechanism 1.2 gives for alternatives; there is no delimiter.
    assert _interactions(_set([_blank()]))[0]["correct_responses"] == ["New Delhi", "Delhi"]


def test_a_blanks_weighting_is_its_share_of_the_questions_points():
    item = _blank(
        text="[[1]] and [[2]].",
        points=2.0,
        blanks=[Blank(id="q1-b1", answers=["one"]), Blank(id="q1-b2", answers=["two"])],
    )
    assert [i["weighting"] for i in _interactions(_set([item]))] == ["1", "1"]


# --- reporting degrades, the assessment never does ---------------------------


def test_a_question_with_too_many_options_still_renders_but_is_not_reported():
    # SCORM 1.2 identifies an option with a SINGLE character, so there is no 37th.
    # H5P would drop the question; we own the player, so it is asked and scored --
    # only the report loses it.
    choices = [Choice(id=f"q1-c{i}", text=f"option {i}", is_correct=(i == 0)) for i in range(40)]
    package = emit_scorm(_set([_mcq(choices=choices)]))

    assert any("more than 36 options" in w for w in package.warnings)
    payload = _payload(_set([_mcq(choices=choices)]))
    assert payload["questions"][0]["interactions"] == []
    assert len(payload["questions"][0]["choices"]) == 40  # still asked


def test_a_weighting_over_the_decimal_limit_is_clamped_with_a_warning():
    # CMIDecimal caps at three integer digits, so points > 999 fails on format
    # before it fails on range.
    package = emit_scorm(_set([_mcq(points=500.0)]))
    assert any("clamped" in w for w in package.warnings)


# --- LaTeX: rendered, not dropped --------------------------------------------


def test_a_latex_question_is_kept_and_warned_about_rather_than_dropped():
    # The deliberate divergence from emit_h5p. SCORM has no maths support and no
    # LMS supplies a renderer, so the choice is between showing the source and
    # withholding the question -- and we own the player.
    package = emit_scorm(_set([_mcq(prompt=r"Solve \(x^2=4\).", has_latex=True)]))
    assert any("LaTeX" in w and "q1" in w for w in package.warnings)
    payload = _payload(_set([_mcq(prompt=r"Solve \(x^2=4\).", has_latex=True)]))
    assert len(payload["questions"]) == 1
    assert payload["questions"][0]["has_latex"] is True


def test_multi_line_latex_is_not_dropped_the_way_h5p_drops_it():
    item = _mcq(prompt="Solve \\[\n x = 1 \n\\] now.", has_latex=True)
    assert len(_payload(_set([item]))["questions"]) == 1


# --- the payload the player reads --------------------------------------------


def test_the_rubric_travels_with_the_package_because_scorm_has_no_slot_for_it():
    bands = [
        ScoreBand(from_percent=0, to_percent=59, feedback="Try again"),
        ScoreBand(from_percent=60, to_percent=100, feedback="Mastered"),
    ]
    payload = _payload(_set([_mcq()], pass_percentage=60, score_bands=bands))
    assert [b["feedback"] for b in payload["score_bands"]] == ["Try again", "Mastered"]
    assert payload["pass_percentage"] == 60


def test_a_default_rubric_is_derived_when_none_is_supplied():
    payload = _payload(_set([_mcq()], pass_percentage=70))
    assert [(b["from_percent"], b["to_percent"]) for b in payload["score_bands"]] == [(0, 69), (70, 100)]


def test_the_explanation_travels_because_there_is_no_cmi_slot_for_it():
    payload = _payload(_set([_mcq(explanation="Because the passage says so.")]))
    assert payload["questions"][0]["explanation"] == "Because the passage says so."


def test_max_points_is_authoritative_unlike_the_h5p_path():
    # We own the grader, so points is honoured exactly -- there is no equivalent
    # of emit_h5p's "H5P scores this out of a different total" warning.
    assessment = _set([_mcq(id="q1", points=1.0), _match(id="q2", points=2.0)])
    package = emit_scorm(assessment)
    assert _payload(assessment)["max_points"] == pytest.approx(3.0)
    assert not any("points" in w and "not" in w for w in package.warnings)


def test_each_option_carries_the_character_scorm_will_report_it_as():
    # The player reads these back rather than reimplementing the alphabet.
    payload = _payload(_set([_match()]))
    assert [s["char"] for s in payload["questions"][0]["sources"]] == ["a", "b"]
    assert [t["char"] for t in payload["questions"][0]["targets"]] == ["a", "b", "c"]


# --- escaping ----------------------------------------------------------------


def test_a_closing_script_tag_in_a_question_cannot_break_out_of_the_data_island():
    # HTML escaping does not apply inside a <script> block: the tokenizer ends the
    # block at the first literal "</script". The text comes from a tenant's
    # uploaded document, so this is a real injection path.
    item = _mcq(prompt="</script><img src=x onerror=alert(1)>")
    package = emit_scorm(_set([item]))
    index = zipfile.ZipFile(io.BytesIO(package.content)).read("index.html").decode("utf-8")

    # Exactly the three script tags we wrote -- no fourth conjured by the payload.
    assert index.count("</script>") == 3
    assert "<img src=x" not in index
    # ...and it still round-trips back out intact.
    assert _payload(_set([item]))["questions"][0]["prompt"] == "</script><img src=x onerror=alert(1)>"


def test_devanagari_survives_the_round_trip():
    item = _mcq(prompt="जल चक्र क्या है?", choices=[Choice(id="q1-c1", text="वाष्पीकरण", is_correct=True), Choice(id="q1-c2", text="संघनन")])
    payload = _payload(_set([item]))
    assert payload["questions"][0]["prompt"] == "जल चक्र क्या है?"
    assert payload["questions"][0]["choices"][0]["text"] == "वाष्पीकरण"


# --- the package as a whole --------------------------------------------------


def test_the_package_carries_its_own_player():
    # The whole difference from H5P: the LMS supplies only an API, not a renderer.
    package = emit_scorm(_set([_mcq()]))
    assert sorted(zipfile.ZipFile(io.BytesIO(package.content)).namelist()) == [
        "imsmanifest.xml",
        "index.html",
        "scorm/api.js",
        "scorm/player.css",
        "scorm/player.js",
    ]


def test_the_manifest_lists_exactly_what_the_zip_holds():
    # A file in the manifest that is not in the ZIP, or the reverse, is the kind of
    # drift an importer notices and we would not.
    package = emit_scorm(_set([_mcq()]))
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    listed = set(re.findall(r'<file href="([^"]+)"', archive.read("imsmanifest.xml").decode()))
    assert listed == set(archive.namelist()) - {"imsmanifest.xml"}


def test_an_assessment_with_no_questions_is_an_error():
    assessment = _set([])
    with pytest.raises(EmptyAssessmentError):
        emit_scorm(assessment)


def test_the_filename_is_derived_from_the_source_document():
    assert emit_scorm(_set([_mcq()])).filename == "lesson-scorm.zip"


def test_the_same_assessment_always_emits_the_same_bytes():
    assessment = _set([_mcq(id="q1"), _match(id="q2"), _blank(id="q3")])
    assert emit_scorm(assessment).content == emit_scorm(assessment).content


# --- short answer ------------------------------------------------------------


def _short(**overrides) -> ShortAnswerItem:
    kwargs = {
        "id": "q1",
        "prompt": "Explain how a sea breeze forms.",
        "points": 2.0,
        "model_answer": "The land heats faster, so wind blows from sea to land.",
        "key_points": [
            KeyPoint(id="q1-k1", text="Land warms faster", accepted=["land heats", "land warms"]),
            KeyPoint(id="q1-k2", text="Air moves inland", accepted=["sea to land"]),
        ],
    }
    kwargs.update(overrides)
    return ShortAnswerItem(**kwargs)


def test_a_short_answer_reports_one_interaction_per_key_point():
    # Structurally the same choice as fill-in-the-blank, and for the same reason: a
    # single verdict would tell a teacher only that the answer was wrong, where one
    # interaction per point shows them WHICH points the learner made.
    interactions = _interactions(_set([_short()]))
    assert [i["id"] for i in interactions] == ["q1-k1", "q1-k2"]
    assert all(i["type"] == "fill-in" for i in interactions)


def test_every_accepted_form_becomes_a_correct_response_pattern():
    assert _interactions(_set([_short()]))[0]["correct_responses"] == ["land heats", "land warms"]


def test_a_key_points_weighting_is_its_own_weight():
    item = _short(
        points=4.0,
        key_points=[
            KeyPoint(id="q1-k1", text="Main", accepted=["land heats"], weight=3),
            KeyPoint(id="q1-k2", text="Detail", accepted=["sea to land"], weight=1),
        ],
    )
    assert [i["weighting"] for i in _interactions(_set([item]))] == ["3", "1"]


def test_no_pattern_can_exceed_the_scorm_string_limit():
    # The contract caps an accepted form at 60 characters precisely so this is
    # structurally impossible rather than something the emitter has to police.
    for interaction in _interactions(_set([_short()])):
        assert all(len(p) <= 255 for p in interaction["correct_responses"])


def test_the_player_gets_the_mark_scheme_and_the_model_answer():
    # It grades offline, and it shows the learner what a complete answer looks like.
    payload = _payload(_set([_short()]))["questions"][0]
    assert [k["id"] for k in payload["key_points"]] == ["q1-k1", "q1-k2"]
    assert payload["model_answer"]
    assert payload["max_chars"] >= payload["min_chars"]


def test_a_mixed_set_reports_interactions_for_both_fill_in_types():
    # THE REGRESSION TEST. A short answer also reports as type "fill-in", so a
    # reporting loop that dispatched on the INTERACTION type would route it into the
    # fill-in-the-blank handler, hit `question.blanks` on a question that has none,
    # and throw — losing every interaction while the quiz still rendered and still
    # showed a score. Dispatch is on the QUESTION type for exactly this reason.
    assessment = _set([_blank(id="q1"), _short(id="q2", key_points=[
        KeyPoint(id="q2-k1", text="A", accepted=["land heats"]),
        KeyPoint(id="q2-k2", text="B", accepted=["sea to land"]),
    ])])
    interactions = _interactions(assessment)
    assert [i["id"] for i in interactions] == ["q1-b1", "q2-k1", "q2-k2"]


def test_a_short_answer_needing_latex_is_kept_rather_than_dropped():
    # The divergence from the H5P path: we own this player, so the source is shown
    # as written instead of the question being withheld.
    package = emit_scorm(_set([_short(has_latex=True)]))
    assert any("LaTeX" in w and "q1" in w for w in package.warnings)
    assert len(_payload(_set([_short(has_latex=True)]))["questions"]) == 1


def test_max_points_includes_the_short_answer():
    assessment = _set([_mcq(id="q1", points=1.0), _short(id="q2", key_points=[
        KeyPoint(id="q2-k1", text="A", accepted=["land heats"]),
        KeyPoint(id="q2-k2", text="B", accepted=["sea to land"]),
    ])])
    assert _payload(assessment)["max_points"] == pytest.approx(3.0)
