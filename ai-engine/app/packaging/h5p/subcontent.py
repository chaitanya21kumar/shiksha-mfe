"""The subcontent wrapper — how one H5P library is embedded inside another.

A Question Set holds its questions this way, and Module C's Interactive Video and
Module D's Course Presentation will embed their children the same way, which is
why this sits in the packaging layer rather than in the assessment module.

Two details are load-bearing and easy to get wrong:

- The wrapper has **exactly four keys**. H5P's ``filterParams`` deletes anything
  it does not recognise, silently — so provenance stashed here (a source page, an
  assessment id) does not survive, it just disappears.
- ``subContentId`` is validated by a regex that is looser than a strict UUIDv4:
  it never pins the version nibble, and it has no ``/i`` flag, so the hex must be
  **lowercase**. That lets us use a *deterministic* uuid5 instead of a random
  uuid4, which is what makes a generated package byte-reproducible and therefore
  testable. An id that fails the regex is stripped rather than rejected, so the
  package still imports — it just quietly loses stable per-question identity.
"""

from __future__ import annotations

import uuid

from .versions import Library, library_string

#: Namespace for deterministic subcontent ids. Any fixed UUID works; this is the
#: standard URL namespace, and the name we hash into it is "<assessment>/<question>".
_NAMESPACE = uuid.NAMESPACE_URL


def subcontent_id(assessment_id: str, question_id: str) -> str:
    """A stable, lowercase subcontent id derived from the ids we already assign."""
    return str(uuid.uuid5(_NAMESPACE, f"{assessment_id}/{question_id}"))


def wrap(
    *,
    library: Library,
    params: dict[str, object],
    content_type: str,
    title: str,
    assessment_id: str,
    question_id: str,
) -> dict[str, object]:
    """Wrap one library's params as a subcontent entry.

    ``content_type`` and ``title`` are what an H5P editor would show for the
    child; they carry no runtime behaviour but real packages always set them.
    """
    return {
        "library": library_string(library),
        "params": params,
        "subContentId": subcontent_id(assessment_id, question_id),
        "metadata": {
            "contentType": content_type,
            "license": "U",
            "title": title,
        },
    }
