"""The acceptance matrix has to keep pointing at things that exist.

`docs/acceptance.md` maps every criterion in issue #7 to the code that meets it and
the tests that hold it. It is written for a reviewer to spot-check, which makes a
stale reference in it worse than no reference at all: a test name that no longer
exists reads as evidence right up until someone looks.

Every check here asserts a floor on how much it found before asserting that nothing is
missing. Without that a regex that quietly stops matching passes as loudly as one that
matches everything — which is exactly what a first version of this did, matching no
filenames at all because the pattern could not cross the dot in `test_x.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
DOC = ENGINE / "docs" / "acceptance.md"


def body() -> str:
    return DOC.read_text()


def cited(pattern: str) -> set[str]:
    return set(re.findall(pattern, body()))


def test_every_test_file_the_matrix_names_exists():
    names = cited(r"`(test_[a-z0-9_]+\.py)`")
    assert len(names) >= 15, f"only found {len(names)} test files cited; the pattern is wrong"
    present = {p.name for p in (ENGINE / "tests").glob("test_*.py")}
    assert not names - present, f"named in acceptance.md but gone: {sorted(names - present)}"


def test_every_individual_test_the_matrix_names_exists():
    """Named tests are the strongest claims in the document — the ones where a single
    guarantee is pointed at. A renamed test would leave the claim unbacked."""
    names = cited(r"`(test_[a-z0-9_]+)`(?!\.)")
    named = {n for n in names if not n.endswith(".py")}
    assert len(named) >= 15, f"only found {len(named)} individual tests cited; the pattern is wrong"

    defined: set[str] = set()
    for path in (ENGINE / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^(?:async )?def (test_[a-z0-9_]+)", path.read_text(), re.MULTILINE))
    assert not named - defined, f"named in acceptance.md but not defined: {sorted(named - defined)}"


def test_every_source_path_the_matrix_names_exists():
    paths = cited(r"`(app/[A-Za-z0-9_/]+(?:\.py)?)`")
    assert len(paths) >= 10, f"only found {len(paths)} source paths cited; the pattern is wrong"
    missing = sorted(p for p in paths if not (ENGINE / p).exists())
    assert not missing, f"named in acceptance.md but gone: {missing}"


def test_every_relative_link_resolves():
    links = {
        target for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", body())
        if not target.startswith("http")
    }
    assert len(links) >= 10, f"only found {len(links)} relative links; the pattern is wrong"
    missing = sorted(t for t in links if not (DOC.parent / t).resolve().exists())
    assert not missing, f"acceptance.md links to files that do not exist: {missing}"


def test_every_endpoint_the_matrix_names_is_really_served():
    """The matrix tells a reviewer which route to call for each criterion. A route
    that was renamed would send them somewhere that 404s."""
    from app.main import app

    served = set(app.openapi()["paths"])
    endpoints = {e for e in cited(r"`(/[a-z0-9/_-]*)`") if e != "/"}
    assert len(endpoints) >= 12, f"only found {len(endpoints)} endpoints cited; the pattern is wrong"

    # `/micro-lesson/h5p`, `/html5`, `/scorm` is how the document lists a family, so a
    # bare suffix is resolved against the families the service actually serves.
    def real(endpoint: str) -> bool:
        return endpoint in served or any(p.endswith(endpoint) for p in served)

    missing = sorted(e for e in endpoints if not real(e))
    assert not missing, f"acceptance.md names endpoints the app does not serve: {missing}"


def test_the_matrix_covers_all_four_modules_and_the_outcomes():
    """A criterion silently dropped from the document is the failure this catches."""
    text = body()
    for heading in (
        "Module A — Document Ingestion",
        "Module B — Assessment Suite",
        "Module C — Multimedia Intelligence",
        "Module D — Micro-Learning Studio",
        "Expected outcomes",
    ):
        assert heading in text, f"acceptance.md no longer covers {heading!r}"
