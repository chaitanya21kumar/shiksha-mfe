"""HTTP surface for the whole pipeline: one upload, one course (Week 11).

Every other router in this engine answers one question. This one answers the
question a teacher actually has, which is all of them at once — and the reason it
exists is that composing the others by hand is a real cost. Building a course from
a document today means five calls in a specific order, each feeding the next, with
the caller reimplementing the ordering and the error handling every time. Module B
already taught us what happens when a capability is only reachable one way: half the
callers never get it.

Two shapes, as everywhere else. `/course/…` returns the course as data, so a teacher
can read what was made and change it. `/course/bundle/…` returns one archive ready to
hand to an LMS. The second accepts a course object as well as a file, which is what
makes "generate, fix a heading, then publish" a supported path rather than an
accident.

**A 200 here does not mean everything worked.** It means the build ran and reported.
The stage list is the answer, and it is never empty.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile

from ..ingestion.service import parse_upload
from ..packaging.response import ZIP_MEDIA_TYPE, package_response
from ..summarization.pipeline import generation_config
from ..summarization.router import get_llm_client
from .bundle import build_bundle
from .pipeline import DEFAULT_QUESTION_TYPES, build_course
from .schema import Course

router = APIRouter(tags=["course"])

_ERRORS = {
    400: {"description": "The source has nothing a course can be built from"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}
_FILE_ERRORS = {**_ERRORS, 413: {"description": "File too large"},
                415: {"description": "Unsupported file type"}}

_TITLE = Query(description="The course's title. Defaults to the source's own; never generated.")
_LANGUAGE = Query(description="BCP-47 tag for everything the course produces.")
_INSIGHTS = Query(description="Summarise the document: summary, glossary and outline.")
_NARRATION = Query(description="Write a narration script. Text only — this produces no audio.")
_LESSON = Query(description="Build the micro-lesson.")
_ASSESSMENT = Query(description="Generate questions, each grounded in the source.")
_TYPES = Query(description="Which question types to generate.")
_COUNT = Query(ge=1, le=20, description="Questions per type.")
_PASS = Query(ge=0, le=100, description="Percentage needed to pass the quiz.")
_VISIBILITY = Query(description="When a learner may see the answers.")
_TIMER = Query(ge=1, description="Time limit for the whole quiz, in seconds. Omit for none.")


async def _build(
    client, *, document=None, text="", title, language, with_insights, with_narration,
    with_lesson, with_assessment, question_types, count, pass_percentage,
    solution_visibility, time_limit_seconds,
) -> Course:
    return await build_course(
        client, generation_config(),
        document=document, text=text, title=title, language=language,
        with_insights=with_insights, with_narration=with_narration,
        with_lesson=with_lesson, with_assessment=with_assessment,
        question_types=question_types or list(DEFAULT_QUESTION_TYPES),
        question_count=count, pass_percentage=pass_percentage,
        solution_visibility=solution_visibility, time_limit_seconds=time_limit_seconds,
    )


@router.post("/course/file", responses=_FILE_ERRORS)
async def course_from_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE] = None,
    language: Annotated[str, _LANGUAGE] = "en",
    insights: Annotated[bool, _INSIGHTS] = True,
    narration: Annotated[bool, _NARRATION] = False,
    lesson: Annotated[bool, _LESSON] = True,
    assessment: Annotated[bool, _ASSESSMENT] = True,
    question_types: Annotated[list[str] | None, _TYPES] = None,
    count: Annotated[int, _COUNT] = 5,
    pass_percentage: Annotated[int, _PASS] = 60,
    solution_visibility: Annotated[str, _VISIBILITY] = "always",
    time_limit_seconds: Annotated[int | None, _TIMER] = None,
) -> Course:
    """Turn one upload into a whole course, and report on every stage."""
    document = await parse_upload(file)
    return await _build(
        client, document=document, title=title, language=language,
        with_insights=insights, with_narration=narration, with_lesson=lesson,
        with_assessment=assessment, question_types=question_types, count=count,
        pass_percentage=pass_percentage, solution_visibility=solution_visibility,
        time_limit_seconds=time_limit_seconds,
    )


@router.post("/course/text", responses=_ERRORS)
async def course_from_text(
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    text: Annotated[str, Body(media_type="text/plain")],
    title: Annotated[str | None, _TITLE] = None,
    language: Annotated[str, _LANGUAGE] = "en",
) -> Course:
    """Build a course from pasted notes.

    Only the lesson runs. Summarising, narrating and grounding questions all read a
    parsed document's structure, which pasted text does not have — so rather than
    quietly returning less, those three report themselves as skipped and say why.
    """
    # Asked for, deliberately, even though none of the three can run here. The
    # pipeline then reports the structural reason — "only a parsed document can be
    # summarised" — instead of "not requested", which would tell a teacher they had
    # a choice they do not have.
    return await _build(
        client, text=text, title=title, language=language,
        with_insights=True, with_narration=True, with_lesson=True,
        with_assessment=True, question_types=None, count=5, pass_percentage=60,
        solution_visibility="always", time_limit_seconds=None,
    )


@router.post(
    "/course/bundle/file",
    responses={**_FILE_ERRORS, 200: {"content": {ZIP_MEDIA_TYPE: {}},
               "description": "The whole course as one archive"}},
    response_class=Response,
)
async def course_bundle_from_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE] = None,
    language: Annotated[str, _LANGUAGE] = "en",
    insights: Annotated[bool, _INSIGHTS] = True,
    narration: Annotated[bool, _NARRATION] = False,
    lesson: Annotated[bool, _LESSON] = True,
    assessment: Annotated[bool, _ASSESSMENT] = True,
    question_types: Annotated[list[str] | None, _TYPES] = None,
    count: Annotated[int, _COUNT] = 5,
    pass_percentage: Annotated[int, _PASS] = 60,
    solution_visibility: Annotated[str, _VISIBILITY] = "always",
    time_limit_seconds: Annotated[int | None, _TIMER] = None,
) -> Response:
    """Upload a file, get the finished course back as one archive."""
    document = await parse_upload(file)
    course = await _build(
        client, document=document, title=title, language=language,
        with_insights=insights, with_narration=narration, with_lesson=lesson,
        with_assessment=assessment, question_types=question_types, count=count,
        pass_percentage=pass_percentage, solution_visibility=solution_visibility,
        time_limit_seconds=time_limit_seconds,
    )
    return package_response(build_bundle(course))


@router.post(
    "/course/bundle",
    responses={**_ERRORS, 200: {"content": {ZIP_MEDIA_TYPE: {}},
               "description": "The whole course as one archive"}},
    response_class=Response,
)
async def course_bundle(course: Course) -> Response:
    """Package a course that was already built — after a teacher has edited it.

    The reason this route exists rather than only the one-call form: a lesson a
    teacher has corrected is the one they want packaged, and a pipeline that can only
    package what it just generated cannot express that.
    """
    return package_response(build_bundle(course))
