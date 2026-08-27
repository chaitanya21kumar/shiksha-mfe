"""Documents to measure against, built to order and always the same.

The engine ships with a two-page sample, which is fine for checking it works and
useless for measuring. The number that matters to a tenant is what happens to a real
teaching document — a chapter, a unit, a whole module — so these are generated at
three sizes spanning that range.

Two properties matter more than the prose itself:

**They are structured.** Headings, paragraphs of varying length, and sections that end
where a teacher would end them. A document of undifferentiated text measures a parser
that never has to decide anything, and every module past ingestion works off the
sections the parser found, so a flat document under-reports the whole pipeline.

**They are deterministic.** The same size argument gives byte-identical output on every
machine, so a number measured today can be compared with one measured next month. That
rules out random filler, and it is why the prose below is a fixed pool cycled in order
rather than sampled.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import fitz

#: Real explanatory prose, because parsing and sectioning both behave differently on
#: text with actual sentence structure than on filler. Ordinary paragraphs of the kind
#: a science textbook carries: a claim, a mechanism, and a consequence.
_PARAGRAPHS: tuple[str, ...] = (
    (
        "Water moves continuously between the ocean, the atmosphere and the land. The same "
        "water has been circulating for billions of years, changing state but never leaving "
        "the system. Understanding where it goes, and how quickly, is what allows a region to "
        "plan for both drought and flood."
    ),

    (
        "Evaporation is driven almost entirely by solar energy. Sunlight warms the surface of "
        "the ocean and the molecules with the most energy escape into the air as vapour. Because "
        "warmer air holds more vapour than cooler air, the rate rises steeply with temperature, "
        "which is why the tropics contribute far more than the poles."
    ),

    (
        "Plants return water to the atmosphere through transpiration, drawing it up from the "
        "soil and releasing it through pores in their leaves. Over a forested catchment this can "
        "account for a larger share of the returned water than evaporation from open surfaces, "
        "which is one reason clearing forest changes local rainfall."
    ),

    (
        "As air rises it cools, and cooler air holds less vapour. Once it can hold no more, the "
        "surplus condenses onto small particles of dust or salt suspended in the atmosphere. The "
        "result is cloud, which is not vapour at all but many billions of liquid droplets small "
        "enough to stay aloft."
    ),

    (
        "Precipitation begins when droplets grow heavy enough that rising air can no longer hold "
        "them. Whether that arrives as rain, sleet, hail or snow depends on the temperature profile "
        "of the air the drop falls through, which can differ from the temperature at the ground."
    ),

    (
        "Water that reaches the ground either soaks in or runs off. Which one dominates depends on "
        "how saturated the soil already is, how steep the slope is, and whether the surface has been "
        "built on. A catchment that absorbed a storm comfortably one year can flood in the next after "
        "development upstream."
    ),

    (
        "Groundwater moves slowly, sometimes only metres in a year, through the pore spaces of rock "
        "and sediment. That slowness is why an aquifer can supply a town through a dry season, and "
        "also why one that is over-extracted takes decades to recover rather than months."
    ),

    (
        "The residence time of water differs enormously by reservoir. A molecule spends days in the "
        "atmosphere, weeks in a river, and thousands of years in the deep ocean or an ice sheet. Any "
        "statement about how quickly the cycle responds to change has to say which reservoir it means."
    ),

    (
        "Energy and water move together. Evaporation absorbs heat from the surface and condensation "
        "releases it higher in the atmosphere, which makes the cycle one of the main ways heat is "
        "carried from the equator toward the poles. The water cycle and the climate system are not "
        "separate topics."
    ),

    (
        "Human use has become large enough to appear in the accounting. Irrigation, reservoirs and "
        "groundwater extraction all redirect flows that would otherwise have followed the natural "
        "path, and at a river-basin scale the redirected fraction can exceed what falls as rain in a "
        "dry year."
    ),
)

#: Section headings, cycled alongside the paragraphs. Named for what a teacher would
#: call the section rather than numbered, because the sectioning logic prefers an
#: author's heading over a generated one and a numbered stub would not exercise that.
_HEADINGS: tuple[str, ...] = (
    "The Water Cycle in Outline",
    "Evaporation from Open Water",
    "Transpiration and Vegetation",
    "Condensation and Cloud Formation",
    "Forms of Precipitation",
    "Infiltration and Surface Runoff",
    "Groundwater and Aquifers",
    "Residence Time by Reservoir",
    "Energy Transport in the Cycle",
    "Human Influence on the Cycle",
    "Measuring Rainfall",
    "Catchments and Watersheds",
    "Rivers and Sediment Transport",
    "Lakes and Standing Water",
    "Snowpack and Seasonal Melt",
    "Glaciers as Long-Term Storage",
    "Soil Moisture and Crops",
    "Water Quality and Filtration",
    "Drought and Its Indicators",
    "Flood Risk and Return Periods",
)

_MARGIN = 56
_WIDTH, _HEIGHT = fitz.paper_size("a4")

#: A hard stop on the growth loop below. Far above any size worth benchmarking, and
#: present only so a layout bug cannot turn "grow until it fits" into a hang.
_MAX_SECTIONS = 500


@dataclass(frozen=True)
class Document:
    """A generated document, with the facts a benchmark row needs beside its timings."""

    name: str
    pages: int
    sections: int
    words: int
    content: bytes

    @property
    def kilobytes(self) -> float:
        return len(self.content) / 1024


def build_pdf(pages: int, *, title: str = "Water and the Earth's Systems") -> Document:
    """A structured teaching document of at least `pages` pages.

    Grown rather than estimated. A first version worked out how many sections should
    fit on a page and laid out that many — it was asked for sixty pages and produced
    thirty-two. Adding sections until the page count is reached, then reading the real
    counts back off the finished document, cannot be wrong in that way.
    """
    sections = 0
    doc, words = _lay_out(1, title)
    while doc.page_count < pages and sections < _MAX_SECTIONS:
        doc.close()
        sections += 1
        doc, words = _lay_out(sections + 1, title)
    sections = max(1, sections + 1)

    built = doc.page_count
    content = _stable_bytes(doc, title)
    doc.close()
    return Document(
        name=f"{built}-page", pages=built, sections=sections, words=words, content=content
    )


def _lay_out(sections: int, title: str) -> tuple[fitz.Document, int]:
    """Build a document of exactly `sections` sections; return it and its word count."""
    doc = fitz.open()
    page = doc.new_page(width=_WIDTH, height=_HEIGHT)
    y = _write(page, title, _MARGIN, size=18, bold=True) + 10
    words = 0

    for index in range(sections):
        heading = _HEADINGS[index % len(_HEADINGS)]
        if index >= len(_HEADINGS):
            # Past the pool, keep headings distinct rather than repeating verbatim —
            # a document with twenty identical headings is not one anybody has.
            heading = f"{heading} ({index // len(_HEADINGS) + 1})"

        block = [_PARAGRAPHS[(index + offset) % len(_PARAGRAPHS)] for offset in range(3)]
        needed = 30 + sum(_height_of(text) for text in block)
        if y + needed > _HEIGHT - _MARGIN:
            page = doc.new_page(width=_WIDTH, height=_HEIGHT)
            y = _MARGIN

        y = _write(page, heading, y, size=13, bold=True) + 4
        for text in block:
            y = _write(page, text, y, size=10.5) + 6
            words += len(text.split())
        y += 8
    return doc, words


def _stable_bytes(doc: fitz.Document, title: str) -> bytes:
    """Serialise the document so two runs produce the same bytes.

    A PDF carries a creation timestamp and a trailer identifier, both of which differ
    on every write. Fixed metadata removes the first; the second is rewritten here
    because PyMuPDF generates it from the file contents and the clock, and a benchmark
    corpus that changes identity between runs cannot be compared across them.
    """
    doc.set_metadata(
        {"producer": "lms-ai-engine benchmarks", "creator": "", "title": title,
         "author": "", "subject": "", "keywords": "",
         "creationDate": "D:19800101000000Z", "modDate": "D:19800101000000Z"}
    )
    raw = doc.tobytes(garbage=4, deflate=True)
    return re.sub(rb"/ID\s*\[\s*<[^>]*>\s*<[^>]*>\s*\]", b"/ID [<00><00>]", raw)


def _height_of(text: str, size: float = 10.5) -> float:
    """How tall a paragraph will be once wrapped, in points."""
    usable = _WIDTH - (2 * _MARGIN)
    chars_per_line = max(1, int(usable / (size * 0.5)))
    lines = max(1, -(-len(text) // chars_per_line))
    return lines * (size * 1.35)


def _write(page: fitz.Page, text: str, y: float, *, size: float, bold: bool = False) -> float:
    """Draw one wrapped block and return the y it ended at."""
    font = "helvetica-bold" if bold else "helvetica"
    box = fitz.Rect(_MARGIN, y, _WIDTH - _MARGIN, _HEIGHT - _MARGIN)
    page.insert_textbox(box, text, fontsize=size, fontname=font, align=0)
    return y + _height_of(text, size)


#: The three sizes worth reporting, chosen to span what a teacher actually uploads:
#: a handout, a unit, and a full chapter. The largest is deliberately well beyond
#: everyday use, so the published figures describe the engine at its heaviest rather
#: than at its most flattering.
SIZES: tuple[int, ...] = (2, 20, 60)


def corpus() -> list[Document]:
    """Every document a full benchmark run measures, smallest first."""
    return [build_pdf(pages) for pages in SIZES]


def as_upload(document: Document) -> io.BytesIO:
    """The document as a file object, the way a route would receive it."""
    return io.BytesIO(document.content)
