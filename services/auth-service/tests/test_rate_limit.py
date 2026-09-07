"""
Security regression tests for Priority 1 — Login Rate Limiting.

Rate limit thresholds are overridden in conftest.py (small numbers) so
these tests don't need hundreds of requests to trip a limit:
    IP:       6 failed attempts / 15 min (any username)
    PAIR:     3 failed attempts / 15 min (same IP + username)
    USERNAME: 5 failed attempts / 15 min (same username, any IP)
"""


def _bad_login(client, username="alice", password="wrong", ip="1.1.1.1"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
        headers={"X-Real-IP": ip},
    )


def _good_login(client, username, password, ip="1.1.1.1"):
    return client.post(
        "/login",
        json={"username": username, "password": password},
        headers={"X-Real-IP": ip},
    )


def test_successful_login_works(client, make_user):
    make_user(username="alice", password="correct horse battery staple")
    resp = _good_login(client, "alice", "correct horse battery staple")
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_repeated_failed_attempts_get_throttled(client, make_user):
    make_user(username="alice", password="correct horse battery staple")

    # PAIR limit is 3 for (ip=1.1.1.1, username=alice)
    statuses = [_bad_login(client, "alice", "wrong", ip="1.1.1.1").status_code for _ in range(3)]
    assert statuses == [401, 401, 401]

    limited = _bad_login(client, "alice", "wrong", ip="1.1.1.1")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_rate_limit_does_not_reveal_account_existence(client, make_user):
    make_user(username="alice", password="correct horse battery staple")

    # Trip the pair limit for a REAL account and a FAKE account identically.
    for _ in range(3):
        real_resp = _bad_login(client, "alice", "wrong", ip="2.2.2.2")
    for _ in range(3):
        fake_resp = _bad_login(client, "definitely-not-a-user", "wrong", ip="3.3.3.3")

    real_limited = _bad_login(client, "alice", "wrong", ip="2.2.2.2")
    fake_limited = _bad_login(client, "definitely-not-a-user", "wrong", ip="3.3.3.3")

    assert real_limited.status_code == fake_limited.status_code == 429
    assert real_limited.json()["detail"] == fake_limited.json()["detail"]

    # Below the limit, both a real and fake account return the identical
    # generic 401 body too — no enumeration signal either way.
    assert real_resp.status_code == fake_resp.status_code == 401
    assert real_resp.json()["detail"] == fake_resp.json()["detail"]


def test_different_accounts_from_same_ip_are_independent(client, make_user):
    make_user(username="alice", password="alice-password")
    make_user(username="bob", password="bob-password")

    # Trip the pair limit for alice from this IP (3 attempts).
    for _ in range(3):
        _bad_login(client, "alice", "wrong", ip="4.4.4.4")
    alice_limited = _bad_login(client, "alice", "wrong", ip="4.4.4.4")
    assert alice_limited.status_code == 429

    # bob, same IP, is a different (ip, username) pair — still allowed,
    # until it consumes its own share of the IP-wide budget.
    bob_resp = _bad_login(client, "bob", "wrong", ip="4.4.4.4")
    assert bob_resp.status_code == 401

    # And bob can still log in successfully with the right password.
    good = _good_login(client, "bob", "bob-password", ip="4.4.4.4")
    assert good.status_code == 200


def test_same_account_from_different_ip_is_not_locked_out(client, make_user):
    """
    An attacker who deliberately fails login for a victim's username from
    ONE ip must not be able to lock the victim out when the victim logs in
    correctly from THEIR OWN (different) ip — that would be a trivial
    account-lockout DoS. The (ip, username) pair limit is what's meant to
    guard against this: exhausting attacker_ip+victim doesn't touch the
    victim_ip+victim counter.
    """
    make_user(username="victim", password="victim-password")

    for _ in range(3):
        _bad_login(client, "victim", "wrong", ip="9.9.9.9")
    attacker_limited = _bad_login(client, "victim", "wrong", ip="9.9.9.9")
    assert attacker_limited.status_code == 429

    victim_login = _good_login(client, "victim", "victim-password", ip="10.10.10.10")
    assert victim_login.status_code == 200


def test_ip_wide_limit_covers_credential_stuffing_across_usernames(client, make_user):
    make_user(username="user-a", password="pw-a")
    make_user(username="user-b", password="pw-b")
    make_user(username="user-c", password="pw-c")

    # IP limit is 6. Spread failed attempts across usernames from one IP
    # (each pair only gets 2, below the pair limit of 3) to isolate the
    # IP-wide limiter rather than the pair limiter.
    ip = "5.5.5.5"
    responses = []
    usernames = ["user-a", "user-b", "user-c"]
    for i in range(6):
        responses.append(_bad_login(client, usernames[i % 3], "wrong", ip=ip).status_code)
    assert responses == [401] * 6

    limited = _bad_login(client, "user-a", "wrong", ip=ip)
    assert limited.status_code == 429


def test_username_wide_limit_covers_distributed_brute_force(client, make_user):
    """
    An attacker rotating source IPs against a single account must still be
    bounded — this is the USERNAME-wide limiter (5 in the test config),
    independent of both the IP and (ip, username) limiters.
    """
    make_user(username="victim2", password="victim2-password")

    for i in range(5):
        resp = _bad_login(client, "victim2", "wrong", ip=f"20.20.20.{i}")
        assert resp.status_code == 401

    limited = _bad_login(client, "victim2", "wrong", ip="20.20.20.99")
    assert limited.status_code == 429


def test_legitimate_login_works_again_once_window_is_cleared(client, make_user, db):
    """
    Simulates the sliding window elapsing: attempts recorded further in
    the past than the window no longer count, so a legitimate user is
    never permanently locked out — only rate-limited within the window.
    """
    from datetime import datetime, timedelta
    from app import models

    make_user(username="alice", password="correct horse battery staple")

    for _ in range(3):
        _bad_login(client, "alice", "wrong", ip="6.6.6.6")
    assert _bad_login(client, "alice", "wrong", ip="6.6.6.6").status_code == 429

    # Age out every recorded attempt past the (15 minute) window.
    db.query(models.LoginAttempt).update({models.LoginAttempt.created_at: datetime.utcnow() - timedelta(minutes=20)})
    db.commit()

    resp = _good_login(client, "alice", "correct horse battery staple", ip="6.6.6.6")
    assert resp.status_code == 200


def test_successful_login_is_not_counted_against_the_limit(client, make_user):
    make_user(username="alice", password="correct horse battery staple")

    for _ in range(2):  # below the pair limit of 3
        assert _good_login(client, "alice", "correct horse battery staple", ip="7.7.7.7").status_code == 200

    resp = _bad_login(client, "alice", "wrong", ip="7.7.7.7")
    assert resp.status_code == 401
