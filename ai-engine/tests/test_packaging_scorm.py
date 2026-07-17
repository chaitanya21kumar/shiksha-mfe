"""Tests for the SCORM 1.2 packaging primitives — data model, manifest, ZIP.

SCORM fails quietly. A malformed value is refused with a numeric error code the
SCO has to go and ask for, so a wrong format does not crash anything — it just
means nothing is ever recorded. These tests therefore assert against the
consumers' **own** rules, copied in verbatim rather than paraphrased:

- the regexes below are Moodle's ``mod/scorm/datamodels/scorm_12.js``;
- the manifest tests replicate Open edX's real parser and Moodle's real
  ``scormtype`` lookup, both of which have bugs we must not trip over.

The negative controls matter as much as the positive ones: several of these
values look obviously correct and are rejected outright by a real LMS.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from app.packaging.scorm import (
    EXIT_VALUES,
    LESSON_STATUS_WRITABLE,
    build_manifest,
    format_score,
    format_timespan,
    response_char,
    sanitise_identifier,
    write_scorm,
)
from app.packaging.scorm.datamodel import CMI_DECIMAL, CMI_TIME, CMI_TIMESPAN

# These mirror Moodle's mod/scorm/datamodels/scorm_12.js, which writes them as:
#
#   CMITimespan = '^([0-9]{2,4}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,2})?$'
#   CMITime     = '^([0-2]{1}[0-9]{1}):([0-5]{1}[0-9]{1}):([0-5]{1}[0-9]{1})(\.[0-9]{1,2})?$'
#   CMIDecimal  = '^-?([0-9]{0,3})(\.[0-9]*)?$'
#   CMIStatus   = '^passed$|^completed$|^failed$|^incomplete$|^browsed$'
#
# The tests below assert BEHAVIOUR against those rules rather than re-declaring
# the patterns: a second copy of a regex only ever proves the two copies agree
# with each other, which is worth nothing. What is worth something is the
# negative controls — the values that look obviously fine and are refused.
MOODLE_CMI_TIMESPAN = CMI_TIMESPAN
MOODLE_CMI_TIME = CMI_TIME
MOODLE_CMI_DECIMAL = CMI_DECIMAL
MOODLE_CMI_STATUS = re.compile(r"^passed$|^completed$|^failed$|^incomplete$|^browsed$")


def _manifest() -> str:
    return build_manifest(
        assessment_id="a-demo-1",
        title="The Water Cycle",
        launch_href="index.html",
        files=["index.html", "scorm/api.js", "scorm/player.js", "scorm/player.css"],
    ).decode("utf-8")


# --- our regexes match the same character set the LMS's do --------------------


@pytest.mark.parametrize("pattern", [CMI_TIMESPAN, CMI_TIME, CMI_DECIMAL])
def test_a_digit_means_an_ascii_digit_the_way_it_does_in_moodle(pattern):
    # We write \d where Moodle writes [0-9]. Python's \d is Unicode-aware, so
    # without re.ASCII it would also match Devanagari and Arabic-Indic digits —
    # which PHP and JavaScript reject. On a multi-tenant Indian LMS that is a
    # difference we would meet, not a curiosity.
    assert pattern.flags & re.ASCII


def test_a_devanagari_digit_is_not_a_number_to_a_scorm_lms():
    assert CMI_DECIMAL.match("50")
    assert not CMI_DECIMAL.match("५०")
    assert not CMI_TIMESPAN.match("००:१२:३०.५०")


# --- timespans ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00:00.00"), (750.5, "00:12:30.50"), (45296.78, "12:34:56.78"), (3600, "01:00:00.00")],
)
def test_a_duration_renders_as_a_cmi_timespan(seconds, expected):
    assert format_timespan(seconds) == expected


def test_every_rendered_timespan_satisfies_moodles_regex():
    for seconds in [0, 0.5, 59.99, 60, 750.5, 3600, 45296.78, 359999.99]:
        assert MOODLE_CMI_TIMESPAN.match(format_timespan(seconds)), seconds


def test_the_hours_are_zero_padded_because_moodle_demands_two_digits():
    # Moodle's regex is ^([0-9]{2,4}): -- so "0:12:30" is refused, which would
    # otherwise be every session shorter than ten hours.
    assert format_timespan(750.5).startswith("00:")
    assert not MOODLE_CMI_TIMESPAN.match("0:12:30")


def test_an_iso_8601_duration_is_not_a_cmi_timespan():
    # "PT1H30M" is SCORM 2004's form and is refused by a 1.2 LMS.
    assert not MOODLE_CMI_TIMESPAN.match("PT1H30M")


def test_a_timespan_and_a_time_of_day_overlap_until_they_suddenly_do_not():
    # cmi.interactions.n.latency is a duration; .time is a time of day. The two
    # formats accept the same string for any duration under 30 hours, so mixing
    # them up is not caught by anything -- until a duration exceeds the clock and
    # the LMS starts refusing writes. Worth pinning: the overlap is why this is a
    # latent bug rather than an obvious one.
    assert MOODLE_CMI_TIME.match(format_timespan(45296.78))  # 12:34:56.78 -- looks fine
    assert not MOODLE_CMI_TIME.match(format_timespan(144000))  # 40:00:00.00 -- suddenly not
    assert MOODLE_CMI_TIMESPAN.match(format_timespan(144000))


# --- scores ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("earned", "possible", "expected"),
    [
        (100, 100, "100"),
        (50, 100, "50"),
        (0, 1, "0"),
        (1, 3, "33.33"),
        (850, 1000, "85"),
        (3, 4, "75"),
    ],
)
def test_a_score_renders_as_a_percentage(earned, possible, expected):
    assert format_score(earned, possible) == expected


def test_a_full_score_is_100_not_1():
    # The .2f in format_score guarantees a "." in the string, which is what stops
    # the rstrip("0") from eating the zeros: rstrip on a bare "100" gives "1".
    assert format_score(100, 100) == "100"
    assert format_score(10, 10) == "100"


def test_every_rendered_score_satisfies_moodles_regex_and_range():
    for earned, possible in [(0, 1), (1, 3), (850, 1000), (100, 100), (2, 7), (999, 1000)]:
        rendered = format_score(earned, possible)
        assert MOODLE_CMI_DECIMAL.match(rendered), rendered
        assert 0.0 <= float(rendered) <= 100.0


def test_a_score_is_normalised_rather_than_raw():
    # SCORM 1.2 requires score.raw to be 0-100. Moodle enforces the range and
    # Open edX divides by 100 while ignoring score.max, so a raw of 850/1000
    # would grade as 850% there.
    assert format_score(850, 1000) == "85"
    assert format_score(5, 4) == "100"  # clamped, never over 100
    assert format_score(-3, 4) == "0"  # clamped, never negative


def test_a_score_out_of_nothing_is_zero_not_a_crash():
    assert format_score(0, 0) == "0"


def test_a_raw_score_over_999_would_fail_moodles_decimal_format():
    # Not a thing we can emit -- the point is that the 0-100 rule is what keeps us
    # inside CMIDecimal's three integer digits.
    assert not MOODLE_CMI_DECIMAL.match("1000")


# --- vocabularies ------------------------------------------------------------


def test_not_attempted_is_not_a_writable_lesson_status():
    # The RTE book lists six values, but Moodle binds WRITES to five. A SCO that
    # reads the status and echoes it back fails on its first write.
    assert "not attempted" not in LESSON_STATUS_WRITABLE
    assert not MOODLE_CMI_STATUS.match("not attempted")


def test_every_status_we_would_write_is_one_moodle_accepts():
    for status in LESSON_STATUS_WRITABLE:
        assert MOODLE_CMI_STATUS.match(status), status


def test_an_empty_exit_is_a_normal_exit_and_normal_is_not_a_value():
    assert "" in EXIT_VALUES
    assert "normal" not in EXIT_VALUES


# --- response identifiers ----------------------------------------------------


def test_options_map_to_single_character_identifiers():
    assert [response_char(i) for i in range(4)] == ["a", "b", "c", "d"]
    assert response_char(26) == "0"


def test_more_than_36_options_cannot_be_identified():
    # choice/matching identifiers are ONE character from 0-9a-z, so there is no
    # 37th. The emitter turns this into a warning rather than a broken pattern.
    with pytest.raises(ValueError, match="single character"):
        response_char(36)


# --- the manifest ------------------------------------------------------------


def test_the_manifest_is_not_pretty_printed():
    # Open edX matches ^1.2$ against schemaversion's text. Indent the XML and that
    # text becomes "\n  1.2\n", the match fails, the package is treated as SCORM
    # 2004, and the LMS injects API_1484_11 -- so our SCO finds no API and reports
    # nothing at all, while the quiz renders perfectly.
    xml = _manifest()
    assert "<schemaversion>1.2</schemaversion>" in xml
    assert "\n  <" not in xml


def test_open_edx_detects_scorm_12_and_finds_our_launch_file():
    # Replicated verbatim from openedxscorm/scormxblock.py.
    xml = _manifest().encode("utf-8")
    namespaces = {
        prefix: uri for _, (prefix, uri) in ET.iterparse(io.BytesIO(xml), events=["start-ns"])
    }
    namespace = namespaces.get("", "")
    prefix = "{" + namespace + "}" if namespace else ""

    root = ET.fromstring(xml)
    resource = root.find(f"{prefix}resources/{prefix}resource[@href]")
    schemaversion = root.find(f"{prefix}metadata/{prefix}schemaversion")

    assert resource is not None and resource.get("href") == "index.html"
    assert re.match("^1.2$", schemaversion.text) is not None


def test_the_ims_namespace_is_the_default_one():
    # Open edX takes the first namespace whose prefix is empty and uses it to find
    # the resource. Bind IMS CP to a prefix instead and it finds nothing.
    xml = _manifest()
    assert 'xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"' in xml
    assert "ns0:" not in xml


def test_moodle_can_see_the_scormtype_attribute():
    # Moodle parses without namespace processing and matches the upper-cased
    # literal string 'ADLCP:SCORMTYPE'. The correct URI bound to a different
    # prefix is valid XML that Moodle cannot see -- it would default the resource
    # to 'asset' and the SCO would never make an API call.
    upper = _manifest().upper()
    assert 'ADLCP:SCORMTYPE="SCO"' in upper


def test_the_adlcp_namespace_is_scorm_12_not_scorm_11():
    # Moodle ignores namespace URIs entirely, so a wrong one passes a Moodle test
    # and fails schema validation everywhere else. This is the only guard.
    xml = _manifest()
    assert 'xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"' in xml
    assert "adl_cp_rootv1p1" not in xml


def test_the_manifest_declares_adl_scorm_1_2():
    assert "<schema>ADL SCORM</schema>" in _manifest()


def test_there_is_exactly_one_resource_and_it_carries_the_href():
    # Open edX takes the FIRST resource with an @href and never checks scormtype,
    # so an asset listed first would hijack the launch.
    root = ET.fromstring(_manifest())
    ns = "{http://www.imsproject.org/xsd/imscp_rootv1p1p2}"
    assert len(root.findall(f"{ns}resources/{ns}resource[@href]")) == 1


def test_mastery_score_is_not_declared():
    # With it present Moodle stops believing the SCO's own lesson_status and
    # derives pass/fail itself, while Open edX has no mastery path at all and
    # would never mark success. One authority: our player.
    assert "masteryscore" not in _manifest().lower()


def test_both_titles_are_present_because_open_edx_reads_them_unguarded():
    # find_titles_recursively does an unguarded .text, so a missing <title> is an
    # AttributeError at import time.
    root = ET.fromstring(_manifest())
    ns = "{http://www.imsproject.org/xsd/imscp_rootv1p1p2}"
    org = root.find(f"{ns}organizations/{ns}organization")
    assert org.find(f"{ns}title").text == "The Water Cycle"
    assert org.find(f"{ns}item/{ns}title").text == "The Water Cycle"


def test_an_awkward_assessment_id_still_yields_a_safe_identifier():
    assert sanitise_identifier("a b/c") == "a-b-c"
    assert sanitise_identifier("") == "ASSESSMENT"
    assert sanitise_identifier("123") == "ASSESSMENT-123"


# --- the ZIP -----------------------------------------------------------------


def _zip() -> zipfile.ZipFile:
    payload = write_scorm(
        manifest=_manifest().encode("utf-8"),
        files={"index.html": b"<html></html>", "scorm/api.js": b"//"},
    )
    return zipfile.ZipFile(io.BytesIO(payload))


def test_the_manifest_sits_at_the_archive_root():
    # Open edX finds the package root by looking for a member basenamed
    # imsmanifest.xml; Moodle requires it at the root outright.
    assert _zip().namelist()[0] == "imsmanifest.xml"


def test_the_package_has_no_directory_entries():
    assert [i.filename for i in _zip().infolist() if i.is_dir()] == []


def test_the_package_is_a_readable_deflated_zip():
    archive = _zip()
    assert archive.testzip() is None
    assert {i.compress_type for i in archive.infolist()} == {zipfile.ZIP_DEFLATED}


def test_the_same_inputs_emit_the_same_bytes():
    first = write_scorm(manifest=b"<m/>", files={"index.html": b"x"})
    second = write_scorm(manifest=b"<m/>", files={"index.html": b"x"})
    assert first == second
