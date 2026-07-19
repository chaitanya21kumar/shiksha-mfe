"""Differential test: our grader must agree with H5P.Essay's own algorithm.

The whole design rests on one property — the same answer scores the same mark
whether the tenant imported the H5P package or the SCORM one. Three
implementations exist (Python here, JavaScript in the shipped player, and H5P's
own library), so that property is only true if it is *tested*, not asserted in a
comment.

`tests/parity/h5p_essay_reference.js` is a verbatim transcription of
`Essay.getInput`, `Essay.detectExactMatches` and `H5P.TextUtilities.isIsolated`
from the version the Hub serves. This test drives a corpus through both it and
`app.assessment.grading`, and through the matcher the SCORM player actually
ships, and requires all three to return identical marks.

The corpus deliberately includes the awkward cases — the whitespace-halving
quirk, tabs, substring traps, the consumed-haystack behaviour, and both
documented failure modes — because those are exactly where a "tidier"
re-implementation silently drifts.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.assessment.grading import score_short_answer
from app.assessment.schema import KeyPoint, ShortAnswerItem

_REFERENCE = Path(__file__).parent / "parity" / "h5p_essay_reference.js"
_PLAYER = Path(__file__).resolve().parents[1] / "app/packaging/scorm/assets/player.js"

_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node is not installed")

KEY_POINTS = [
    KeyPoint(id="k1", text="Land warms faster", accepted=["land heats", "land warms"]),
    KeyPoint(id="k2", text="Air moves inland", accepted=["sea to land", "onshore wind"]),
    KeyPoint(id="k3", text="It reverses at night", accepted=["it reverses", "reverses at night"]),
]

#: The keywords those key points become in the emitted H5P params.
KEYWORDS = [
    {
        "keyword": point.accepted[0],
        "alternatives": point.accepted[1:],
        "options": {"points": point.weight, "occurrences": 1, "caseSensitive": False},
    }
    for point in KEY_POINTS
]

CORPUS = [
    "",
    "   ",
    "The land heats faster than the water, so wind blows from sea to land. It reverses at night.",
    "the land heats",
    "LAND HEATS and IT REVERSES",
    "land heats sea to land it reverses",  # keyword stuffing
    "The ground warms quicker, pulling damp air inland; at night it flips.",  # paraphrase
    "grassland heats up",  # substring trap
    "the land heats",  # exact, at the very end
    "land heats",  # exact, whole string
    "wind blows from sea   to land",  # triple space defeats the phrase
    "wind blows from sea  to land",  # double space collapses back
    "The land heats.\nIt reverses at night.",  # newlines
    "The land heats.\r\nIt reverses at night.",  # CRLF
    "The\tland heats",  # tab
    "The\t\tland heats",  # tab pair — \s\s collapses this
    "onshore windonshore wind",  # consumed-haystack behaviour
    "land heatsland heats",  # ditto
    "x" * 300 + " land heats",
    "It reverses at night, it reverses at night.",
    "sea to land.",
    "sea to land!",
    "'land heats'",
    '"it reverses"',
    "It, land heats, reverses",
]


def _item() -> ShortAnswerItem:
    return ShortAnswerItem(
        id="q1",
        prompt="Explain it.",
        points=float(len(KEY_POINTS)),
        model_answer="The land heats, wind blows from sea to land, and it reverses at night.",
        key_points=KEY_POINTS,
    )


def _run_node(script: str, payload: dict) -> list[float]:
    result = subprocess.run(
        [_NODE, script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(result.stdout)


@_needs_node
def test_our_grader_agrees_with_h5ps_own_algorithm_on_every_case():
    ours = [score_short_answer(_item(), answer) for answer in CORPUS]
    theirs = _run_node(str(_REFERENCE), {"keywords": KEYWORDS, "answers": CORPUS})

    mismatches = [
        (answer, mine, mine_h5p)
        for answer, mine, mine_h5p in zip(CORPUS, ours, theirs)
        if mine != mine_h5p
    ]
    assert not mismatches, f"our grader and H5P.Essay disagree on: {mismatches}"


@_needs_node
def test_the_shipped_player_agrees_with_our_grader_on_every_case(tmp_path):
    # Extract the matcher from the player we actually ship, so this cannot pass
    # against a copy that has drifted from the file in the package.
    source = _PLAYER.read_text(encoding="utf-8")
    wanted = ["essayNormalise", "isIsolated", "occursIsolated"]
    extracted = []
    for name in wanted:
        match = re.search(rf"\n  function {name}\(.*?\n  }}\n", source, re.S)
        assert match, f"{name} not found in the shipped player.js"
        extracted.append(match.group(0).replace("\n  ", "\n"))

    harness = tmp_path / "player_matcher.js"
    harness.write_text(
        'var WORD_DELIMITER = /[\\s.?!,\';"]/;\n'
        + "".join(extracted)
        + """
function score(keywords, answer) {
  var hay = essayNormalise(answer);
  var total = 0;
  keywords.forEach(function (group) {
    var forms = [group.keyword].concat(group.alternatives || []);
    var hit = forms.some(function (form) {
      var needle = String(form).trim().toLowerCase();
      return needle !== '' && occursIsolated(needle, hay);
    });
    if (hit) total += group.options.points * group.options.occurrences;
  });
  return total;
}
var chunks = [];
process.stdin.on('data', function (c) { chunks.push(c); });
process.stdin.on('end', function () {
  var input = JSON.parse(chunks.join(''));
  process.stdout.write(JSON.stringify(input.answers.map(function (a) {
    return score(input.keywords, a);
  })));
});
""",
        encoding="utf-8",
    )

    ours = [score_short_answer(_item(), answer) for answer in CORPUS]
    player = _run_node(str(harness), {"keywords": KEYWORDS, "answers": CORPUS})

    mismatches = [
        (answer, mine, theirs)
        for answer, mine, theirs in zip(CORPUS, ours, player)
        if mine != theirs
    ]
    assert not mismatches, f"our grader and the shipped player disagree on: {mismatches}"
