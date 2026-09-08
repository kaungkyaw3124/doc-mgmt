"""
Security regression tests for Priority 6 — JWT Storage and Rotation.

TestClient (httpx-based) persists cookies across requests on the same
client instance, exactly like a browser — so these tests exercise the
real cookie-based refresh flow end to end against the real DB-backed
RefreshToken table, not mocks.
"""

from app import models


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password})


def test_login_returns_access_token_and_sets_refresh_cookie(client, make_user):
    make_user(username="alice", password="correct horse battery staple")
    resp = _login(client, "alice", "correct horse battery staple")
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "refresh_token" in resp.cookies
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header
    assert "SameSite=lax" in set_cookie_header or "SameSite=Lax" in set_cookie_header


def test_valid_authentication_can_access_protected_route(client, make_user):
    make_user(username="alice", password="correct horse battery staple", is_superuser=True)
    login_resp = _login(client, "alice", "correct horse battery staple")
    token = login_resp.json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200


def test_expired_token_is_rejected_by_verify(client, make_user):
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone
    from app.core.config import settings

    user = make_user(username="alice", password="correct horse battery staple")
    expired = pyjwt.encode(
        {"sub": str(user.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    resp = client.get("/verify", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_invalid_token_is_rejected(client):
    resp = client.get("/verify", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_refresh_rotates_token_and_issues_new_access_token(client, make_user, db):
    make_user(username="alice", password="correct horse battery staple")
    login_resp = _login(client, "alice", "correct horse battery staple")
    old_access_token = login_resp.json()["access_token"]
    assert db.query(models.RefreshToken).count() == 1

    refresh_resp = client.post("/refresh")
    assert refresh_resp.status_code == 200
    new_access_token = refresh_resp.json()["access_token"]
    assert new_access_token != old_access_token

    # Rotation: the old row is revoked, a new row exists — table still has
    # exactly 2 rows (old revoked + new active), not a growing pile and
    # not just mutated in place.
    assert db.query(models.RefreshToken).count() == 2
    revoked = db.query(models.RefreshToken).filter(models.RefreshToken.revoked_at.isnot(None)).count()
    assert revoked == 1


def test_replay_of_a_rotated_refresh_token_is_rejected(client, make_user):
    """
    The core anti-replay property: once a refresh token has been used
    (rotated away), presenting that SAME cookie value again must fail —
    proving refresh tokens are single-use, not reusable.
    """
    make_user(username="alice", password="correct horse battery staple")
    _login(client, "alice", "correct horse battery staple")

    original_cookie_value = client.cookies.get("refresh_token")
    assert original_cookie_value

    first_refresh = client.post("/refresh")
    assert first_refresh.status_code == 200

    # Replay: manually re-present the ORIGINAL (now-rotated-away) cookie.
    client.cookies.set("refresh_token", original_cookie_value)
    replay_resp = client.post("/refresh")
    assert replay_resp.status_code == 401


def test_replaying_a_rotated_token_revokes_the_users_other_sessions_too(client, make_user, db):
    """Replay is treated as a possible-theft signal: it revokes ALL of
    that user's outstanding refresh tokens, not just the replayed one."""
    user = make_user(username="alice", password="correct horse battery staple")
    _login(client, "alice", "correct horse battery staple")
    original_cookie_value = client.cookies.get("refresh_token")

    # This rotation creates a second, currently-valid token (simulating a
    # legitimate second session/tab that refreshed normally).
    client.post("/refresh")
    valid_cookie_value = client.cookies.get("refresh_token")

    # Now replay the original (already-rotated) token — should revoke
    # everything, including the otherwise-still-valid second session.
    client.cookies.set("refresh_token", original_cookie_value)
    replay_resp = client.post("/refresh")
    assert replay_resp.status_code == 401

    client.cookies.set("refresh_token", valid_cookie_value)
    should_also_fail = client.post("/refresh")
    assert should_also_fail.status_code == 401

    active_tokens = db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user.id, models.RefreshToken.revoked_at.is_(None)
    ).count()
    assert active_tokens == 0


def test_missing_refresh_cookie_is_rejected(client):
    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_garbage_refresh_cookie_is_rejected(client):
    client.cookies.set("refresh_token", "not-a-real-token-value")
    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_logout_invalidates_the_refresh_token(client, make_user):
    make_user(username="alice", password="correct horse battery staple")
    _login(client, "alice", "correct horse battery staple")

    logout_resp = client.post("/logout")
    assert logout_resp.status_code == 204

    # The cookie the browser had is now invalid server-side, even though
    # nothing forced the browser to forget it — this is what makes
    # logout a REAL server-side invalidation, not just "the client
    # forgot its token."
    refresh_resp = client.post("/refresh")
    assert refresh_resp.status_code == 401


def test_logout_without_a_session_still_succeeds(client):
    resp = client.post("/logout")
    assert resp.status_code == 204


def test_disabled_user_cannot_refresh(client, make_user, db):
    user = make_user(username="alice", password="correct horse battery staple")
    _login(client, "alice", "correct horse battery staple")

    user.is_active = False
    db.commit()

    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_disabling_a_user_revokes_their_refresh_tokens(client, make_user, db):
    """Exercises the same revoke_all_user_tokens() call admin.py's
    set_user_active endpoint makes — directly, since that endpoint lives
    behind admin auth this test suite doesn't set up a full admin
    session for."""
    from app.core.refresh_tokens import revoke_all_user_tokens

    user = make_user(username="alice", password="correct horse battery staple")
    _login(client, "alice", "correct horse battery staple")

    revoke_all_user_tokens(db, user.id)

    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_password_change_revokes_refresh_tokens_via_admin_edit_user(client, make_user, db):
    admin = make_user(username="admin-user", password="admin-password-xyz", is_superuser=True)
    target = make_user(username="bob", password="bobs-old-password")

    _login(client, "bob", "bobs-old-password")
    bob_refresh_cookie = client.cookies.get("refresh_token")
    assert bob_refresh_cookie

    admin_login = _login(client, "admin-user", "admin-password-xyz")
    admin_token = admin_login.json()["access_token"]

    edit_resp = client.patch(
        f"/admin/users/{target.id}",
        json={"password": "bobs-new-password"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert edit_resp.status_code == 200

    client.cookies.set("refresh_token", bob_refresh_cookie)
    resp = client.post("/refresh")
    assert resp.status_code == 401


def test_unrelated_username_edit_does_not_revoke_tokens(client, make_user, db):
    """A username-only edit (no password field) must NOT nuke the
    target's active session — only an actual password change should."""
    admin = make_user(username="admin-user2", password="admin-password-xyz", is_superuser=True)
    target = make_user(username="carol", password="carols-password")

    _login(client, "carol", "carols-password")
    carol_refresh_cookie = client.cookies.get("refresh_token")

    admin_login = _login(client, "admin-user2", "admin-password-xyz")
    admin_token = admin_login.json()["access_token"]

    edit_resp = client.patch(
        f"/admin/users/{target.id}",
        json={"username": "carol-renamed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert edit_resp.status_code == 200

    client.cookies.set("refresh_token", carol_refresh_cookie)
    resp = client.post("/refresh")
    assert resp.status_code == 200


def test_jwt_does_not_contain_username_or_password():
    import jwt as pyjwt
    from app.core.jwt_utils import create_access_token
    from app.core.config import settings

    token = create_access_token(subject="00000000-0000-0000-0000-000000000000")
    payload = pyjwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert set(payload.keys()) == {"sub", "exp"}
    assert payload["sub"] == "00000000-0000-0000-0000-000000000000"
