"""Run the generated SCORM package in a real browser.

Every other test in this suite asserts on bytes: the JSON we write, the fields we
set, the files in the ZIP. None of them can tell whether the countdown counts,
whether an expiry submits past the minimum-length guard, or whether reloading the
page hands a learner their time back. Those are properties of the player running,
and the only honest way to check them is to run it.

Skipped unless both `node` and `puppeteer-core` are available, in the same spirit
as `test_grader_parity.py`: the offline suite must never require a browser. Run

    npm install puppeteer-core

anywhere on the module path to enable it, and set `CHROME_PATH` if Chrome is not
at the macOS default location.

The guard being live matters more than it looks. `min_chars` defaults to 0, so a
short answer built from the plain factory can always be submitted — and against
that fixture a forced submit and an ordinary one are indistinguishable. The first
version of this check passed for exactly that reason while proving nothing, so
the fixture below sets `min_chars` deliberately.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from app.assessment.emit.scorm import emit_scorm
from tests.factories import make_mcq, make_set, make_short

HARNESS = Path(__file__).parent / "player" / "drive_player.js"


def _node_can_drive_a_browser() -> bool:
    if shutil.which("node") is None:
        return False
    probe = subprocess.run(
        ["node", "-e", "require.resolve('puppeteer-core')"], capture_output=True
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not _node_can_drive_a_browser(),
    reason="needs node and puppeteer-core; the offline suite must not require a browser",
)


@pytest.fixture(scope="module")
def unpacked(tmp_path_factory) -> Path:
    """Three real packages, unzipped where a browser can open them."""
    root = tmp_path_factory.mktemp("player-check")
    cases = {
        # A short answer with a live guard, plus a clock: the expiry has to get
        # past the guard, and with min_chars at its default of 0 it would not
        # have to try.
        "timed": make_set(
            questions=[make_short(min_chars=40), make_mcq(id="q2")], time_limit_seconds=30
        ),
        "withheld": make_set(
            questions=[make_short(min_chars=40), make_mcq(id="q2")],
            solution_visibility="never",
        ),
        "open": make_set(questions=[make_short(min_chars=40), make_mcq(id="q2")]),
    }
    for name, assessment in cases.items():
        target = root / name
        target.mkdir()
        zipfile.ZipFile(io.BytesIO(emit_scorm(assessment).content)).extractall(target)
    return root


def test_the_player_behaves_as_the_teacher_configured_it(unpacked: Path):
    run = subprocess.run(
        ["node", str(HARNESS), str(unpacked)], capture_output=True, text=True, timeout=180
    )
    assert run.stdout.strip(), f"the harness printed nothing\nstderr:\n{run.stderr}"
    # The last line is the JSON summary. A crash mid-run leaves something else
    # there, and letting json raise shows exactly what, which is more useful than
    # a message we wrote guessing at it.
    summary = json.loads(run.stdout.strip().splitlines()[-1])
    assert summary.get("failed") == [], (
        f"{summary.get('failed')} failed out of {summary.get('total')}\n{run.stdout}"
    )
