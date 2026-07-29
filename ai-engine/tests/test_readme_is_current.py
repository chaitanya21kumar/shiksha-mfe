"""The README is the first thing a reviewer or an evaluator reads.

It drifted once already: three modules shipped and their endpoints were never
added, while prose written before H5P and SCORM landed still described them as
future work. Prose cannot be checked automatically, but the endpoint list can — and
that is the part that goes wrong silently every time a router is added.
"""

import pathlib
import re

from app.main import app

_README = pathlib.Path(__file__).resolve().parent.parent / "README.md"
_LISTED = re.compile(r"^- `(GET|POST) (/[^`]*)`", re.MULTILINE)


def _documented() -> set[tuple[str, str]]:
    return set(_LISTED.findall(_README.read_text()))


def _served() -> set[tuple[str, str]]:
    paths = app.openapi()["paths"]
    served = {(method.upper(), path) for path, ops in paths.items() for method in ops}
    # /docs is served by FastAPI itself and is not in the OpenAPI schema.
    return served | {("GET", "/docs")}


def test_every_endpoint_the_app_serves_is_in_the_readme():
    assert not (_served() - _documented()), "endpoints exist that the README never mentions"


def test_the_readme_lists_no_endpoint_the_app_does_not_serve():
    assert not (_documented() - _served()), "the README promises endpoints that do not exist"
