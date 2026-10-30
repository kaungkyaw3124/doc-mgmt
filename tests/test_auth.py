import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, unique


def test_admin_login_ok(client):
    resp = client.post("/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


@pytest.fixture
def pending_user(client, admin):
    """A throwaway registered (pending-approval) account.

    The wrong-password test deliberately does NOT target the real admin:
    the login rate limiter blocks an (ip, username) pair after 5 failures
    in 15 minutes (auth-service app/core/config.py), and it checks that
    BEFORE the password — so a few quick re-runs of this suite would lock
    the admin out and turn every other test red with a 429."""
    username = unique("test-user")
    resp = client.post("/auth/register", json={"username": username, "password": unique("pw")})
    assert resp.status_code in (200, 201), f"register: {resp.status_code} {resp.text}"
    yield username
    # /deny hard-deletes an unapproved account — clean up after ourselves.
    pending = admin.get("/admin/users/pending")
    if pending.status_code == 200:
        for u in pending.json():
            if u["username"] == username:
                admin.post(f"/admin/users/{u['id']}/deny")


def test_wrong_password_rejected(client, pending_user):
    resp = client.post("/auth/login", json={"username": pending_user, "password": "definitely-wrong"})
    assert resp.status_code == 401, resp.text


def test_protected_route_without_token_rejected(client):
    resp = client.get("/documents")
    assert resp.status_code == 401, f"expected 401 without a token, got {resp.status_code} {resp.text[:200]}"


def test_protected_route_with_garbage_token_rejected(client):
    resp = client.get("/documents", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401, f"expected 401 for a bad token, got {resp.status_code} {resp.text[:200]}"


def test_sessions_and_logout_everywhere_through_the_gateway(admin):
    """Loop iteration 10: login starts a session (listed by /auth/sessions);
    /auth/logout-all ends it, and the still-unexpired access token is
    rejected at once (/verify checks the session), not 15 minutes later.

    Uses its own throwaway user: logout-all on the admin would also end the
    suite's shared admin session. Users can't be deleted (by design), so it
    is deactivated afterwards."""
    import httpx
    from conftest import BASE_URL

    username, password = unique("sess-user"), "a long throwaway password 123"
    created = admin.post("/auth/users", json={"username": username, "password": password})
    assert created.status_code == 201, created.text
    user_id = next(u["id"] for u in admin.get("/admin/users").json() if u["username"] == username)
    try:
        with httpx.Client(base_url=BASE_URL, timeout=30) as c:
            login = c.post("/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200, login.text
            assert set(login.json()["session"]) == {"idle_timeout_s", "warning_s", "expires_at"}
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            sessions = c.get("/auth/sessions", headers=headers)
            assert sessions.status_code == 200, sessions.text
            assert [s["current"] for s in sessions.json()] == [True]
            assert c.get("/documents", headers=headers).status_code == 403  # no documents role: but signed in

            assert c.post("/auth/logout-all", headers=headers).status_code == 204
            assert c.get("/documents", headers=headers).status_code == 401  # session ended
            assert c.post("/auth/refresh").status_code == 401
    finally:
        admin.patch(f"/admin/users/{user_id}/active", json={"is_active": False})
