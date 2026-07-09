"""Unit tests for the assessment pipeline.

The deterministic parts (sectioning, grounding, id assignment, the drop rules)
are pure and tested directly; the per-type model call is exercised through a
mocked gateway so these stay offline and deterministic.
"""

import asyncio
import json
import re
from datetime import datetime, timezone

import httpx
import pytest

from app.assessment.pipeline import (
    _build_sections,
    _has_latex,
    _is_grounded,
    _normalise,
    generate_assessment,
)
from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.summarization.pipeline import EmptyDocumentError, GenerationConfig

_EVIDENCE = "Plants make food from light in the chloroplast."


def _doc(pages: list[Page], fmt: str = "pdf") -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="x." + fmt, format=fmt, page_count=len(pages), title="Bio"),
        parser="test",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=pages,
    )


def _lesson() -> ParsedDocument:
    return _doc(
        [
            Page(
                index=1,
                kind="slide",
                blocks=[
                    Block(kind=BlockKind.heading, text="Photosynthesis", level=1),
                    Block(kind=BlockKind.paragraph, text=_EVIDENCE),
                ],
                notes="Say hello.",
            )
        ],
        fmt="pptx",
    )


def _config(max_source_chars: int = 24000) -> GenerationConfig:
    return GenerationConfig(
        base_url="https://gateway/v1",
        api_key="k",
        model="m",
        provider="test",
        temperature=0.0,
        max_source_chars=max_source_chars,
    )


def _reply(questions: list[dict]) -> httpx.Response:
    content = json.dumps({"questions": questions})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _run(doc, handler, *, types=("mcq",), count=3, config=None, language="en"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))
    try:
        return asyncio.run(
            generate_assessment(
                client, doc, config or _config(), question_types=list(types), count=count, language=language
            )
        )
    finally:
        asyncio.run(client.aclose())


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #
def test_build_sections_folds_page_and_notes():
    sections = _build_sections(_lesson())
    assert len(sections) == 1
    assert sections[0].title == "Photosynthesis"
    assert sections[0].source_index == 1
    assert "chloroplast" in sections[0].text
    assert "Say hello." in sections[0].text  # notes folded in


def test_build_sections_skips_pages_with_no_text():
    doc = _doc([Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])])
    assert _build_sections(doc) == []


def test_is_grounded_matches_despite_formatting():
    hay = [_normalise("Plants  make food\nfrom light in the chloroplast.")]
    assert _is_grounded("plants MAKE food from light", hay)
    assert not _is_grounded("photosynthesis needs carbon dioxide", hay)


def test_is_grounded_rejects_too_short_evidence():
    assert not _is_grounded("cell", [_normalise("the cell is small")])


def test_has_latex_detects_portable_delimiters():
    assert _has_latex(r"the area is \( \pi r^2 \)")
    assert _has_latex("displayed $$E = mc^2$$")
    assert not _has_latex("no math here", "just words")


# --------------------------------------------------------------------------- #
# Generation — happy paths
# --------------------------------------------------------------------------- #
def test_generate_mcq_assigns_ids_and_grounds():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": "Where do plants make food?",
                    "options": [
                        {"text": "Chloroplast", "is_correct": True},
                        {"text": "Nucleus", "is_correct": False},
                        {"text": "Vacuole", "is_correct": False},
                    ],
                    "explanation": "The source says food is made in the chloroplast.",
                }
            ]
        )

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.counts == {"mcq": 1}
    assert result.max_points == pytest.approx(1.0)
    assert result.assessment_id and result.language == "en" and result.pass_percentage == 50
    q = result.questions[0]
    assert q.type == "mcq" and q.id == "q1" and q.source_index == 1
    assert [c.id for c in q.choices] == ["q1-c1", "q1-c2", "q1-c3"]
    assert sum(c.is_correct for c in q.choices) == 1
    assert result.warnings == []


def test_generate_match_builds_sources_targets_and_distractors():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": "Match each term.",
                    "pairs": [{"left": "Plants", "right": "Food"}, {"left": "Light", "right": "Energy"}],
                    "distractors": ["Water"],
                }
            ]
        )

    q = _run(_lesson(), handler, types=("match",)).questions[0]
    assert q.type == "match"
    assert {s.id: s.target_id for s in q.sources} == {"q1-s1": "q1-t1", "q1-s2": "q1-t2"}
    assert [t.id for t in q.targets] == ["q1-t1", "q1-t2", "q1-t3"]  # distractor appended
    assert q.targets[-1].text == "Water"


