"""
Shared fixtures for the black-box smoke suite.

Every test talks to the real stack through the Nginx gateway only (never a
service's own port), so the auth_request/RBAC layer is exercised on every
call exactly as the browser would hit it. Run via ./scripts/verify.sh, or
directly with `pytest tests/ -v` against an already-running stack.

Env:
  BASE_URL        default http://localhost:8080/api
  ADMIN_USERNAME  default admin      (SEED_ADMIN_USERNAME in auth-service/.env.example)
  ADMIN_PASSWORD  default CHANGE_ME  (SEED_ADMIN_PASSWORD placeholder — test-only)
"""
import os
import time
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080/api").rstrip("/")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "CHANGE_ME")


def unique(prefix: str) -> str:
    """Unique-per-run name, so the suite can be re-run against the same
    database without colliding with rows a previous run left behind."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def poll(fn, timeout: float = 10.0, interval: float = 0.5):
    """Call fn() until it returns something truthy or timeout elapses;
    returns the last result either way. Meilisearch indexing is async, so
    "just created" is not the same as "searchable yet"."""
    deadline = time.monotonic() + timeout
    while True:
        result = fn()
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(interval)


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="session")
def admin_token(client):
    # Logged in once per session on purpose: the login rate limiter
    # (auth-service app/core/rate_limit.py) only counts failures, but there
    # is no reason to put more login traffic through it than needed.
    resp = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, f"admin login failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def admin(admin_token):
    """An httpx client pre-authorized as the seeded admin."""
    with httpx.Client(base_url=BASE_URL, timeout=60.0, headers=auth_headers(admin_token)) as c:
        yield c


@pytest.fixture
def customer(admin):
    resp = admin.post("/customers", json={"name": unique("test-customer")})
    assert resp.status_code == 201, f"create customer: {resp.status_code} {resp.text}"
    body = resp.json()
    yield body
    # Best-effort cleanup — a document still referencing it makes this a
    # 400 by design, so documents fixtures must be torn down first (pytest
    # does that: they depend on this fixture, so finalize before it).
    admin.delete(f"/customers/{body['id']}")


def create_quotation(admin, customer_id: str, description: str) -> dict:
    resp = admin.post(
        "/documents",
        json={
            "doc_type": "quotation",
            "customer_id": customer_id,
            "currency": "USD",
            "items": [{"description": description, "quantity": "2", "unit_price": "10.00"}],
        },
    )
    assert resp.status_code == 201, f"create quotation: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.fixture
def quotation(admin, customer):
    doc = create_quotation(admin, customer["id"], unique("test-item"))
    yield doc
    admin.delete(f"/documents/{doc['id']}")


@pytest.fixture
def category(admin):
    # short_term is unique and max 20 chars (catalogue-service schemas.py).
    # Categories have no DELETE endpoint, so they're left behind — the
    # unique name/short_term keeps re-runs from colliding with them.
    payload = {"name": unique("test-category"), "short_term": "T" + uuid.uuid4().hex[:8].upper()}
    resp = admin.post("/categories", json=payload)
    assert resp.status_code in (200, 201), f"create category: {resp.status_code} {resp.text}"
    return resp.json()


@pytest.fixture
def product(admin, category):
    resp = admin.post(
        "/products",
        json={"name": unique("test-product"), "category": category["name"], "unit_price": "12.50"},
    )
    assert resp.status_code == 201, f"create product: {resp.status_code} {resp.text}"
    body = resp.json()
    yield body
    admin.delete(f"/products/{body['id']}")


@pytest.fixture
def editor_user(admin):
    """A plain Operation-group Editor (not a superuser, not a group admin):
    {"username", "password", "id"}. Users can't be deleted (by design), so
    it is deactivated afterwards."""
    groups = admin.get("/admin/groups")
    assert groups.status_code == 200, groups.text
    operation = next(g for g in groups.json() if g["name"] == "Operation")
    roles = admin.get(f"/admin/groups/{operation['id']}/roles").json()
    editor_role = next(r for r in roles if r["name"] == "Editor")
    user = {"username": unique("ui-editor"), "password": "a long throwaway password 123"}
    resp = admin.post(f"/admin/groups/{operation['id']}/users",
                      json={**user, "role_id": editor_role["id"]})
    assert resp.status_code == 201, f"create editor: {resp.status_code} {resp.text}"
    user["id"] = next(u["id"] for u in admin.get("/admin/users").json() if u["username"] == user["username"])
    yield user
    admin.patch(f"/admin/users/{user['id']}/active", json={"is_active": False})
