"""Tests for the system probes. Hermetic — no external services required."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"]
    assert body["version"]


def test_root_lists_endpoints():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"]


def test_openapi_served():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"]


def test_ready_responds():
    # As a context manager the client runs the lifespan, which sets up the shared
    # HTTP client. Ollama isn't running under tests, so readiness reports it
    # unreachable (503) — the point is the probe answers cleanly with components.
    with TestClient(app) as ready_client:
        resp = ready_client.get("/ready")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "ready" in body
    assert "ollama" in body["components"]
