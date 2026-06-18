"""Tests for the PDF parser.

The fixture builds a small but representative PDF at runtime (title, headings,
paragraphs, a bullet list, an image, across two pages) so the test is real and
self-contained — no binary checked into the repo.
"""

import io

import pymupdf
import pytest
from PIL import Image

from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.schema import BlockKind


@pytest.fixture
def sample_pdf(tmp_path):
    doc = pymupdf.open()
    doc.set_metadata({"title": "Photosynthesis Basics", "author": "Test Author"})

    p1 = doc.new_page()
    p1.insert_text((72, 72), "Photosynthesis", fontsize=24)        # title -> heading L1
    p1.insert_text((72, 110), "Overview", fontsize=16)             # heading L2
    p1.insert_text((72, 140), "Plants convert light energy into chemical energy.", fontsize=11)
    p1.insert_text((72, 160), "This happens mainly in the leaves.", fontsize=11)
    p1.insert_textbox(pymupdf.Rect(72, 185, 400, 260), "- Sunlight\n- Water\n- Carbon dioxide", fontsize=11)
    png = io.BytesIO()
    Image.new("RGB", (80, 60), (120, 160, 200)).save(png, "PNG")
    p1.insert_image(pymupdf.Rect(72, 300, 152, 360), stream=png.getvalue())

    p2 = doc.new_page()
    p2.insert_text((72, 72), "Key terms", fontsize=16)
    p2.insert_text((72, 110), "Chlorophyll is the green pigment in chloroplasts.", fontsize=11)

    out = tmp_path / "sample.pdf"
    doc.save(out)
    doc.close()
    return str(out)


def test_metadata_and_page_count(sample_pdf):
    doc = parse_pdf(sample_pdf)
    assert doc.source.format == "pdf"
    assert doc.source.page_count == 2
    assert doc.source.title == "Photosynthesis Basics"
    assert doc.source.author == "Test Author"
    assert len(doc.pages) == 2
    assert all(page.kind == "page" for page in doc.pages)


def test_headings_detected(sample_pdf):
    doc = parse_pdf(sample_pdf)
    headings = [b for p in doc.pages for b in p.blocks if b.kind == BlockKind.heading]
    texts = {h.text for h in headings}
    assert "Photosynthesis" in texts
    assert "Overview" in texts
    # "Key terms" sits alone on a sparse page 2 — it is only correctly read as a
    # heading because body size is measured across the whole document, not per page.
    assert "Key terms" in texts
    # the title is larger than the section heading, so it should rank higher
    title = next(h for h in headings if h.text == "Photosynthesis")
    overview = next(h for h in headings if h.text == "Overview")
    assert title.level < overview.level


def test_list_and_paragraph(sample_pdf):
    doc = parse_pdf(sample_pdf)
    lists = [b for p in doc.pages for b in p.blocks if b.kind == BlockKind.list]
    assert lists, "expected a list block"
    assert "Sunlight" in lists[0].items
    paragraphs = [b for p in doc.pages for b in p.blocks if b.kind == BlockKind.paragraph]
    assert any("leaves" in (p.text or "") for p in paragraphs)


def test_image_captured(sample_pdf):
    doc = parse_pdf(sample_pdf)
    images = [b for p in doc.pages for b in p.blocks if b.kind == BlockKind.image]
    assert images, "expected an image block"
    assert images[0].image is not None
    assert images[0].image.id


def test_json_round_trip(sample_pdf):
    doc = parse_pdf(sample_pdf)
    payload = doc.model_dump_json()
    assert '"schema_version"' in payload
    assert '"format":"pdf"' in payload
