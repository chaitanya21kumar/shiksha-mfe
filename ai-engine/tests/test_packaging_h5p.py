"""Tests for the H5P packaging primitives — manifest, versions, subcontent, ZIP.

These assert the rules read out of H5P's own validator
(``h5p-php-library/h5p.classes.php``) and out of the package the H5P Hub serves,
because almost every one of them fails *silently*: a wrong key or a stray
directory entry produces a file that looks fine and then misbehaves inside the
LMS. The regexes below are copied from ``$h5pRequired``/``$h5pOptional`` so the
tests check what H5P checks, not a paraphrase of it.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

from app.packaging.h5p import (
    ALLOWED_QUESTION_LIBRARIES,
    BLANKS,
    CLOSURE,
    DRAGTEXT,
    MULTICHOICE,
    QUESTIONSET,
    build_manifest,
    library_string,
    sanitise_language,
    sanitise_title,
    subcontent_id,
    wrap,
    write_h5p,
)

# Verbatim from H5P's $h5pRequired.
H5P_LANGUAGE_RE = re.compile(r"^[-a-zA-Z]{1,10}$")
H5P_MAIN_LIBRARY_RE = re.compile(r"^[$a-z_][0-9a-z_.$]{1,254}$", re.IGNORECASE)
# H5P's own pattern is /^[0-9]{1,5}$/. `re.ASCII` is what keeps `\d` to that same
# set: without it Python's `\d` also matches Devanagari and Arabic-Indic digits,
# which PHP would reject -- not a hypothetical for a multi-tenant Indian LMS.
H5P_VERSION_RE = re.compile(r"^\d{1,5}$", re.ASCII)
H5P_TITLE_RE = re.compile(r"^.{1,255}$", re.DOTALL)
# Verbatim from $h5pOptional['license'].
H5P_LICENSE_RE = re.compile(
    r"^(CC BY|CC BY-SA|CC BY-ND|CC BY-NC|CC BY-NC-SA|CC BY-NC-ND|CC0 1\.0"
    r"|GNU GPL|PD|ODC PDDL|CC PDM|U|C)$"
)
# From H5P core's subContentId check: looser than a strict UUIDv4 (it never pins
# the version nibble) and, with no /i flag, lowercase-only.
SUBCONTENT_ID_RE = re.compile(
    r"^\{?[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\}?$"
)


def _manifest() -> dict:
    return build_manifest(title="Photosynthesis Quiz", language="en")


# --- manifest ----------------------------------------------------------------


def test_manifest_has_exactly_the_five_required_keys_and_none_are_null():
    manifest = _manifest()
    for key in ("title", "language", "mainLibrary", "embedTypes", "preloadedDependencies"):
        assert key in manifest
        # H5P checks required keys with isset(), which reads an explicit null as
        # missing -- so null is never a way to say "unset".
        assert manifest[key] is not None


def test_manifest_fields_match_h5ps_own_validation_regexes():
    manifest = _manifest()
    assert H5P_TITLE_RE.match(manifest["title"])
    assert H5P_LANGUAGE_RE.match(manifest["language"])
    assert H5P_MAIN_LIBRARY_RE.match(manifest["mainLibrary"])
    assert H5P_LICENSE_RE.match(manifest["license"])
    assert manifest["embedTypes"] == ["div"]


def test_main_library_is_a_machine_name_without_a_version():
    # "H5P.QuestionSet 1.20" fails H5P's mainLibrary regex because of the space.
    assert _manifest()["mainLibrary"] == "H5P.QuestionSet"


def test_main_library_version_is_declared_in_the_dependencies():
    # The manifest names the main library without a version, so this list is the
    # only place its version is stated.
    manifest = _manifest()
    entry = [d for d in manifest["preloadedDependencies"] if d["machineName"] == "H5P.QuestionSet"]
    assert entry == [{"machineName": "H5P.QuestionSet", "majorVersion": 1, "minorVersion": 20}]


def test_building_a_manifest_whose_closure_omits_the_main_library_is_refused():
    # H5P's validator does NOT catch this: an undeclared main library passes
    # validation and then breaks in savePackage, producing broken content. This
    # assertion is the only thing standing in the way, so it must hold.
    with pytest.raises(ValueError, match="missing from the dependency closure"):
        build_manifest(title="t", language="en", dependencies=(MULTICHOICE,))


def test_dependency_versions_are_ints_that_satisfy_h5ps_numeric_regex():
    for dep in _manifest()["preloadedDependencies"]:
        assert isinstance(dep["majorVersion"], int)
        assert isinstance(dep["minorVersion"], int)
        # H5P guards the regex with is_string($v) || is_int($v), so ints are
        # accepted and coerced by preg_match.
        assert H5P_VERSION_RE.match(str(dep["majorVersion"]))
        assert H5P_VERSION_RE.match(str(dep["minorVersion"]))


def test_closure_is_the_twelve_libraries_read_from_the_hub_package():
    # Resolved by walking preloadedDependencies through the library.json of every
    # library inside the Hub's own Question Set download. Notably it does NOT
    # include H5P.Components, which master's dependency graph implies but the
    # shipped libraries do not use.
    assert set(CLOSURE) == {
        ("H5P.QuestionSet", 1, 20),
        ("H5P.MultiChoice", 1, 16),
        ("H5P.Blanks", 1, 14),
        ("H5P.DragText", 1, 10),
        ("H5P.Question", 1, 5),
        ("H5P.JoubelUI", 1, 3),
        ("H5P.Transition", 1, 0),
        ("H5P.FontIcons", 1, 0),
        ("H5P.TextUtilities", 1, 3),
        ("H5P.Video", 1, 6),
        ("FontAwesome", 4, 5),
        ("jQuery.ui", 1, 10),
    }
    assert not any(name == "H5P.Components" for name, _, _ in CLOSURE)


def test_closure_contains_no_editor_dependencies():
    # H5P's exporter skips editor dependencies; a naive "copy every dependency"
    # pass would pull in H5PEditor.RangeList and friends, which Blanks and
    # DragText both declare.
    assert not any(name.startswith("H5PEditor.") for name, _, _ in CLOSURE)


def test_math_display_is_never_declared_as_a_dependency():
    # H5P.MathDisplay is runnable:0 with an addTo regex -- core injects it into
    # any content whose text looks like maths. Declaring it is wrong.
    assert not any(name == "H5P.MathDisplay" for name, _, _ in CLOSURE)
    assert not any(
        dep["machineName"] == "H5P.MathDisplay" for dep in _manifest()["preloadedDependencies"]
    )


# --- language / title --------------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("en", "en"),
        ("hi", "hi"),
        ("en-IN", "en-IN"),
        ("zh-Hant-TW", "zh-Hant-TW"),  # exactly 10 chars -- the limit
        ("es-419", "es"),  # digits are rejected, so fall back to the primary subtag
        ("", "und"),
        ("this-is-far-too-long-to-be-a-tag", "und"),
        ("419", "und"),
    ],
)
def test_language_is_coerced_into_something_h5p_accepts(supplied, expected):
    result = sanitise_language(supplied)
    assert result == expected
    assert H5P_LANGUAGE_RE.match(result)


def test_every_sanitised_language_satisfies_h5ps_regex():
    for tag in ["en", "es-419", "", "pt-BR", "sr-Latn-RS", "zz", "12345", "a" * 40]:
        assert H5P_LANGUAGE_RE.match(sanitise_language(tag))


def test_title_is_never_empty_and_never_over_255_chars():
    assert sanitise_title("") == "Assessment"
    assert sanitise_title("   ") == "Assessment"
    assert len(sanitise_title("x" * 400)) == 255
    assert H5P_TITLE_RE.match(sanitise_title("x" * 400))


# --- subcontent --------------------------------------------------------------


def test_subcontent_id_is_lowercase_and_matches_h5ps_regex():
    generated = subcontent_id("a-1", "q1")
    assert SUBCONTENT_ID_RE.match(generated)
    assert generated == generated.lower()


def test_subcontent_id_is_deterministic_and_unique_per_question():
    assert subcontent_id("a-1", "q1") == subcontent_id("a-1", "q1")
    assert subcontent_id("a-1", "q1") != subcontent_id("a-1", "q2")
    assert subcontent_id("a-1", "q1") != subcontent_id("a-2", "q1")


def test_subcontent_wrapper_has_exactly_the_four_keys_h5p_keeps():
    # filterParams silently deletes anything else, so extra provenance stashed
    # here would vanish without warning rather than fail loudly.
    wrapper = wrap(
        library=MULTICHOICE,
        params={"question": "<p>Q</p>"},
        content_type="Multiple Choice",
        title="q1",
        assessment_id="a-1",
        question_id="q1",
    )
    assert set(wrapper) == {"library", "params", "subContentId", "metadata"}
    assert set(wrapper["metadata"]) == {"contentType", "license", "title"}


def test_library_strings_are_rendered_as_h5p_names_them():
    assert library_string(MULTICHOICE) == "H5P.MultiChoice 1.16"
    assert library_string(BLANKS) == "H5P.Blanks 1.14"
    assert library_string(DRAGTEXT) == "H5P.DragText 1.10"
    assert library_string(QUESTIONSET) == "H5P.QuestionSet 1.20"


def test_the_libraries_we_emit_are_in_the_installed_question_sets_whitelist():
    # questions[].library is compared by exact string equality against the
    # whitelist baked into the installed Question Set's semantics.json. This is
    # the check that decides whether a package imports at all.
    for library in (MULTICHOICE, BLANKS, DRAGTEXT):
        assert library_string(library) in ALLOWED_QUESTION_LIBRARIES


# --- the ZIP -----------------------------------------------------------------


def _zip_of(**overrides) -> zipfile.ZipFile:
    payload = write_h5p(manifest=_manifest(), content=overrides.get("content", {"questions": []}))
    return zipfile.ZipFile(io.BytesIO(payload))


def test_package_holds_exactly_the_manifest_and_the_content_at_the_expected_paths():
    assert _zip_of().namelist() == ["h5p.json", "content/content.json"]


def test_package_has_no_directory_entries():
    # A bare "content/" entry is routed into H5P's content branch, tested against
    # an extension-anchored whitelist, has no extension, and rejects the WHOLE
    # package. writestr never creates one; walking a tree would.
    assert [i.filename for i in _zip_of().infolist() if i.is_dir()] == []


def test_package_is_a_readable_deflated_zip():
    archive = _zip_of()
    assert archive.testzip() is None
    assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_DEFLATED}


def test_package_is_byte_identical_across_runs():
    # Fixed timestamps plus deterministic subcontent ids. Without this, "did the
    # emitter change?" could only be answered by re-implementing it.
    first = write_h5p(manifest=_manifest(), content={"questions": []})
    second = write_h5p(manifest=_manifest(), content={"questions": []})
    assert first == second


def test_non_ascii_content_survives_the_round_trip():
    # The LMS is multi-tenant Indian ed-tech: Devanagari has to come back intact,
    # not as a pile of \uXXXX escapes.
    hindi = "जल चक्र क्या है?"
    payload = write_h5p(manifest=_manifest(), content={"question": hindi})
    archive = zipfile.ZipFile(io.BytesIO(payload))
    assert json.loads(archive.read("content/content.json"))["question"] == hindi