def test_generate_fill_blank_keeps_markers_and_answers():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "text": "Plants make food from light in the [[1]].",
                    "blanks": [{"answers": ["chloroplast", "chloroplasts"], "tip": "an organelle"}],
                }
            ]
        )

    q = _run(_lesson(), handler, types=("fill_blank",)).questions[0]
    assert q.type == "fill_blank"
    assert q.blanks[0].id == "q1-b1" and q.blanks[0].answers[0] == "chloroplast"
    assert q.case_sensitive is False and q.order_matters is True


def test_fill_blank_drops_ungrounded_alternative_answers():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "text": "Plants make food from light in the [[1]].",
                    "blanks": [{"answers": ["chloroplast", "petals"]}],  # 'petals' is invented
                }
            ]
        )

    q = _run(_lesson(), handler, types=("fill_blank",)).questions[0]
    assert q.blanks[0].answers == ["chloroplast"]  # the ungrounded alternative is dropped


def test_only_requested_types_are_generated():
    calls: list[str] = []

    def handler(req):
        user = json.loads(req.content)["messages"][1]["content"]
        calls.append("mcq" if "multiple-choice" in user else "other")
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": "Where do plants make food?",
                    "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "Nucleus", "is_correct": False}],
                }
            ]
        )

    _run(_lesson(), handler, types=("mcq",))
    assert calls == ["mcq"]  # exactly one model call, only for MCQ


# --------------------------------------------------------------------------- #
# Grounding & drop rules
# --------------------------------------------------------------------------- #
def test_ungrounded_question_is_dropped_with_warning():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": "Jupiter is the largest planet in the solar system.",
                    "prompt": "Which planet is largest?",
                    "options": [{"text": "Jupiter", "is_correct": True}, {"text": "Mars", "is_correct": False}],
                }
            ]
        )

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == []
    assert any("not grounded" in w for w in result.warnings)


def test_mcq_with_multiple_correct_is_dropped():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": "Pick one.",
                    "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "Leaf", "is_correct": True}],
                }
            ]
        )

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == []
    assert any("malformed multiple-choice" in w for w in result.warnings)


def test_fill_blank_answer_not_in_evidence_is_dropped():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "text": "Plants make food in the [[1]].",
                    "blanks": [{"answers": ["mitochondrion"]}],  # not in the evidence sentence
                }
            ]
        )

    result = _run(_lesson(), handler, types=("fill_blank",))
    assert result.questions == []
    assert any("not found in the source" in w for w in result.warnings)


def test_wrong_section_claim_is_reattributed_to_the_real_section():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 99,  # the model mislabels the section
                    "evidence": _EVIDENCE,  # but the quote is really from section 1 (page 1)
                    "prompt": "Where do plants make food?",
                    "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "Nucleus", "is_correct": False}],
                }
            ]
        )

    # Attribution finds the page the evidence is actually on, not the claimed one.
    q = _run(_lesson(), handler, types=("mcq",)).questions[0]
    assert q.source_index == 1


def test_field_name_aliases_are_tolerated():
    def handler(_req):
        # model uses "question"/"answers" instead of "prompt"/"options"
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "question": "Where do plants make food?",
                    "answers": [{"text": "Chloroplast", "is_correct": True}, {"text": "Nucleus", "is_correct": False}],
                }
            ]
        )

    q = _run(_lesson(), handler, types=("mcq",)).questions[0]
    assert q.prompt == "Where do plants make food?" and len(q.choices) == 2


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
def test_unusable_model_output_degrades_to_warning():
    def handler(_req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == []
    assert any("Could not generate mcq" in w for w in result.warnings)


def test_empty_document_raises():
    def handler(_req):
        return _reply([])

    empty = _doc([Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])])
    with pytest.raises(EmptyDocumentError):
        _run(empty, handler)


def test_size_limit_leaving_no_sections_raises():
    def handler(_req):
        return _reply([])

    doc = _lesson()
    config = _config(max_source_chars=0)
    with pytest.raises(EmptyDocumentError):
        _run(doc, handler, config=config)


def test_unreachable_gateway_propagates():
    from app.summarization.llm_client import LLMUnavailable

    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    doc = _lesson()
    with pytest.raises(LLMUnavailable):
        _run(doc, handler, types=("mcq",))


