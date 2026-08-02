"""A mistyped field is refused, not dropped.

Pydantic ignores an unknown key by default, which turns a caller's typo into a
quietly wrong artefact. That is not hypothetical: `ChapterCheck(chapter_index=1,
question=...)` — singular — was accepted while building this very module, leaving
`questions` empty and producing an interactive video with no knowledge checks at
all, reported as nothing more than a warning. It then caught a second one, in a
test fixture written an hour earlier: `TranscriptSource(..., duration_seconds=300)`
where the field is `media_seconds`.

One model is deliberately exempt, and the last test here is what stops someone
"fixing" that.
"""

from __future__ import annotations

import importlib
import inspect
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.assessment.schema import AssessmentSet, Choice
from app.chaptering.schema import Chapter
from app.interactive_video.schema import ChapterCheck, VideoSource
from app.main import create_app
from app.transcription.schema import TranscriptSource
from tests.factories import make_mcq, make_set

MODULES = [
    "ingestion",
    "transcription",
    "chaptering",
    "assessment",
    "interactive_video",
    "summarization",
    "narration",
]

#: The single exemption, and the reason. Its own serialised output has to be
#: acceptable as input again, and computed fields make that impossible under
#: `extra="forbid"`.
EXEMPT = {"AssessmentSet"}


def contract_models():
    for name in MODULES:
        module = importlib.import_module(f"app.{name}.schema")
        for attr, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__module__ == module.__name__
            ):
                yield f"{name}.{attr}", obj


def test_every_contract_refuses_unknown_fields_except_the_documented_one():
    lax = [
        name
        for name, model in contract_models()
        if model.model_config.get("extra") != "forbid" and name.split(".")[-1] not in EXEMPT
    ]
    assert lax == [], f"these contracts would silently drop a mistyped field: {lax}"


@pytest.mark.parametrize(
    ("build", "kwargs"),
    [
        (ChapterCheck, {"chapter_index": 1, "question": None}),
        (TranscriptSource, {"filename": "a.mp4", "duration_seconds": 10.0}),
        (VideoSource, {"url": "https://x.test/a.mp4", "mimetype": "video/mp4"}),
        (Chapter, {"index": 1, "start": 0.0, "end": 1.0, "title": "T", "body": "x"}),
        (Choice, {"id": "c1", "text": "A", "correct": True}),
    ],
)
def test_a_realistic_typo_is_refused(build, kwargs):
    """Each of these is a plausible near-miss for a real field name."""
    with pytest.raises(ValidationError):
        build(**kwargs)


def test_the_correct_spelling_of_each_still_works():
    assert ChapterCheck(chapter_index=1, questions=[make_mcq()]).questions
    assert TranscriptSource(filename="a.mp4", media_seconds=10.0).media_seconds == 10.0
    assert VideoSource(url="https://x.test/a.mp4", mime="video/webm").mime == "video/webm"
    assert Chapter(index=1, start=0.0, end=1.0, title="T", text="x").text == "x"
    assert Choice(id="c1", text="A", is_correct=True).is_correct


# --- the exemption, and why it has to stay --------------------------------------


def test_an_assessment_set_survives_its_own_round_trip():
    """The review seam: take what `/assess` returned, edit it, package it.

    `max_points` and `counts` are computed fields — pydantic writes them out and
    will not read them back. Forbidding extras on this model would 422 the exact
    workflow the packaging endpoints exist for.
    """
    original = make_set(questions=[make_mcq()])
    dumped = json.loads(original.model_dump_json())
    assert "max_points" in dumped
    assert "counts" in dumped
    assert AssessmentSet.model_validate(dumped).questions[0].id == "q1"


def test_the_packaging_endpoints_accept_what_the_generator_returned():
    client = TestClient(create_app())
    body = json.loads(make_set(questions=[make_mcq()]).model_dump_json())
    for route in ("/assess/h5p", "/assess/scorm"):
        response = client.post(route, json=body)
        assert response.status_code == 200, f"{route} rejected its own contract: {response.text}"


def test_a_typo_inside_a_question_is_still_caught():
    """The exemption is only skin deep — everything nested stays strict, which is
    where a silently dropped field actually corrupts the artefact."""
    body = json.loads(make_set(questions=[make_mcq()]).model_dump_json())
    body["questions"][0]["choices"][0]["correct"] = True  # real field is `is_correct`
    response = TestClient(create_app()).post("/assess/h5p", json=body)
    assert response.status_code == 422


def test_the_exemption_list_has_not_quietly_grown():
    """A second exemption should be a decision someone makes on purpose."""
    assert EXEMPT == {"AssessmentSet"}
    assert AssessmentSet.model_config.get("extra") != "forbid"
