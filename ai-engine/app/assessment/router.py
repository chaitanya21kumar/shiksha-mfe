"""HTTP surface for the assessment layer (Module B).

- ``POST /assess`` takes a `ParsedDocument` (the output of ``/ingest``) and
  returns an `AssessmentSet`.
- ``POST /assess/file`` takes a raw upload and does parse + generate in one call.
- ``POST /assess/h5p`` takes an `AssessmentSet` and returns an ``.h5p`` file.
- ``POST /assess/h5p/file`` takes an upload and does parse + generate + package.

The generating endpoints accept ``question_types`` (any of mcq, match,
fill_blank, short_answer; defaults to all four), ``count`` (questions per type), ``language``
(BCP-47 tag), and ``pass_percentage`` (the mastery threshold, which becomes H5P's
passPercentage and drives the default rubric). Like the other generative routers
the endpoints stay thin: the pipeline raises the shared domain errors (empty
document, gateway unavailable, timeout) and the app-level exception handlers map
those to the documented HTTP responses. The model client and generation config
are shared with the summarisation layer.

The packaging endpoints return a binary file, so a partial failure has nowhere to
go in the body: any question the emitter had to drop is reported in the
``X-Package-Warnings`` response header instead.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from ..packaging.response import ZIP_MEDIA_TYPE, package_response
from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from ..summarization.pipeline import generation_config
from ..summarization.router import get_llm_client
from .emit import emit_h5p, emit_scorm
from .pipeline import ALL_TYPES, QuestionType, generate_assessment
from .schema import AssessmentSet

router = APIRouter(tags=["assessment"])

_ERROR_RESPONSES = {
    400: {"description": "The document has no text to build questions from"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}

_TYPES_QUERY = Query(
    description=(
        "Question types to generate (any of mcq, match, fill_blank, short_answer). "
        "Defaults to all four."
    )
)
_COUNT_QUERY = Query(ge=1, le=20, description="Questions to generate per requested type.")
_LANGUAGE_QUERY = Query(description="BCP-47 language tag of the source (e.g. en, hi).")
_PASS_QUERY = Query(
    ge=0,
    le=100,
    description="Mastery threshold as a percentage (H5P passPercentage, SCORM masteryscore).",
)


def _resolve_types(question_types: list[QuestionType] | None) -> list[QuestionType]:
    """Default to every type, and drop duplicates while keeping request order."""
    return list(dict.fromkeys(question_types or ALL_TYPES))


@router.post("/assess", responses=_ERROR_RESPONSES)
async def assess(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
    pass_percentage: Annotated[int, _PASS_QUERY] = 50,
) -> AssessmentSet:
    """Generate a source-grounded assessment from an already-parsed document."""
    return await generate_assessment(
        client,
        document,
        generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
        pass_percentage=pass_percentage,
    )


@router.post(
    "/assess/file",
    responses={**_ERROR_RESPONSES, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def assess_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
    pass_percentage: Annotated[int, _PASS_QUERY] = 50,
) -> AssessmentSet:
    """Parse an uploaded document and generate an assessment in one call."""
    document = await parse_upload(file)
    return await generate_assessment(
        client,
        document,
        generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
        pass_percentage=pass_percentage,
    )


@router.post(
    "/assess/h5p",
    response_class=Response,
    responses={
        200: {
            "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "An H5P Question Set, importable into an LMS with the H5P content types installed",
        },
        400: {"description": "No question in the set could be packaged"},
    },
)
async def assess_h5p(assessment: AssessmentSet) -> Response:
    """Package an assessment as an H5P Question Set (`.h5p`).

    Takes the output of `/assess` unchanged, so a caller can review or edit the
    questions — or supply their own `score_bands` rubric — before packaging.
    """
    return package_response(emit_h5p(assessment))


@router.post(
    "/assess/h5p/file",
    response_class=Response,
    responses={
        200: {
            "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "An H5P Question Set built from the uploaded document",
        },
        **_ERROR_RESPONSES,
        # This route both generates and packages, so its 400 has two causes; the
        # inherited description only covers the generating one.
        400: {
            "description": "The document has no text to build questions from, "
            "or no question could be packaged"
        },
        413: {"description": "File too large"},
        415: {"description": "Unsupported file type"},
    },
)
async def assess_h5p_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
    pass_percentage: Annotated[int, _PASS_QUERY] = 50,
) -> Response:
    """Parse a document, generate an assessment, and package it — in one call."""
    document = await parse_upload(file)
    assessment = await generate_assessment(
        client,
        document,
        generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
        pass_percentage=pass_percentage,
    )
    return package_response(emit_h5p(assessment))


@router.post(
    "/assess/scorm",
    response_class=Response,
    responses={
        200: {
            "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "A SCORM 1.2 package, importable into Moodle 4.x and Open edX",
        },
        400: {"description": "The set has no questions to package"},
    },
)
async def assess_scorm(assessment: AssessmentSet) -> Response:
    """Package an assessment as a SCORM 1.2 course (`.zip`).

    Unlike the H5P package, this one carries its own player: the LMS supplies only
    a JavaScript API to report through. That is why per-question `points` is
    honoured exactly here, where H5P has to score on its own scale.
    """
    return package_response(emit_scorm(assessment))


@router.post(
    "/assess/scorm/file",
    response_class=Response,
    responses={
        200: {
            "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            "description": "A SCORM 1.2 package built from the uploaded document",
        },
        **_ERROR_RESPONSES,
        400: {
            "description": "The document has no text to build questions from, "
            "or the set has no questions to package"
        },
        413: {"description": "File too large"},
        415: {"description": "Unsupported file type"},
    },
)
async def assess_scorm_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
    pass_percentage: Annotated[int, _PASS_QUERY] = 50,
) -> Response:
    """Parse a document, generate an assessment, and package it as SCORM 1.2."""
    document = await parse_upload(file)
    assessment = await generate_assessment(
        client,
        document,
        generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
        pass_percentage=pass_percentage,
    )
    return package_response(emit_scorm(assessment))