def test_partial_degradation_across_types():
    def handler(req):
        user = json.loads(req.content)["messages"][1]["content"]
        if "match-the-pair" in user:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        if "multiple-choice" in user:
            return _reply(
                [
                    {
                        "source_section": 1,
                        "evidence": _EVIDENCE,
                        "prompt": "Where?",
                        "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "Nucleus", "is_correct": False}],
                    }
                ]
            )
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "text": "Plants make food from light in the [[1]].",
                    "blanks": [{"answers": ["chloroplast"]}],
                }
            ]
        )

    result = _run(_lesson(), handler, types=("mcq", "match", "fill_blank"))
    assert result.counts == {"mcq": 1, "fill_blank": 1}  # the good types survive
    assert any("Could not generate match" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# source_index attribution across a multi-page document
# --------------------------------------------------------------------------- #
def _multipage() -> ParsedDocument:
    return _doc(
        [
            Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)]),  # no text -> skipped
            Page(index=2, kind="page", blocks=[Block(kind=BlockKind.paragraph, text="Mitochondria produce energy for the cell.")]),
            Page(index=3, kind="page", blocks=[Block(kind=BlockKind.paragraph, text="Chlorophyll absorbs sunlight in the leaf.")]),
        ],
        fmt="pdf",
    )


def test_source_index_attributes_to_the_real_page():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,  # wrong claim
                    "evidence": "Chlorophyll absorbs sunlight in the leaf.",
                    "prompt": "What absorbs sunlight?",
                    "options": [{"text": "Chlorophyll", "is_correct": True}, {"text": "Water", "is_correct": False}],
                }
            ]
        )

    q = _run(_multipage(), handler, types=("mcq",)).questions[0]
    assert q.source_index == 3  # attributed to page 3, where the evidence really is


def test_source_index_none_when_evidence_spans_sections():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": "the cell. Chlorophyll absorbs sunlight",  # crosses the section boundary
                    "prompt": "Q?",
                    "options": [{"text": "Chlorophyll", "is_correct": True}, {"text": "Water", "is_correct": False}],
                }
            ]
        )

    q = _run(_multipage(), handler, types=("mcq",)).questions[0]
    assert q.source_index is None  # grounded via the whole doc, but not on any one page


# --------------------------------------------------------------------------- #
# id assignment
# --------------------------------------------------------------------------- #
def test_ids_are_gapless_across_dropped_questions():
    def handler(_req):
        return _reply(
            [
                {"source_section": 1, "evidence": _EVIDENCE, "prompt": "A?", "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "X", "is_correct": False}]},
                {"source_section": 1, "evidence": "Jupiter is the largest planet.", "prompt": "B?", "options": [{"text": "Jupiter", "is_correct": True}, {"text": "Mars", "is_correct": False}]},
                {"source_section": 1, "evidence": _EVIDENCE, "prompt": "C?", "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "Y", "is_correct": False}]},
            ]
        )

    result = _run(_lesson(), handler, types=("mcq",), count=3)
    assert [q.id for q in result.questions] == ["q1", "q2"]  # middle one dropped, ids stay gapless


# --------------------------------------------------------------------------- #
# more drop rules
# --------------------------------------------------------------------------- #
def test_mcq_empty_prompt_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "prompt": "   ", "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "X", "is_correct": False}]}])

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == [] and any("empty or not grounded" in w for w in result.warnings)


def test_mcq_fewer_than_two_options_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "prompt": "Q?", "options": [{"text": "Chloroplast", "is_correct": True}]}])

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == [] and any("fewer than two options" in w for w in result.warnings)


def test_match_ungrounded_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": "Jupiter is the largest planet.", "prompt": "Match", "pairs": [{"left": "a", "right": "b"}, {"left": "c", "right": "d"}]}])

    result = _run(_lesson(), handler, types=("match",))
    assert result.questions == [] and any("not grounded" in w for w in result.warnings)


def test_match_fewer_than_two_pairs_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "prompt": "Match", "pairs": [{"left": "Plants", "right": "Food"}]}])

    result = _run(_lesson(), handler, types=("match",))
    assert result.questions == [] and any("fewer than two pairs" in w for w in result.warnings)


def test_fill_blank_multi_blank_happy_path():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "text": "Plants make [[1]] from light in the [[2]].", "blanks": [{"answers": ["food"]}, {"answers": ["chloroplast"]}]}])

    q = _run(_lesson(), handler, types=("fill_blank",)).questions[0]
    assert [b.id for b in q.blanks] == ["q1-b1", "q1-b2"]
    assert q.blanks[0].answers == ["food"] and q.blanks[1].answers == ["chloroplast"]


