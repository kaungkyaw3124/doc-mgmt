"""
Security regression tests for Priority 4 — Authenticate Gateway Trust
Headers (catalogue-service). No live Postgres/MinIO/Meilisearch needed:
the gateway_auth middleware runs BEFORE routing, so a rejected request
never reaches a router/DB, and a request that clears the middleware can
be aimed at a nonexistent path to prove it reached FastAPI's own
routing (a 404) rather than being caught by the guard.
"""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health_is_exempt_no_secret_needed():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_request_without_secret_is_rejected():
    resp = client.get("/products")
    assert resp.status_code == 401
    assert "gateway credential" in resp.json()["detail"]


def test_request_with_wrong_secret_is_rejected():
    resp = client.get("/products", headers={"X-Internal-Secret": "definitely-not-the-secret"})
    assert resp.status_code == 401


def test_spoofed_trust_headers_without_secret_cannot_escalate():
    """
    A malicious client sending X-Allowed-Projects/X-Has-Category-Access
    directly, without going through Nginx, must be rejected before those
    headers are ever read by a route handler.
    """
    resp = client.get(
        "/products",
        headers={"X-Allowed-Projects": "ALL", "X-Has-Category-Access": "true"},
    )
    assert resp.status_code == 401


def test_tampered_secret_on_an_otherwise_legitimate_request_is_rejected():
    resp = client.get(
        "/products",
        headers={
            "X-Internal-Secret": settings.internal_shared_secret + "-tampered",
            "X-Allowed-Projects": "ALL",
        },
    )
    assert resp.status_code == 401


def test_request_with_correct_secret_passes_the_gateway_check():
    resp = client.get(
        "/__definitely_not_a_real_route__",
        headers={"X-Internal-Secret": settings.internal_shared_secret},
    )
    # 404 (FastAPI's own "no matching route"), not the gateway's 401 —
    # proves the middleware let it through to routing.
    assert resp.status_code == 404
    assert resp.json().get("detail") != "missing or invalid gateway credential"
