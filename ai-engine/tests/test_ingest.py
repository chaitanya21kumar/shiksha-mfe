"""Tests for the POST /ingest endpoint (the HTTP surface of Module A.1)."""

import pymupdf
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Ingest Sample", "author": "Test"})
    page = doc.new_page()
    page.insert_text((72, 72), "Title Here", fontsize=20)
    page.insert_text((72, 110), "A short paragraph of body text.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_ingest_pdf_returns_structured_json():
    resp = client.post(
        "/ingest",
        files={"file": ("sample.pdf", _sample_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["format"] == "pdf"
    assert body["source"]["filename"] == "sample.pdf"  # real name, not the temp file
    assert body["pages"]


def test_ingest_rejects_unsupported_type():
    resp = client.post("/ingest", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 415


def test_ingest_rejects_corrupt_file():
    # A supported extension but invalid bytes: the parser fails and we should
    # return a clean 400, not leak a 500.
    resp = client.post(
        "/ingest",
        files={"file": ("broken.pdf", b"this is not a real pdf", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "parse" in resp.json()["detail"].lower()