def test_fill_blank_marker_blank_mismatch_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "text": "Plants make food in the [[1]] and [[2]].", "blanks": [{"answers": ["chloroplast"]}]}])

    result = _run(_lesson(), handler, types=("fill_blank",))
    assert result.questions == [] and any("markers did not match" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
# bounding, block rendering, count clamp, field passthrough
# --------------------------------------------------------------------------- #
def test_section_cap_warns_and_limits():
    pages = [Page(index=i, kind="page", blocks=[Block(kind=BlockKind.paragraph, text=f"Fact number {i} about cells.")]) for i in range(1, 46)]
    result = _run(_doc(pages, fmt="pdf"), lambda _req: _reply([]), types=("mcq",))
    assert any("used the first 40" in w for w in result.warnings)


def test_char_truncation_warns():
    result = _run(_lesson(), lambda _req: _reply([]), types=("mcq",), config=_config(max_source_chars=20))
    assert any("truncated to about 20" in w for w in result.warnings)


def test_build_sections_renders_list_and_table():
    page = Page(
        index=1,
        kind="page",
        blocks=[
            Block(kind=BlockKind.list, items=["one", "two"]),
            Block(kind=BlockKind.table, rows=[["a", "b"], ["c", "d"]]),
        ],
    )
    sections = _build_sections(_doc([page], fmt="pdf"))
    assert "- one" in sections[0].text and "- two" in sections[0].text
    assert "a | b" in sections[0].text and "c | d" in sections[0].text


def test_count_is_clamped_in_the_pipeline():
    seen: dict[str, int] = {}

    def handler(req):
        user = json.loads(req.content)["messages"][1]["content"]
        seen["n"] = int(re.search(r"up to (\d+)", user).group(1))
        return _reply([])

    _run(_lesson(), handler, types=("mcq",), count=0)
    assert seen["n"] == 1  # clamped up to the floor
    _run(_lesson(), handler, types=("mcq",), count=100)
    assert seen["n"] == 20  # clamped down to _MAX_PER_TYPE


def test_has_latex_and_optional_fields_pass_through():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": r"Which is right? \( x^2 \)",
                    "options": [{"text": "Chloroplast", "is_correct": True, "feedback": "correct!"}, {"text": "Nucleus", "is_correct": False}],
                    "explanation": "Because the source says so.",
                }
            ]
        )

    q = _run(_lesson(), handler, types=("mcq",)).questions[0]
    assert q.has_latex is True
    assert q.explanation == "Because the source says so."
    assert q.choices[0].feedback == "correct!"


def test_fill_blank_tip_passes_through():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "text": "Plants make food from light in the [[1]].", "blanks": [{"answers": ["chloroplast"], "tip": "an organelle"}]}])

    q = _run(_lesson(), handler, types=("fill_blank",)).questions[0]
    assert q.blanks[0].tip == "an organelle"


# --------------------------------------------------------------------------- #
# dedup / uniqueness (adopted from review)
# --------------------------------------------------------------------------- #
def test_mcq_duplicate_option_text_is_dropped():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "prompt": "Q?", "options": [{"text": "Chloroplast", "is_correct": True}, {"text": "chloroplast", "is_correct": False}]}])

    result = _run(_lesson(), handler, types=("mcq",))
    assert result.questions == [] and any("malformed multiple-choice" in w for w in result.warnings)


def test_match_distractor_duplicating_a_target_is_skipped():
    def handler(_req):
        return _reply(
            [
                {
                    "source_section": 1,
                    "evidence": _EVIDENCE,
                    "prompt": "Match",
                    "pairs": [{"left": "Plants", "right": "Food"}, {"left": "Light", "right": "Energy"}],
                    "distractors": ["Food", "Water"],  # "Food" already a correct target
                }
            ]
        )

    q = _run(_lesson(), handler, types=("match",)).questions[0]
    assert [t.text for t in q.targets] == ["Food", "Energy", "Water"]  # duplicate "Food" skipped


def test_fill_blank_dedupes_alternative_answers():
    def handler(_req):
        return _reply([{"source_section": 1, "evidence": _EVIDENCE, "text": "Plants make food from light in the [[1]].", "blanks": [{"answers": ["chloroplast", "Chloroplast", "chloroplast"]}]}])

    q = _run(_lesson(), handler, types=("fill_blank",)).questions[0]
    assert q.blanks[0].answers == ["chloroplast"]  # case-insensitive duplicates collapsed
