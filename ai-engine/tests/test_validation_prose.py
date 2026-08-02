"""The spelling gateway the mentors asked for after the midpoint review.

The interesting tests here are the ones about what is *not* flagged. A spelling
checker that fires on subject vocabulary, compound adjectives and proper nouns is
worse than no checker at all: a teacher stops reading the warnings, and then stops
reading the real ones too.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.assessment.schema import Choice, MCQItem
from app.summarization.schema import (
    DocumentInsights,
    GlossaryTerm,
    InsightsSource,
    OutlineSection,
)
from app.validation import check_assessment, check_insights, supported_languages
from app.validation.prose import ProseChecker, parts_of, words_in
from tests.factories import make_set

pytestmark = pytest.mark.skipif(
    "en" not in supported_languages(),
    reason="needs the optional spelling extra: pip install 'lms-ai-engine[spelling]'",
)

SOURCE = (
    "The Calvin cycle is light-independent and takes place in the stroma. "
    "Evapotranspiration moves water from the Deccan plateau into the atmosphere."
)


def insights(**overrides) -> DocumentInsights:
    fields = {
        "source": InsightsSource(filename="lesson.pdf", page_count=1),
        "generator": "test",
        "model": "m",
        "generated_at": datetime.now(timezone.utc),
        "summary": "",
        "key_takeaways": [],
        "glossary": [],
        "outline": [],
    }
    fields.update(overrides)
    return DocumentInsights(**fields)


# --- what it catches ------------------------------------------------------------


def test_a_plain_misspelling_is_reported_with_a_suggestion():
    report = check_insights(insights(summary="This sentance is wrong."), SOURCE)
    assert report.status == "passed_with_warnings"
    issue = next(i for i in report.issues if i.actual == "sentance")
    assert issue.suggestion == "sentence"
    assert issue.field_path == "summary"
    assert issue.severity == "warning"


def test_a_misspelling_inside_a_compound_is_reported_as_the_wrong_piece():
    """Correcting "light-independant" should offer "independent", not try to find a
    dictionary entry for the whole phrase."""
    report = check_insights(insights(summary="It is light-independant."), SOURCE)
    issue = next(i for i in report.issues if i.suggestion == "independent")
    assert issue.actual == "independant"


def test_every_generated_field_is_reached():
    report = check_insights(
        insights(
            summary="aaaa bbbb misspeled",
            key_takeaways=["another mistaek here"],
            glossary=[GlossaryTerm(term="stroma", definition="Where the wrogn thing happens.")],
            outline=[OutlineSection(title="Ovreview", points=["A furthur error."])],
        ),
        SOURCE,
    )
    reached = {i.field_path for i in report.issues}
    assert "summary" in reached
    assert "key_takeaways.0" in reached
    assert "glossary.0.definition" in reached
    assert "outline.0.title" in reached
    assert "outline.0.points.0" in reached


def test_assessment_prose_is_checked():
    question = MCQItem(
        id="q1",
        prompt="Which proccess returns water to the air?",
        choices=[Choice(id="q1-c1", text="Evaporation", is_correct=True), Choice(id="q1-c2", text="Freezing")],
    )
    report = check_assessment(make_set(questions=[question]), SOURCE)
    assert any(i.actual == "proccess" for i in report.issues)


# --- what it must NOT catch -----------------------------------------------------


def test_vocabulary_from_the_source_is_never_flagged():
    """If the author wrote it, it is correct for this document by definition."""
    report = check_insights(
        insights(summary="Evapotranspiration over the Deccan plateau fills the stroma."),
        SOURCE,
    )
    assert report.issues == []


def test_a_compound_of_real_words_is_not_flagged():
    checker = ProseChecker("en", "")
    text = "a well-known self-contained multi-tenant cross-platform decision"
    assert checker.check(text, "f") == []


def test_the_possessive_of_a_source_term_is_not_flagged():
    """This is where splitting on the apostrophe earns its place.

    The dictionary knows common possessives outright — "learner's" is in it — so a
    test using one of those passes whether or not we split, and proves nothing. It
    does *not* know "stroma's", and the allow-list holds "stroma", not its
    possessive. Only splitting connects the two.
    """
    checker = ProseChecker("en", SOURCE)
    assert checker.check("the stroma's role in the reaction", "f") == []


def test_a_misspelling_wearing_a_possessive_still_suggests_the_base_word():
    checker = ProseChecker("en", "")
    issue = checker.check("the sentance's meaning", "f")[0]
    assert issue.actual == "sentance"
    assert issue.suggestion == "sentence"


def test_a_glossary_term_is_allow_listed_not_checked():
    """The term came out of the document; only the definition is ours.

    Marking it would be telling a teacher their own textbook is misspelt. The
    fixture uses a word the dictionary genuinely lacks — an earlier version used
    "Rhizobium", which pyspellchecker already knows, so the test passed whether or
    not the term was allow-listed.
    """
    report = check_insights(
        insights(
            glossary=[
                GlossaryTerm(
                    term="plasmodesmata",
                    definition="Plasmodesmata are channels between plant cells.",
                )
            ],
        ),
        source_text="",
    )
    assert report.issues == []


def test_urls_maths_and_code_are_not_treated_as_prose():
    checker = ProseChecker("en", "")
    text = r"See https://exmpl.io/xyz and \(E = mc^2\) and `qq_zz` and $$\alpha$$."
    assert checker.check(text, "f") == []


def test_our_own_blank_markers_are_not_words():
    checker = ProseChecker("en", "")
    assert checker.check("Water turns to [[1]] when heated.", "f") == []


def test_a_word_wrong_twice_in_one_field_is_one_issue():
    checker = ProseChecker("en", "")
    issues = checker.check("sentance and another sentance", "f")
    assert len(issues) == 1


# --- language honesty -----------------------------------------------------------


def test_an_unsupported_language_is_skipped_not_checked():
    """The library ships no Indic dictionary.

    Running the English one over Hindi would flag every word in the document. The
    check records itself as skipped, and a skipped check is not a passed check.
    """
    report = check_assessment(make_set(questions=[], language="hi"), SOURCE)
    assert report.status == "not_run"
    assert report.checks_run == []
    assert any("no dictionary is available" in s and "'hi'" in s for s in report.skipped)


def test_a_regional_tag_still_finds_the_base_dictionary():
    assert ProseChecker("en-GB", "").available
    assert ProseChecker("en_US", "").available


def test_an_unsupported_language_never_raises():
    """`SpellChecker` raises on an unknown language, so membership is tested first;
    otherwise a tenant teaching in Hindi turns a quality check into a 500."""
    assert ProseChecker("hi", SOURCE).available is False
    assert ProseChecker("zz", SOURCE).check("anything at all", "f") == []


# --- it reports, it does not rewrite --------------------------------------------


def test_the_artefact_is_never_modified():
    """Rewriting generated prose would change meaning on a guess, and would make
    the same input stop producing the same package."""
    original = "This sentance stays exactly as generated."
    doc = insights(summary=original)
    report = check_insights(doc, SOURCE)
    assert report.issues, "the fixture must actually contain a mistake"
    assert doc.summary == original


def test_a_clean_artefact_passes_with_no_issues():
    report = check_insights(insights(summary="The water returns to the atmosphere."), SOURCE)
    assert report.status == "passed"
    assert report.issues == []
    assert "spelling" in report.checks_run


def test_the_report_flattens_into_the_warnings_a_caller_already_reads():
    report = check_insights(insights(summary="A sentance."), SOURCE)
    flattened = report.as_warnings()
    assert flattened and all(w.startswith("summary: ") for w in flattened)


# --- tokenising -----------------------------------------------------------------


def test_short_tokens_and_digits_are_ignored():
    assert words_in("A 42 x1 of the") == ["the"]


def test_a_lone_prefix_is_still_a_word_worth_checking():
    """"post" on its own is a real word; only a *split* compound skips its parts."""
    assert parts_of("post") == ["post"]
    assert parts_of("post-industrial") == ["industrial"]
