"""The benchmark harness itself.

A measuring instrument that is not checked is not evidence. These do not run the
benchmarks — those cost live model calls and are a command someone runs deliberately.
They check that the thing doing the measuring measures what it claims to.

The two properties that matter are the ones a reader of the published numbers is
implicitly trusting: that the documents are the size they are reported to be and are
the same on every machine, and that the split between "our time" and "the provider's
time" is arithmetic rather than a guess.
"""

from __future__ import annotations

import httpx
import pytest

from benchmarks.corpus import SIZES, Document, build_pdf
from benchmarks.instruments import Timings, TimingTransport


# --- the corpus -------------------------------------------------------------------


@pytest.mark.parametrize("pages", [2, 5])
def test_a_document_has_at_least_the_pages_it_was_asked_for(pages):
    """The first version estimated how many sections fit a page and was asked for
    sixty, producing thirty-two. Reported sizes have to be real ones."""
    document = build_pdf(pages)
    assert document.pages >= pages


def test_the_reported_page_count_is_what_the_pdf_actually_holds():
    """Read back off the finished file rather than carried from the request."""
    document = build_pdf(5)
    import fitz

    with fitz.open(stream=document.content, filetype="pdf") as opened:
        assert opened.page_count == document.pages


def test_the_same_size_gives_byte_identical_documents():
    """Without this, a number measured today cannot be compared with one measured
    next month, because the input would have quietly changed."""
    assert build_pdf(3).content == build_pdf(3).content


def test_a_document_carries_the_counts_a_report_row_needs():
    document = build_pdf(3)
    assert document.words > 0
    assert document.sections > 0
    assert document.kilobytes > 0
    assert isinstance(document, Document)


def test_the_documents_are_structured_rather_than_one_block_of_text():
    """Every module past ingestion works off the sections the parser found, so a flat
    document would under-report the whole pipeline."""
    import fitz

    with fitz.open(stream=build_pdf(3).content, filetype="pdf") as opened:
        text = "\n".join(page.get_text() for page in opened)
    assert "Evaporation from Open Water" in text
    assert "Condensation and Cloud Formation" in text


def test_the_published_sizes_span_small_to_large():
    """A benchmark that only measures the sample everyone has already seen says
    nothing about a real teaching document."""
    assert min(SIZES) <= 2
    assert max(SIZES) >= 50


# --- the split between our time and the provider's ----------------------------------


def test_engine_time_is_the_wall_clock_minus_the_waiting():
    timings = Timings(provider_seconds=9.0, total_seconds=10.0)
    assert timings.engine_seconds == pytest.approx(1.0)


def test_engine_time_never_goes_negative():
    """The two clocks are read at different moments, so a run with almost no engine
    work can come out fractionally negative. That is a measurement artefact, and
    reporting it as a negative duration would look like a bug in the engine."""
    timings = Timings(provider_seconds=10.0001, total_seconds=10.0)
    assert timings.engine_seconds == 0.0


def test_the_provider_share_is_a_fraction():
    assert Timings(provider_seconds=5.0, total_seconds=10.0).provider_share == pytest.approx(0.5)
    assert Timings().provider_share == 0.0


def test_a_run_with_no_calls_is_all_engine_time():
    timings = Timings(total_seconds=2.0)
    assert timings.engine_seconds == pytest.approx(2.0)
    assert timings.provider_calls == 0


@pytest.mark.anyio
async def test_every_request_is_counted_including_ones_the_caller_never_sees():
    """Measured at the transport rather than around a call, so a retry inside the
    client counts too — otherwise a slow run would look fast and unexplained."""
    timings = Timings()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = TimingTransport(httpx.MockTransport(respond), timings)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://example.invalid/one")
        await client.get("https://example.invalid/two")

    assert timings.provider_calls == 2
    assert len(timings.call_seconds) == 2
    assert timings.provider_seconds == pytest.approx(sum(timings.call_seconds))


@pytest.mark.anyio
async def test_a_failed_request_is_still_counted():
    """Time spent waiting on a call that errored is still time the run took."""
    timings = Timings()

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    transport = TimingTransport(httpx.MockTransport(explode), timings)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://example.invalid/boom")

    assert timings.provider_calls == 1
    assert timings.provider_seconds > 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
