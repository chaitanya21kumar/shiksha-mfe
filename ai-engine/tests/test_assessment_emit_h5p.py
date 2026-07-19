"""Tests for mapping an `AssessmentSet` onto an H5P Question Set.

Most of these guard against *silent* failures. An H5P package with the wrong key
or the wrong default imports cleanly and then misbehaves inside the LMS, so
"it imported" proves very little; these assert the specific things that would
otherwise only surface in front of a learner.

The round-trip tests are the centrepiece: they re-implement H5P's real parsers
(read from ``H5P.Blanks-1.14/js/blanks.js`` and ``H5P.DragText-1.10``) and assert
that what we wrote is what H5P will read back.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from datetime import datetime, timezone

import pytest

from app.assessment.emit import EmptyAssessmentError, emit_h5p
from app.packaging.h5p import ALLOWED_QUESTION_LIBRARIES
from app.assessment.schema import (
    AssessmentSet,
    AssessmentSource,
    Blank,
    Choice,
    FillBlankItem,
    KeyPoint,
    MatchItem,
    MatchSource,
    MatchTarget,
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


def _blank(**overrides) -> FillBlankItem:
    kwargs = {
        "id": "q1",
        "text": "Water becomes vapour during [[1]].",
        "blanks": [Blank(id="q1-b1", answers=["evaporation"])],
    }
    kwargs.update(overrides)
    return FillBlankItem(**kwargs)


def _match(**overrides) -> MatchItem:
    kwargs = {
        "id": "q1",
        "prompt": "Match them.",
        "sources": [
            MatchSource(id="q1-s1", text="Sun", target_id="q1-t1"),
            MatchSource(id="q1-s2", text="Cloud", target_id="q1-t2"),
        ],
        "targets": [
            MatchTarget(id="q1-t1", text="heats water"),
            MatchTarget(id="q1-t2", text="holds droplets"),
        ],
    }
    kwargs.update(overrides)
    return MatchItem(**kwargs)


def _content(assessment: AssessmentSet) -> dict:
    package = emit_h5p(assessment)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("content/content.json"))


def _params(assessment: AssessmentSet, index: int = 0) -> dict:
    return _content(assessment)["questions"][index]["params"]


# --- H5P's real parsers, re-implemented ---------------------------------------

# H5P tokenizes with `text.split(/(\*.*?\*)/)`. This is the same expression without
# the reluctant quantifier: JavaScript's `.` excludes line terminators, so excluding
# `\n` from the class keeps it byte-for-byte equivalent (checked across the cases in
# the round-trip tests below). Note `[^*]*` alone would NOT be equivalent -- it would
# match across a newline, where H5P's would not.
_TOKENIZER = re.compile(r"(\*[^*\n]*\*)")


def _h5p_parse_blanks(question_html: str) -> list[tuple[list[str], str | None]]:
    """Re-implementation of H5P.Blanks' tokenizer and parseSolution.

    From ``blanks.js``::

        text.split(/(\\*.*?\\*)/)          # tokenize
        var tipStart = solutionText.indexOf(':');
        solution = solutionText.slice(0, tipStart); tip = solutionText.slice(tipStart + 1);
        var solutions = solution.split('/');
        elem.innerHTML = solutions[i]; solutions[i] = elem.value;   # decodes html entities
    """
    parsed: list[tuple[list[str], str | None]] = []
    for token in _TOKENIZER.split(question_html):
        # Faithful to H5P: DragText's isAnswerPart is startsWith("*") &&
        # endsWith("*"), with no length floor, so "**" IS a token and lexes to an
        # empty answer. A `len(token) > 2` guard here would hide exactly the bug
        # this parser exists to catch.
        if not (token.startswith("*") and token.endswith("*") and len(token) >= 2):
            continue
        inner = token[1:-1]
        tip_start = inner.find(":")
        if tip_start != -1:
            solution, tip = inner[:tip_start], inner[tip_start + 1 :]
        else:
            solution, tip = inner, None
        solutions = [html.unescape(part.strip()) for part in solution.split("/")]
        parsed.append((solutions, html.unescape(tip) if tip is not None else None))
    return parsed


def _h5p_parse_dragtext(text_field: str) -> list[str]:
    """Re-implementation of H5P.DragText's droppable extraction.

    From ``parse-text.js``: the same ``/(\\*.*?\\*)/`` tokenizer, then ``lex()``
    strips the surrounding asterisks and pulls off ``:tip`` and ``\\+``/``\\-``.
    """
    droppables: list[str] = []
    for token in _TOKENIZER.split(text_field):
        # Faithful to H5P: DragText's isAnswerPart is startsWith("*") &&
        # endsWith("*"), with no length floor, so "**" IS a token and lexes to an
        # empty answer. A `len(token) > 2` guard here would hide exactly the bug
        # this parser exists to catch.
        if not (token.startswith("*") and token.endswith("*") and len(token) >= 2):
            continue
        inner = token[1:-1]
        inner = re.sub(r":([^\\*]+)", "", inner)
        droppables.append(inner.strip())
    return droppables


# --- the round trip ----------------------------------------------------------


def test_blanks_markup_round_trips_back_to_the_original_answers_and_tip():
    item = _blank(
        text="It [[1]] then [[2]].",
        blanks=[
            Blank(id="q1-b1", answers=["evaporates", "boils"], tip="Think of steam"),
            Blank(id="q1-b2", answers=["condenses"]),
        ],
    )
    recovered = _h5p_parse_blanks(_params(_set([item]))["questions"][0])
    assert recovered == [(["evaporates", "boils"], "Think of steam"), (["condenses"], None)]


def test_blanks_answers_containing_ampersands_survive_h5ps_entity_decode():
    # We escape everything on the way in; parseSolution decodes it on the way out.
    # If either half were missing, the learner could never type the right answer.
    item = _blank(blanks=[Blank(id="q1-b1", answers=["R&D"], tip="Tom & Jerry")])
    recovered = _h5p_parse_blanks(_params(_set([item]))["questions"][0])
    assert recovered == [(["R&D"], "Tom & Jerry")]


def test_dragtext_markup_round_trips_back_to_the_target_texts():
    content = _params(_set([_match()]))
    assert _h5p_parse_dragtext(content["textField"]) == ["heats water", "holds droplets"]


def test_dragtext_distractors_round_trip_and_exclude_matched_targets():
    item = _match(
        targets=[
            MatchTarget(id="q1-t1", text="heats water"),
            MatchTarget(id="q1-t2", text="holds droplets"),
            MatchTarget(id="q1-t3", text="melts rock"),
        ]
    )
    params = _params(_set([item]))
    assert _h5p_parse_dragtext(params["distractors"]) == ["melts rock"]


def test_each_match_pair_is_separated_by_a_newline_not_by_markup():
    # textField is declared widget:textarea with no tags, so H5P's importer runs
    # it through htmlspecialchars -- a <br/> would reach the learner as those five
    # literal characters. DragText converts newlines to <br/> itself, afterwards.
    params = _params(_set([_match()]))
    assert params["textField"] == "Sun — *heats water*\nCloud — *holds droplets*"
    assert "<" not in params["textField"]


# --- escaping / injection ----------------------------------------------------


def test_a_script_tag_in_a_stem_is_escaped():
    # H5P injects these fields as HTML, and the text originates in a tenant's
    # uploaded document, so this is a script-injection path into their LMS.
    item = _mcq(prompt="<script>alert(1)</script>")
    assert "<script>" not in _params(_set([item]))["question"]
    assert "&lt;script&gt;" in _params(_set([item]))["question"]


def test_a_script_tag_in_a_choice_is_escaped():
    item = _mcq(
        choices=[
            Choice(id="q1-c1", text="<script>alert(1)</script>", is_correct=True),
            Choice(id="q1-c2", text="safe"),
        ]
    )
    rendered = json.dumps(_params(_set([item]))["answers"])
    assert "<script>" not in rendered


def test_markup_in_a_blank_answer_or_tip_is_escaped():
    # The payload deliberately contains no * : or / -- those are separately
    # refused by the markup guard, which would mask whether escaping works.
    item = _blank(
        blanks=[
            Blank(
                id="q1-b1",
                answers=["<img src=x onerror=alert(1)>"],
                tip="<script>alert(1)</script>",
            )
        ]
    )
    rendered = _params(_set([item]))["questions"][0]
    assert "<img" not in rendered
    assert "<script>" not in rendered
    assert "&lt;img" in rendered


def test_markup_in_the_intro_title_is_escaped():
    # questionset.js concatenates introPage.title straight into the intro page's
    # HTML, and the title falls back to the uploaded document's filename -- so a
    # hostile filename is a real injection path.
    assessment = _set(
        [_mcq()],
        source=AssessmentSource(filename="<img src=x onerror=alert(1)>.pdf", page_count=1),
    )
    assert "<img" not in json.dumps(_content(assessment)["introPage"])


def test_markup_in_a_supplied_rubric_band_is_escaped():
    # A rubric can come from the caller, and the band text is injected into the
    # results screen as HTML.
    bands = [ScoreBand(from_percent=0, to_percent=100, feedback="<img src=x onerror=alert(1)>")]
    content = _content(_set([_mcq()], score_bands=bands))
    assert "<img" not in json.dumps(content["endGame"]["overallFeedback"])


def test_markup_in_a_match_term_is_escaped():
    item = _match(
        sources=[
            MatchSource(id="q1-s1", text="<img src=x onerror=alert(1)>", target_id="q1-t1"),
            MatchSource(id="q1-s2", text="plain", target_id="q1-t2"),
        ],
        targets=[MatchTarget(id="q1-t1", text="<b>x</b>"), MatchTarget(id="q1-t2", text="y")],
    )
    params = _params(_set([item]))
    assert "<img" not in params["textField"]
    assert "<b>" not in params["textField"]
    assert "&lt;b&gt;x&lt;" in params["textField"]


# --- the markup guard --------------------------------------------------------


@pytest.mark.parametrize("answer", ["m/s", "3:4", "a*b"])
def test_a_blank_answer_h5ps_markup_cannot_express_is_dropped_with_a_warning(answer):
    # None of these can be escaped away: blanks.js splits on / and : and re-pairs
    # asterisks, with no escape mechanism. Emitting them anyway would produce a
    # package that imports fine and grades wrongly.
    good = _mcq(id="q2")
    bad = _blank(id="q1", blanks=[Blank(id="q1-b1", answers=[answer])])
    package = emit_h5p(_set([bad, good]))
    content = json.loads(zipfile.ZipFile(io.BytesIO(package.content)).read("content/content.json"))
    assert [q["library"] for q in content["questions"]] == ["H5P.MultiChoice 1.16"]
    assert any("q1" in warning for warning in package.warnings)


def test_an_asterisk_in_the_sentence_is_dropped_with_a_warning():
    bad = _blank(id="q1", text="Multiply 3 * 4 to get [[1]].", blanks=[Blank(id="q1-b1", answers=["12"])])
    package = emit_h5p(_set([bad, _mcq(id="q2")]))
    assert any("q1" in warning for warning in package.warnings)


@pytest.mark.parametrize("term", ["a:b", "a*b"])
def test_a_match_term_h5ps_markup_cannot_express_is_dropped_with_a_warning(term):
    bad = _match(
        id="q1",
        targets=[MatchTarget(id="q1-t1", text=term), MatchTarget(id="q1-t2", text="ok")],
    )
    package = emit_h5p(_set([bad, _mcq(id="q2")]))
    assert any("q1" in warning for warning in package.warnings)


@pytest.mark.parametrize("answer", ["", "   "])
def test_an_empty_blank_answer_is_dropped_rather_than_emitted_as_a_hollow_gap(answer):
    # "**" is a well-formed token to H5P's tokenizer, so an empty answer becomes a
    # real gap that no learner input can ever satisfy -- silently, and worth a
    # point that can never be scored.
    bad = _blank(id="q1", blanks=[Blank(id="q1-b1", answers=[answer])])
    package = emit_h5p(_set([bad, _mcq(id="q2")]))
    assert any("q1" in warning for warning in package.warnings)
    content = json.loads(zipfile.ZipFile(io.BytesIO(package.content)).read("content/content.json"))
    assert [q["library"] for q in content["questions"]] == ["H5P.MultiChoice 1.16"]


def test_an_empty_match_target_is_dropped_rather_than_emitted_as_a_blank_draggable():
    bad = _match(
        id="q1",
        targets=[MatchTarget(id="q1-t1", text="  "), MatchTarget(id="q1-t2", text="ok")],
    )
    package = emit_h5p(_set([bad, _mcq(id="q2")]))
    assert any("q1" in warning for warning in package.warnings)


@pytest.mark.parametrize("tip", ["   ", ""])
def test_a_whitespace_only_tip_does_not_leave_a_trailing_colon(tip):
    # "answer:" is not a harmless no-op -- parseSolution finds the colon and reads
    # everything after it as a real, empty tip.
    item = _blank(blanks=[Blank(id="q1-b1", answers=["evaporation"], tip=tip)])
    rendered = _params(_set([item]))["questions"][0]
    assert "evaporation:" not in rendered
    assert _h5p_parse_blanks(rendered) == [(["evaporation"], None)]


def test_a_dropped_question_says_why_it_was_dropped():
    # Each cause is genuinely different; a warning that names the wrong one sends
    # the reader looking in the wrong place.
    markup = _blank(id="q1", blanks=[Blank(id="q1-b1", answers=["m/s"])])
    maths = _mcq(id="q2", prompt="Solve \\[\n x=1 \n\\].", has_latex=True)
    package = emit_h5p(_set([markup, maths, _mcq(id="q3")]))

    reasons = " ".join(package.warnings)
    assert "fill-in-the-blank markup" in reasons
    assert "multiple lines" in reasons


def test_no_emitted_gap_is_ever_empty():
    # The general form of the two tests above: whatever we emit, "**" must never
    # appear in it -- real H5P lexes that into an answer part carrying no text.
    assessment = _set([_mcq(id="q1"), _blank(id="q2"), _match(id="q3")])
    rendered = json.dumps(_content(assessment))
    assert "**" not in rendered


def test_a_newline_in_a_match_term_is_dropped_because_newlines_separate_pairs():
    bad = _match(
        id="q1",
        sources=[
            MatchSource(id="q1-s1", text="Sun\nrays", target_id="q1-t1"),
            MatchSource(id="q1-s2", text="Cloud", target_id="q1-t2"),
        ],
    )
    package = emit_h5p(_set([bad, _mcq(id="q2")]))
    assert any("q1" in warning for warning in package.warnings)


def test_the_multichoice_ui_labels_h5p_does_not_default_are_emitted():
    # multichoice.js reads UI.tipsLabel / UI.correctAnswer / UI.wrongAnswer but
    # carries no default for them -- they exist only as semantics defaults, which
    # the editor applies and a machine-written content.json never sees. Omitting
    # them renders the literal string "undefined" to the learner.
    ui = _params(_set([_mcq()]))["UI"]
    assert ui["tipsLabel"] and ui["correctAnswer"] and ui["wrongAnswer"]
    assert "undefined" not in json.dumps(ui)


def test_the_intro_page_carries_only_fields_question_set_declares():
    # validateGroup silently unsets any key with no matching semantics field, so
    # a stray key is dead weight that looks meaningful in review.
    intro = _content(_set([_mcq()]))["introPage"]
    assert set(intro) <= {"showIntroPage", "title", "introduction", "startButtonText", "backgroundImage"}


def test_dropping_every_question_is_an_error_not_an_empty_package():
    # A Question Set requires at least one question, so there is no valid empty
    # package to hand back -- this must surface as a 400, not a corrupt download.
    bad = _blank(blanks=[Blank(id="q1-b1", answers=["m/s"])])
    assessment = _set([bad])
    with pytest.raises(EmptyAssessmentError):
        emit_h5p(assessment)


def test_an_assessment_with_no_questions_is_an_error():
    assessment = _set([])
    with pytest.raises(EmptyAssessmentError):
        emit_h5p(assessment)


# --- the MultiChoice traps ---------------------------------------------------


def test_single_answer_maps_to_type_single_and_never_to_auto():
    assert _params(_set([_mcq(single_answer=True)]))["behaviour"]["type"] == "single"


def test_a_multi_answer_question_with_one_correct_choice_is_still_multi():
    # This is exactly why "auto" is wrong: it would infer single from the answer
    # count and silently render radio buttons, when the contract says the learner
    # may pick several.
    item = _mcq(
        single_answer=False,
        choices=[Choice(id="q1-c1", text="Only right one", is_correct=True), Choice(id="q1-c2", text="b")],
    )
    assert _params(_set([item]))["behaviour"]["type"] == "multi"


def test_single_answer_is_never_emitted():
    # Absent from semantics; multichoice.js assigns it from behaviour.type before
    # any read, and the 1.4 upgrade deleted it.
    assert "singleAnswer" not in _params(_set([_mcq()]))["behaviour"]


def test_every_answer_carries_an_explicit_correct_key():
    # multichoice.js deep-extends its defaults, and jQuery merges arrays by index.
    # Its defaults.answers[0] is {correct: true}, so an omitted key on the first
    # answer silently turns a wrong choice correct.
    item = _mcq(
        choices=[
            Choice(id="q1-c1", text="wrong first"),
            Choice(id="q1-c2", text="right second", is_correct=True),
        ]
    )
    answers = _params(_set([item]))["answers"]
    assert [a["correct"] for a in answers] == [False, True]
    assert all("correct" in a for a in answers)


def test_single_point_and_random_answers_are_emitted_explicitly():
    # The JS defaults contradict the semantics defaults, and a machine-written
    # content.json never goes through the editor that applies semantics.
    behaviour = _params(_set([_mcq()]))["behaviour"]
    assert behaviour["singlePoint"] is True
    assert behaviour["randomAnswers"] is True


def test_a_choices_feedback_becomes_its_chosen_feedback():
    item = _mcq(choices=[Choice(id="q1-c1", text="a", is_correct=True, feedback="Nice"), Choice(id="q1-c2", text="b")])
    answers = _params(_set([item]))["answers"]
    assert answers[0]["tipsAndFeedback"]["chosenFeedback"] == "Nice"


# --- Blanks / DragText behaviour ---------------------------------------------


def test_case_sensitive_is_always_emitted_because_h5p_defaults_it_the_other_way():
    # H5P defaults caseSensitive to true; our contract defaults it to false.
    # Omitting it would invert the author's intent.
    assert _params(_set([_blank()]))["behaviour"]["caseSensitive"] is False
    assert _params(_set([_blank(case_sensitive=True)]))["behaviour"]["caseSensitive"] is True


def test_dragtext_behaviour_carries_only_the_four_keys_it_defines():
    assert set(_params(_set([_match()]))["behaviour"]) == {
        "enableRetry",
        "enableSolutionsButton",
        "enableCheckButton",
        "instantFeedback",
    }


def test_a_match_without_distractors_omits_the_field_entirely():
    assert "distractors" not in _params(_set([_match()]))


# --- the rubric --------------------------------------------------------------


def test_score_bands_land_in_end_game_overall_feedback_as_a_flat_list():
    # Nesting this one level deeper is the classic H5P bug: it imports cleanly
    # and simply never shows any feedback.
    content = _content(_set([_mcq()]))
    assert isinstance(content["endGame"]["overallFeedback"], list)


def test_a_default_rubric_is_derived_from_the_pass_percentage():
    content = _content(_set([_mcq()], pass_percentage=60))
    assert content["passPercentage"] == 60
    assert [(b["from"], b["to"]) for b in content["endGame"]["overallFeedback"]] == [(0, 59), (60, 100)]


def test_a_pass_percentage_of_zero_does_not_produce_a_backwards_band():
    content = _content(_set([_mcq()], pass_percentage=0))
    assert [(b["from"], b["to"]) for b in content["endGame"]["overallFeedback"]] == [(0, 100)]


def test_a_supplied_rubric_is_used_verbatim():
    bands = [
        ScoreBand(from_percent=0, to_percent=49, feedback="Try again"),
        ScoreBand(from_percent=50, to_percent=79, feedback="Good"),
        ScoreBand(from_percent=80, to_percent=100, feedback="Excellent"),
    ]
    content = _content(_set([_mcq()], score_bands=bands))
    assert [b["feedback"] for b in content["endGame"]["overallFeedback"]] == [
        "Try again",
        "Good",
        "Excellent",
    ]


def test_an_explanation_becomes_a_full_range_feedback_band_on_the_question():
    params = _params(_set([_mcq(explanation="Because the passage says so.")]))
    assert params["overallFeedback"] == [
        {"from": 0, "to": 100, "feedback": "Because the passage says so."}
    ]


# --- scoring divergence ------------------------------------------------------


def test_h5ps_implied_total_is_warned_about_when_it_differs_from_ours():
    # H5P has no per-question weight: params.weight is absent from semantics and
    # gets stripped. Blanks scores one mark per blank, so this set is out of 3,
    # not 2. Mastery is a percentage, so it still behaves -- but say so.
    item = _blank(
        text="It [[1]] and then [[2]].",
        blanks=[Blank(id="q1-b1", answers=["a"]), Blank(id="q1-b2", answers=["b"])],
    )
    package = emit_h5p(_set([item, _mcq(id="q2")]))
    assert any("3 points" in warning for warning in package.warnings)


def test_no_scoring_warning_when_the_totals_agree():
    package = emit_h5p(_set([_mcq(id="q1"), _mcq(id="q2")]))
    assert not any("points" in warning for warning in package.warnings)


# --- LaTeX -------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [r"Solve \(x^2 + 1 = 0\).", r"Given $$E = mc^2$$ find m.", r"Then \[\int_0^1 x\,dx\] holds."],
)
def test_single_line_latex_is_kept(prompt):
    package = emit_h5p(_set([_mcq(prompt=prompt, has_latex=True)]))
    assert package.warnings == []


def test_multi_line_latex_is_dropped_because_mathdisplay_would_never_attach():
    # MathDisplay's trigger regex has no DOTALL flag, so a span containing a
    # newline never matches and the learner is shown raw LaTeX.
    item = _mcq(prompt="Solve \\[\n x = 1 \n\\] now.", has_latex=True)
    assessment = _set([item])
    with pytest.raises(EmptyAssessmentError):
        emit_h5p(assessment)


def test_multi_line_latex_in_a_choice_is_caught_not_just_in_the_stem():
    # has_latex means "stem OR answers", so checking only the stem would let a
    # broken choice through.
    item = _mcq(
        has_latex=True,
        choices=[
            Choice(id="q1-c1", text="\\(\n x=1 \n\\)", is_correct=True),
            Choice(id="q1-c2", text="two"),
        ],
    )
    assessment = _set([item])
    with pytest.raises(EmptyAssessmentError):
        emit_h5p(assessment)


def test_latex_inside_a_blank_answer_is_dropped_because_the_answer_is_a_text_box():
    item = _blank(has_latex=True, blanks=[Blank(id="q1-b1", answers=[r"\(x^2\)"])])
    package = emit_h5p(_set([item, _mcq(id="q2")]))
    assert any("text box" in warning for warning in package.warnings)


def test_latex_questions_do_not_add_a_mathdisplay_dependency():
    package = emit_h5p(_set([_mcq(prompt=r"Solve \(x=1\).", has_latex=True)]))
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(package.content)).read("h5p.json"))
    assert not any(d["machineName"] == "H5P.MathDisplay" for d in manifest["preloadedDependencies"])


# --- the package as a whole --------------------------------------------------


def test_every_emitted_question_uses_a_whitelisted_library_in_source_order():
    assessment = _set([_mcq(id="q1"), _blank(id="q2"), _match(id="q3")])
    assert [q["library"] for q in _content(assessment)["questions"]] == [
        "H5P.MultiChoice 1.16",
        "H5P.Blanks 1.14",
        "H5P.DragText 1.10",
    ]


def test_the_filename_is_derived_from_the_source_document():
    assert emit_h5p(_set([_mcq()])).filename == "lesson.h5p"


def test_an_awkward_source_filename_still_yields_a_safe_one():
    assessment = _set([_mcq()], source=AssessmentSource(filename="my report (final).pdf", page_count=1))
    assert emit_h5p(assessment).filename == "my-report-final.h5p"


def test_the_same_assessment_always_emits_the_same_bytes():
    assessment = _set([_mcq(id="q1"), _blank(id="q2"), _match(id="q3")])
    assert emit_h5p(assessment).content == emit_h5p(assessment).content


# --- short answer -> H5P.Essay -----------------------------------------------


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


def test_a_short_answer_becomes_an_essay_the_question_set_will_accept():
    assert _content(_set([_short()]))["questions"][0]["library"] == "H5P.Essay 1.5"


def test_the_essay_library_is_on_the_installed_whitelist():
    # questions[].library is matched by exact string equality against a whitelist
    # baked into the installed Question Set — a version it lacks is a hard rejection.
    assert "H5P.Essay 1.5" in ALLOWED_QUESTION_LIBRARIES


def test_the_package_declares_essay_and_the_matcher_it_depends_on():
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(emit_h5p(_set([_short()])).content)).read("h5p.json"))
    declared = {d["machineName"] for d in manifest["preloadedDependencies"]}
    # TextUtilities is the easy one to forget: three other libraries already pull it
    # in, but it is what Essay's isIsolated lives in, and without it the matcher is
    # unusable.
    assert {"H5P.Essay", "H5P.TextUtilities"} <= declared


def test_every_keyword_carries_a_complete_options_group():
    # essay.js reads alternativeGroup.options with no `|| {}` guard and dereferences
    # .points and .occurrences. A missing group is a TypeError that takes the whole
    # activity down; a missing points makes the score NaN. Neither degrades — both
    # are fatal, and semantics defaults do not apply to a machine-written file.
    for keyword in _params(_set([_short()]))["keywords"]:
        assert {"points", "occurrences", "caseSensitive"} <= set(keyword["options"])
        assert keyword["options"]["points"] is not None
        assert keyword["options"]["occurrences"] == 1


def test_keyword_matching_is_forced_case_insensitive():
    # H5P defaults this to true. A learner should not lose a mark for starting a
    # sentence in lower case.
    params = _params(_set([_short()]))
    assert all(k["options"]["caseSensitive"] is False for k in params["keywords"])
    assert params["behaviour"]["overrideCaseSensitive"] == "off"


def test_percentage_passing_is_present_so_a_blank_answer_does_not_report_as_passed():
    # Omitted, essay.js computes `undefined * scoreMax / 100 || 0` -> 0, and every
    # submission including an empty one reports success.
    assert _params(_set([_short()]))["behaviour"]["percentagePassing"] == 100


def test_percentage_mastering_is_absent_so_the_max_score_is_our_weights():
    # Essay reads `percentageMastering === undefined ? scoreMax : pct * scoreMax/100`,
    # so any value below 100 would LOWER the max score rather than raise a threshold.
    assert "percentageMastering" not in _params(_set([_short()]))["behaviour"]


def test_input_field_size_is_the_string_the_select_declares():
    assert _params(_set([_short()]))["behaviour"]["inputFieldSize"] == "10"


def test_the_essays_overall_feedback_is_flat_like_every_other_type():
    # Essay declares overallFeedback as a group wrapping a list — exactly as
    # MultiChoice does — and H5P collapses single-field groups, which is why the
    # Hub's own authored content emits it flat.
    params = _params(_set([_short(explanation="Because the passage says so.")]))
    assert isinstance(params["overallFeedback"], list)


def test_the_solution_group_stays_nested_because_it_has_two_fields():
    # The flattening rule applies to single-field groups only, so `solution` keeps
    # its shape where `overallFeedback` loses its wrapper.
    solution = _params(_set([_short()]))["solution"]
    assert set(solution) == {"introduction", "sample"}


def test_h5ps_max_score_equals_our_points_so_no_scale_warning_is_emitted():
    # A replica of essay.js's toPoints/computeScore: max(1, sum(points * occurrences)).
    item = _short()
    params = _params(_set([item]))
    implied = max(1, sum(k["options"]["points"] * k["options"]["occurrences"] for k in params["keywords"]))
    assert implied == item.points
    assert not any("points" in w for w in emit_h5p(_set([item])).warnings)


def test_a_short_answer_needing_latex_is_dropped_with_a_warning():
    # The learner answers in a plain textarea: there is no markup for MathDisplay to
    # typeset and no way to type LaTeX into it. It still ships in the SCORM package.
    package = emit_h5p(_set([_short(id="q1", has_latex=True), _mcq(id="q2")]))
    assert any("q1" in w and "plain-text" in w for w in package.warnings)


def test_an_accepted_form_containing_an_ampersand_round_trips():
    # Escaped on the way in; Essay's computeResults maps every alternative through
    # htmlDecode before matching, so both sides arrive decoded.
    item = _short(key_points=[
        KeyPoint(id="q1-k1", text="R&D matters", accepted=["r&d spending"]),
        KeyPoint(id="q1-k2", text="Other", accepted=["other thing"]),
    ], model_answer="r&d spending and other thing")
    keyword = _params(_set([item]))["keywords"][0]["keyword"]
    assert "&amp;" in keyword
    assert html.unescape(keyword) == "r&d spending"
