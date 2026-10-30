"""
Loop iteration 10 — sessions end. A login starts a session; its refresh
token expires at min(now + idle timeout, login + max hours), /refresh
rotates within the session, access tokens carry `sid` and die with the
session (/verify and get_current_user check it), and users/admins can list
and end sessions.

Time is moved with the `clock` fixture (it shifts "now" for the session
code) instead of sleeping. Each TestClient is a separate browser/device:
it keeps its own refresh cookie, exactly like a real one.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app import models
from app.core import refresh_tokens
from app.core.config import settings
from app.main import app
from app.routers import auth as auth_router

PASSWORD = "correct horse battery staple"


@pytest.fixture
def clock(monkeypatch):
    """clock.advance(minutes=...) moves "now" forward for the session code."""
    class FakeDateTime(datetime):
        offset = timedelta(0)

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    class Clock:
        @staticmethod
        def advance(**delta):
            FakeDateTime.offset += timedelta(**delta)

    monkeypatch.setattr(refresh_tokens, "datetime", FakeDateTime)
    monkeypatch.setattr(auth_router, "datetime", FakeDateTime)
    return Clock()


def _login(client, username="alice", password=PASSWORD):
    resp = client.post("/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _claims(token):
    return pyjwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def _max_age(resp):
    cookie = resp.headers["set-cookie"]
    return int(cookie.split("Max-Age=", 1)[1].split(";", 1)[0])


# 1, 2 — idle timeout and hard limit

def test_refresh_within_the_idle_window_works_and_after_it_fails(client, make_user, clock):
    make_user()
    _login(client)
    clock.advance(minutes=29)
    assert client.post("/refresh").status_code == 200
    clock.advance(minutes=29)  # 29 min since the last refresh: still inside the window
    assert client.post("/refresh").status_code == 200
    clock.advance(minutes=31)  # idle for longer than 30 min
    resp = client.post("/refresh")
    assert resp.status_code == 401
    assert 'refresh_token=""' in resp.headers["set-cookie"]  # cookie cleared


def test_activity_does_not_extend_the_session_past_the_max(client, make_user, clock):
    make_user()
    _login(client)
    for _ in range(29):  # active every 20 min, up to 9 h 40 min after login
        clock.advance(minutes=20)
        assert client.post("/refresh").status_code == 200
    clock.advance(minutes=20)  # exactly 10 h after login
    assert client.post("/refresh").status_code == 401


# 3, 4 — rotation, cookie lifetime, access token claims

def test_rotation_keeps_the_session_and_cookie_max_age_matches_the_expiry(client, make_user, db, clock):
    make_user()
    login = client.post("/login", json={"username": "alice", "password": PASSWORD})
    assert _max_age(login) == pytest.approx(30 * 60, abs=2)
    first = db.query(models.RefreshToken).one()
    clock.advance(minutes=10)
    refreshed = client.post("/refresh")
    assert _max_age(refreshed) == pytest.approx(30 * 60, abs=2)

    db.expire_all()
    rows = db.query(models.RefreshToken).order_by(models.RefreshToken.created_at).all()
    assert len(rows) == 2
    assert {r.session_id for r in rows} == {first.session_id}
    assert {r.session_started_at for r in rows} == {first.session_started_at}
    assert rows[0].revoked_at is not None and rows[1].revoked_at is None

    # near the hard end the cookie (and token) only lives until the end
    for _ in range(28):
        clock.advance(minutes=20)
        last = client.post("/refresh")
        assert last.status_code == 200
    # 9 h 30 min after login: idle window would say 30 min, the session end says 30 min too
    clock.advance(minutes=15)
    near_end = client.post("/refresh")  # 9 h 45 min: only 15 min left
    assert near_end.status_code == 200
    assert _max_age(near_end) == pytest.approx(15 * 60, abs=2)
    assert near_end.json()["session"]["expires_at"].startswith(
        (first.session_started_at.astimezone(timezone.utc) + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M"))


def test_access_token_has_sid_and_never_outlives_the_session(client, make_user, db, monkeypatch):
    monkeypatch.setattr(settings, "session_max_hours", 0.1)  # 6 min, shorter than the 15-min access token
    make_user()
    body = _login(client)
    claims = _claims(body["access_token"])
    row = db.query(models.RefreshToken).one()
    assert claims["sid"] == str(row.session_id)
    end = refresh_tokens.session_end(row)
    assert claims["exp"] <= int(end.timestamp()) + 1
    assert body["session"]["idle_timeout_s"] == 30 * 60 and body["session"]["warning_s"] == 60


# 5 — revoked sessions are rejected immediately

def _verify(client, token):
    """/verify without X-Service: checks token, user and session only (no per-service grant needed)."""
    return client.get("/verify", headers=_auth(token)).status_code


def test_verify_rejects_a_token_after_logout_all(client, make_user):
    make_user(is_superuser=True)
    token = _login(client)["access_token"]
    assert _verify(client, token) == 200
    assert client.post("/logout-all", headers=_auth(token)).status_code == 204
    assert _verify(client, token) == 401
    assert client.get("/sessions", headers=_auth(token)).status_code == 401  # get_current_user too


def test_verify_rejects_a_token_after_an_admin_revokes_its_session(make_user, db):
    make_user(username="root", is_superuser=True)
    target = make_user(username="bob")
    admin_c, bob_c = TestClient(app), TestClient(app)
    admin_token = _login(admin_c, "root")["access_token"]
    bob_token = _login(bob_c, "bob")["access_token"]
    sid = _claims(bob_token)["sid"]
    assert _verify(bob_c, bob_token) == 200

    r = admin_c.post(f"/admin/users/{target.id}/sessions/{sid}/revoke", headers=_auth(admin_token))
    assert r.status_code == 204
    assert _verify(bob_c, bob_token) == 401
    assert bob_c.post("/refresh").status_code == 401
    assert admin_c.post(f"/admin/users/{target.id}/sessions/{sid}/revoke", headers=_auth(admin_token)).status_code == 404

    bob_token = _login(bob_c, "bob")["access_token"]
    assert admin_c.post(f"/admin/users/{target.id}/logout-all", headers=_auth(admin_token)).status_code == 204
    assert _verify(bob_c, bob_token) == 401
    assert _verify(admin_c, admin_token) == 200  # the admin's own session is untouched


# 6 — tokens from before sessions existed

def test_pre_session_refresh_tokens_are_rejected(client, make_user, db):
    user = make_user()
    plaintext = "legacy-token-from-before-sessions"
    db.add(models.RefreshToken(user_id=user.id, token_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
                               expires_at=datetime.now(timezone.utc) + timedelta(days=13)))  # 14-day style, no session
    db.commit()
    client.cookies.set("refresh_token", plaintext)
    assert client.post("/refresh").status_code == 401


# 7 — own sessions, logout everywhere

def test_sessions_lists_only_own_sessions_and_logout_all_ends_them(make_user):
    make_user()
    make_user(username="carol")
    laptop, phone, other = TestClient(app), TestClient(app), TestClient(app)
    laptop_token = _login(laptop)["access_token"]
    phone_token = _login(phone)["access_token"]
    _login(other, "carol")

    sessions = laptop.get("/sessions", headers=_auth(laptop_token)).json()
    assert len(sessions) == 2
    assert [s["current"] for s in sessions if s["id"] == _claims(laptop_token)["sid"]] == [True]
    assert sum(s["current"] for s in sessions) == 1
    assert {"started_at", "last_activity_at", "user_agent", "ip"} <= set(sessions[0])

    assert laptop.post("/logout-all", headers=_auth(laptop_token)).status_code == 204
    assert _verify(phone, phone_token) == 401
    assert phone.post("/refresh").status_code == 401
    assert other.post("/refresh").status_code == 200  # another user's session is unaffected


# 8 — admin scoping

def test_group_admin_can_only_manage_sessions_in_their_own_groups(make_user, db):
    group, elsewhere = models.Group(name="Ops"), models.Group(name="Finance")
    db.add_all([group, elsewhere])
    db.commit()
    gadmin, member, outsider = make_user(username="gadmin"), make_user(username="member"), make_user(username="outsider")
    root = make_user(username="root", is_superuser=True)
    db.add_all([models.UserGroup(user_id=gadmin.id, group_id=group.id, is_group_admin=True),
                models.UserGroup(user_id=member.id, group_id=group.id),
                models.UserGroup(user_id=outsider.id, group_id=elsewhere.id)])
    db.commit()
    for name in ("member", "outsider", "root"):
        _login(TestClient(app), name)
    c = TestClient(app)
    token = _login(c, "gadmin")["access_token"]

    assert c.get(f"/admin/users/{member.id}/sessions", headers=_auth(token)).status_code == 200
    for target in (outsider, root):
        assert c.get(f"/admin/users/{target.id}/sessions", headers=_auth(token)).status_code == 403
        assert c.post(f"/admin/users/{target.id}/logout-all", headers=_auth(token)).status_code == 403
    outsider_sid = db.query(models.RefreshToken).filter_by(user_id=outsider.id).one().session_id
    assert c.post(f"/admin/users/{outsider.id}/sessions/{outsider_sid}/revoke", headers=_auth(token)).status_code == 403
    assert db.query(models.RefreshToken).filter_by(user_id=outsider.id).one().revoked_at is None

    plain = TestClient(app)
    plain_token = _login(plain, "member")["access_token"]
    assert plain.get(f"/admin/users/{outsider.id}/sessions", headers=_auth(plain_token)).status_code == 403


# 9 — the existing revocations now also stop already-issued access tokens

def test_password_change_and_deactivation_end_sessions(make_user):
    make_user(username="root", is_superuser=True)
    bob = make_user(username="bob")
    admin_c, bob_c = TestClient(app), TestClient(app)
    admin_token = _login(admin_c, "root")["access_token"]

    bob_token = _login(bob_c, "bob")["access_token"]
    admin_c.patch(f"/admin/users/{bob.id}", json={"password": "a new long password 123"}, headers=_auth(admin_token))
    assert _verify(bob_c, bob_token) == 401
    assert bob_c.post("/refresh").status_code == 401

    bob_token = _login(bob_c, "bob", "a new long password 123")["access_token"]
    admin_c.patch(f"/admin/users/{bob.id}/active", json={"is_active": False}, headers=_auth(admin_token))
    assert _verify(bob_c, bob_token) == 403  # "account disabled", as before
    assert bob_c.post("/refresh").status_code == 401


# 10 — the startup column migration

def test_ensure_session_columns_runs_twice_on_an_old_table(db, make_user):
    from app.core.db import engine

    user = make_user()
    with engine.begin() as conn:  # make the table look like it did before sessions
        conn.execute(text("DROP INDEX IF EXISTS ix_refresh_tokens_session_id"))
        for column in ("session_id", "session_started_at", "last_activity_at", "user_agent", "ip"):
            conn.execute(text(f"ALTER TABLE refresh_tokens DROP COLUMN {column}"))
        conn.execute(text("INSERT INTO refresh_tokens (id, user_id, token_hash, created_at, expires_at) "
                          "VALUES (:id, :uid, 'old-hash', now(), now() + interval '13 days')"),
                     {"id": uuid.uuid4(), "uid": user.id})

    refresh_tokens.ensure_session_columns(engine)
    refresh_tokens.ensure_session_columns(engine)  # second run: no-op, no error

    cols = {c["name"] for c in inspect(engine).get_columns("refresh_tokens")}
    assert {"session_id", "session_started_at", "last_activity_at", "user_agent", "ip"} <= cols
    assert "ix_refresh_tokens_session_id" in {i["name"] for i in inspect(engine).get_indexes("refresh_tokens")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT token_hash, session_id FROM refresh_tokens")).all() == [("old-hash", None)]
