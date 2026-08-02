"""Teacher controls over an assessment: who sees the answers, and how long they get.

Both were asked for directly by the mentors. Neither is a feature of the model, and
that is the point of testing them here: they are properties of the *artefact* we
hand an LMS, so the only honest test is to open the artefact and look.

The H5P assertions name real fields from H5P.QuestionSet 1.20's semantics.json.
That matters more than it looks. H5PContentValidator drops keys it does not
recognise without raising, so an invented field would produce a package that
imports cleanly, shows no error anywhere, and simply ignores the teacher's
setting. A test that only checked "we wrote something" would pass on that.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.assessment.emit.h5p import emit_h5p
from app.assessment.emit.scorm import emit_scorm
from app.assessment.schema import AssessmentSet
from tests.factories import make_mcq, make_set, make_short

ASSETS = Path(__file__).resolve().parents[1] / "app" / "packaging" / "scorm" / "assets"


def h5p_content(assessment: AssessmentSet) -> dict:
    package = emit_h5p(assessment)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("content/content.json"))


def scorm_payload(assessment: AssessmentSet) -> dict:
    package = emit_scorm(assessment)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    index = archive.read("index.html").decode()
    island = re.search(
        r'<script type="application/json" id="assessment-data">(.*?)</script>', index, re.S
    )
    assert island, "the data island is missing from index.html"
    return json.loads(island.group(1))


# --- H5P: the three real fields -------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "check", "per_question", "results_page"),
    [
        ("always", True, "on", True),
        ("after_submission", False, "off", True),
        ("never", False, "off", False),
    ],
)
def test_visibility_maps_onto_the_question_set_fields_that_exist(
    visibility, check, per_question, results_page
):
    content = h5p_content(make_set(questions=[make_mcq()], solution_visibility=visibility))
    assert content["override"]["checkButton"] is check
    assert content["override"]["showSolutionButton"] == per_question
    assert content["endGame"]["showSolutionButton"] is results_page


def test_the_per_question_solution_override_is_written_at_all():
    """Regression: `override` used to carry only `checkButton`.

    `override.showSolutionButton` is a *select* whose default is null, and null
    means "leave each question as it is". Omitting it is therefore not a neutral
    act — it is what made "never" impossible to express, because every question
    kept its own Show-solution button no matter what the results page did.
    """
    content = h5p_content(make_set(questions=[make_mcq()], solution_visibility="never"))
    assert "showSolutionButton" in content["override"]


def test_the_select_only_ever_receives_a_value_the_library_declares():
    semantics_values = {"on", "off"}
    for visibility in ("always", "after_submission", "never"):
        content = h5p_content(make_set(questions=[make_mcq()], solution_visibility=visibility))
        assert content["override"]["showSolutionButton"] in semantics_values


# --- H5P: the timer it cannot express -------------------------------------------


def test_a_time_limit_is_reported_as_unsupported_rather_than_dropped():
    package = emit_h5p(make_set(questions=[make_mcq()], time_limit_seconds=600))
    assert any("no timer field" in w for w in package.warnings)
    assert any("600s" in w for w in package.warnings)


def test_no_time_limit_means_no_warning_about_one():
    package = emit_h5p(make_set(questions=[make_mcq()]))
    assert not any("timer" in w for w in package.warnings)


def test_no_invented_timer_key_reaches_the_h5p_content():
    """The failure this prevents is silent, so the assertion has to be broad.

    H5P drops unknown keys without complaint. If a future change writes
    `timeLimit` or `duration` into the Question Set because it seems reasonable,
    nothing at import time will say otherwise — the package will simply never
    count down. So assert on the serialised content, not on our own constants.
    """
    content = h5p_content(make_set(questions=[make_mcq()], time_limit_seconds=600))
    serialised = json.dumps(content).lower()
    for invented in ("timelimit", "time_limit", "countdown", "deadline", "timer"):
        assert invented not in serialised, f"{invented!r} is not a Question Set field"


# --- the contract ---------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 29, 86_401])
def test_an_implausible_time_limit_is_refused(bad):
    question = make_mcq()
    with pytest.raises(ValidationError):
        make_set(questions=[question], time_limit_seconds=bad)


@pytest.mark.parametrize("good", [30, 600, 86_400])
def test_a_plausible_time_limit_is_accepted(good):
    assert make_set(questions=[make_mcq()], time_limit_seconds=good).time_limit_seconds == good


def test_an_unknown_visibility_is_refused():
    question = make_mcq()
    with pytest.raises(ValidationError):
        make_set(questions=[question], solution_visibility="teacher_release")


def test_the_defaults_preserve_the_previous_behaviour():
    """Every existing caller must keep the package it had before this change."""
    assessment = make_set(questions=[make_mcq()])
    assert assessment.solution_visibility == "always"
    assert assessment.time_limit_seconds is None
    content = h5p_content(assessment)
    assert content["override"]["checkButton"] is True
    assert content["endGame"]["showSolutionButton"] is True


# --- SCORM: the package carries them --------------------------------------------


def test_the_scorm_payload_carries_both_controls():
    payload = scorm_payload(
        make_set(
            questions=[make_short()],
            solution_visibility="never",
            time_limit_seconds=300,
        )
    )
    assert payload["solution_visibility"] == "never"
    assert payload["time_limit_seconds"] == 300


def test_the_scorm_payload_keys_the_clock_on_the_assessment():
    """Two packages open in one tab must not share a deadline."""
    payload = scorm_payload(make_set(questions=[make_mcq()], time_limit_seconds=300))
    assert payload["assessment_id"]


def test_an_untimed_assessment_says_so_explicitly():
    payload = scorm_payload(make_set(questions=[make_mcq()]))
    assert payload["time_limit_seconds"] is None


# --- SCORM player: the shipped JavaScript ---------------------------------------
#
# These read the asset that actually goes into the ZIP. They are deliberately
# behavioural greps rather than a JS test runner: the player has no build step and
# no module system, and the properties below are exactly the ones whose absence
# would be invisible until a learner hit them.


def player_source() -> str:
    return (ASSETS / "player.js").read_text()


def test_the_submit_click_handler_cannot_force_a_submission():
    """Regression, and it nearly shipped.

    `submit` takes `forced` as its first argument, and a DOM click handler is
    called with a MouseEvent. Registering `submit` directly would pass that event
    as `forced`, and a MouseEvent is truthy — so every ordinary click would have
    skipped the minimum-length guard.
    """
    source = player_source()
    assert 'addEventListener("click", submit)' not in source
    assert re.search(r'addEventListener\("click",\s*function\s*\(\)\s*\{\s*submit\(false\)', source)


def test_forced_is_narrowed_to_a_strict_boolean():
    assert "forced = forced === true;" in player_source()


def test_expiry_reports_the_scorm_exit_value_for_running_out_of_time():
    """`time-out` is one of the four values SCORM 1.2 allows, and is the only one
    that distinguishes a learner who ran out from one who finished."""
    assert 'forced ? "time-out" : ""' in player_source()


def test_the_deadline_is_stored_as_an_instant_not_a_remaining_duration():
    """A learner who reloads must not be handed their time back."""
    source = player_source()
    assert "cmi.suspend_data" in source
    assert "loadDeadline" in source
    assert "saveDeadline" in source


def test_the_solution_gate_runs_before_anything_that_reveals_an_answer():
    """Order matters: the gate is only a gate if it precedes the reveal."""
    source = player_source()
    gate = source.index("if (!solutionsVisible())")
    model_answer = source.index('el("p", "model-answer"')
    explanation = source.index('el("div", "explanation")')
    assert gate < model_answer
    assert gate < explanation


def test_the_clock_is_not_a_live_region():
    """A per-second aria-live update makes a screen reader unusable; the warnings
    are announced through a separate polite region instead."""
    markup = (ASSETS / "index.html").read_text()
    assert 'id="timer"' in markup
    assert 'id="timer-announce"' in markup
    assert 'aria-live="polite"' in markup
    timer_tag = re.search(r'<div id="timer"[^>]*>', markup).group(0)
    assert "aria-live" not in timer_tag


# --- the routes: where a teacher actually reaches these -------------------------
#
# Everything above opens the artefact and checks the setting landed. All of it
# passed while the two controls were unreachable over HTTP: they were fields on the
# contract, so the two-step flow could set them, but none of the four generating
# routes accepted them. A teacher who uploads a file and gets a package straight
# back had no moment in between to say anything, and the route never asked — so the
# answers were always shown and the clock never ran, silently, in the flow most
# people would use. Tested at the emitter, missed at the door.


from tests.test_assess import _sample_pdf_bytes, _typed_handler


@pytest.fixture
def assess_client():
    """A TestClient whose gateway returns one valid, grounded question per type.

    Reuses test_assess's handler rather than a second copy: an assessment mock has
    to satisfy the grounding gate and every per-type shape, and getting either
    subtly wrong yields an empty set and a 400 that looks like the thing under test
    failing when it is really the fixture.
    """
    import asyncio

    import httpx
    from fastapi.testclient import TestClient

    from app.main import app
    from app.summarization.router import get_llm_client

    fake = httpx.AsyncClient(
        transport=httpx.MockTransport(_typed_handler), timeout=httpx.Timeout(5.0)
    )
    app.dependency_overrides[get_llm_client] = lambda: fake
    yield TestClient(app)
    app.dependency_overrides.pop(get_llm_client, None)
    asyncio.run(fake.aclose())


GENERATING_ROUTES = ["/assess/file", "/assess/h5p/file", "/assess/scorm/file"]


@pytest.mark.parametrize("route", GENERATING_ROUTES)
def test_every_generating_route_accepts_both_controls(assess_client, route):
    """The one-call routes too, not only the two-step flow."""
    resp = assess_client.post(
        route,
        params={"solution_visibility": "never", "time_limit_seconds": 600},
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text


def test_the_setting_reaches_the_generated_assessment(assess_client):
    resp = assess_client.post(
        "/assess/file",
        params={"solution_visibility": "after_submission", "time_limit_seconds": 900},
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    body = resp.json()
    assert body["solution_visibility"] == "after_submission"
    assert body["time_limit_seconds"] == 900


def test_the_setting_reaches_a_package_built_in_one_call(assess_client):
    """The assertion that would have caught it: open the package the quick route
    returns and look, rather than trusting that the route passed anything on."""
    resp = assess_client.post(
        "/assess/h5p/file",
        params={"solution_visibility": "never"},
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    content = json.loads(archive.read("content/content.json"))
    assert content["override"]["showSolutionButton"] == "off"
    assert content["endGame"]["showSolutionButton"] is False


def test_the_clock_reaches_a_scorm_package_built_in_one_call(assess_client):
    resp = assess_client.post(
        "/assess/scorm/file",
        params={"time_limit_seconds": 1200},
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    index = archive.read("index.html").decode()
    island = re.search(
        r'<script type="application/json" id="assessment-data">(.*?)</script>', index, re.S
    )
    assert json.loads(island.group(1))["time_limit_seconds"] == 1200


def test_the_routes_still_default_to_the_old_behaviour(assess_client):
    """Asking for nothing must behave exactly as it did before these existed."""
    resp = assess_client.post(
        "/assess/file", files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")}
    )
    body = resp.json()
    assert body["solution_visibility"] == "always"
    assert body["time_limit_seconds"] is None


@pytest.mark.parametrize(("param", "value"), [("time_limit_seconds", 5), ("solution_visibility", "sometimes")])
def test_a_route_refuses_a_setting_the_contract_would_refuse(assess_client, param, value):
    """The bounds live on the contract; the route must not be a way around them."""
    resp = assess_client.post(
        "/assess/file",
        params={param: value},
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 422
