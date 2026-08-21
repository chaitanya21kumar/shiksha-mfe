"""HTTP surface for the micro-lesson builder (Module D, week 9).

Four endpoints, one per way of arriving at a lesson. Issue #7 asks for a lesson
"generated from a document, a transcript, or free-form input", so there is an
endpoint for each rather than one overloaded route with a mode flag — a caller
uploading a PDF and a caller pasting notes want different request shapes, and
collapsing them would mean a body where half the fields are always unused.

The routes stay thin, as the other modules' do: the pipeline raises the shared
domain errors and the app-level handlers map them to documented responses.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, File, Query, Response, UploadFile

from ..chaptering.schema import ChapteredTranscript
from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from ..packaging.response import ZIP_MEDIA_TYPE, package_response
from ..summarization.pipeline import generation_config
from ..summarization.router import get_llm_client
from .emit import emit_h5p, emit_html5, emit_scorm
from .pipeline import lesson_from_document, lesson_from_text, lesson_from_transcript
from .schema import MicroLesson

router = APIRouter(tags=["micro-lesson"])

_ERRORS = {
    400: {"description": "The source has nothing long enough to build a lesson from"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}

_TITLE_QUERY = Query(
    description="The lesson's title. Defaults to the source's own title or filename; "
    "it is never generated, so a caller who names their lesson gets that name back."
)
_LANGUAGE_QUERY = Query(description="BCP-47 tag for the lesson and its future package.")


@router.post("/micro-lesson", responses=_ERRORS)
async def micro_lesson_from_document(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from an already-parsed document."""
    return await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )


@router.post(
    "/micro-lesson/file",
    responses={**_ERRORS, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def micro_lesson_from_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Parse an upload and build a lesson from it in one call."""
    document = await parse_upload(file)
    return await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )


@router.post("/micro-lesson/transcript", responses=_ERRORS)
async def micro_lesson_from_transcript(
    chaptered: Annotated[ChapteredTranscript, Body()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from a chaptered recording.

    One step per chapter, so the lesson inherits a structure that came from where
    the speaker actually paused rather than from anyone's guess.
    """
    return await lesson_from_transcript(
        client, chaptered, generation_config(), title=title, language=language
    )


@router.post("/micro-lesson/text", responses=_ERRORS)
async def micro_lesson_from_text(
    text: Annotated[str, Body(media_type="text/plain")],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from pasted text, split at its blank lines."""
    return await lesson_from_text(
        client, text, generation_config(), title=title, language=language
    )


# --------------------------------------------------------------------------- #
# Packaging — the same lesson, three ways out
# --------------------------------------------------------------------------- #
#
# Each format gets two routes, matching Module B's shape exactly. The plain route
# takes a `MicroLesson`, so a caller can generate it, read it, edit a heading and
# *then* package — which is the flow that makes the output reviewable rather than
# a black box. The `/file` route does upload-to-package in one call, because that
# is the flow a teacher actually uses, and Module B learned the hard way that
# offering only the two-step version leaves half the API unable to do the job.
#
# `text/html` rather than a ZIP for the HTML5 target: it is one file, and wrapping
# a single file in an archive would take away the thing that makes it useful.

HTML_MEDIA_TYPE = "text/html; charset=utf-8"

_PACKAGE_ERRORS = {
    400: {"description": "The lesson has no step with any text to put on a slide"},
}
_ZIP_RESPONSE = {
    200: {
        "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        "description": "A package, importable into an LMS",
    }
}
_HTML_RESPONSE = {
    200: {
        "content": {"text/html": {"schema": {"type": "string", "format": "binary"}}},
        "description": "One self-contained HTML file",
    }
}


@router.post("/micro-lesson/h5p", response_class=Response, responses={**_ZIP_RESPONSE, **_PACKAGE_ERRORS})
async def micro_lesson_h5p(lesson: MicroLesson) -> Response:
    """Package a lesson as an H5P Course Presentation (`.h5p`).

    Takes the output of `/micro-lesson` unchanged, so a caller can review or edit
    the steps before packaging.
    """
    return package_response(emit_h5p(lesson))


@router.post(
    "/micro-lesson/h5p/file",
    response_class=Response,
    responses={**_ZIP_RESPONSE, **_ERRORS, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def micro_lesson_h5p_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> Response:
    """Parse an upload, build the lesson, and package it as H5P — in one call."""
    document = await parse_upload(file)
    lesson = await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )
    return package_response(emit_h5p(lesson))


@router.post("/micro-lesson/html5", response_class=Response, responses={**_HTML_RESPONSE, **_PACKAGE_ERRORS})
async def micro_lesson_html5(lesson: MicroLesson) -> Response:
    """Package a lesson as one self-contained HTML file.

    No LMS, no unzipping, and nothing fetched from the network — it opens on a
    machine with no internet at all.
    """
    return package_response(emit_html5(lesson), HTML_MEDIA_TYPE)


@router.post(
    "/micro-lesson/html5/file",
    response_class=Response,
    responses={**_HTML_RESPONSE, **_ERRORS, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def micro_lesson_html5_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> Response:
    """Parse an upload, build the lesson, and return one HTML file — in one call."""
    document = await parse_upload(file)
    lesson = await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )
    return package_response(emit_html5(lesson), HTML_MEDIA_TYPE)


@router.post("/micro-lesson/scorm", response_class=Response, responses={**_ZIP_RESPONSE, **_PACKAGE_ERRORS})
async def micro_lesson_scorm(lesson: MicroLesson) -> Response:
    """Package a lesson as a SCORM 1.2 course (`.zip`).

    The only one of the three that reports back: the LMS learns who opened the
    lesson and how far they got. No score is reported, because a lesson asks
    nothing — see the emitter for why 0 out of 0 is worse than silence.
    """
    return package_response(emit_scorm(lesson))


@router.post(
    "/micro-lesson/scorm/file",
    response_class=Response,
    responses={**_ZIP_RESPONSE, **_ERRORS, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def micro_lesson_scorm_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> Response:
    """Parse an upload, build the lesson, and package it as SCORM — in one call."""
    document = await parse_upload(file)
    lesson = await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )
    return package_response(emit_scorm(lesson))
