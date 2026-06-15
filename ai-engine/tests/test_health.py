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
