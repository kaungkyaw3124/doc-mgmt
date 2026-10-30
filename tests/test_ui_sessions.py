"""UI (loop iteration 10): idle warning and logout, "Stay logged in", tabs
sharing activity and logout, the hard session limit, and the session lists.

Needs a stack started with SHORT session values (test stack only), e.g. in
services/auth-service/.env:
    SESSION_IDLE_TIMEOUT_MINUTES=0.33334   (20 s)
    SESSION_IDLE_WARNING_SECONDS=10
    SESSION_MAX_HOURS=0.016667             (60 s)
Skipped otherwise (the normal 30-minute values would take far too long),
and when Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR to
save screenshots."""
import os
import re
import time

import httpx
import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
ACTIVE = re.compile(r"\bactive\b")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _session_values():
    r = httpx.post(BASE_URL + "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    httpx.post(BASE_URL + "/auth/logout", cookies=r.cookies)
    return r.json()["session"]


pytestmark = pytest.mark.skipif(
    _session_values()["idle_timeout_s"] > 60, reason="needs a stack started with short session values (see docstring)")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _login(page):
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    expect(page.locator("#app")).to_have_class(ACTIVE)


def _keep_active(page, seconds):
    end = time.time() + seconds
    while time.time() < end:
        page.mouse.move(100 + int(time.time()) % 50, 300)
        page.wait_for_timeout(2000)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_idle_warning_then_logout_with_the_inactivity_message(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _login(page)
    warning = page.locator("#session-warning-modal")
    expect(warning).to_have_class(ACTIVE, timeout=15000)  # idle 20 s - warning 10 s
    expect(page.locator("#session-warning-countdown")).not_to_have_text("")
    _shot(page, "iter-10-idle-warning.png")
    expect(page.locator("#login-error")).to_contain_text("Logged out after 20 seconds of inactivity", timeout=15000)
    expect(page.locator("#login-screen")).to_be_visible()
    _shot(page, "iter-10-logged-out.png")
    assert page.evaluate("() => document.getElementById('app').classList.contains('active')") is False


def test_stay_logged_in_keeps_the_session(browser):
    page = browser.new_page()
    _login(page)
    expect(page.locator("#session-warning-modal")).to_have_class(ACTIVE, timeout=15000)
    page.click("#session-warning-stay")
    expect(page.locator("#session-warning-modal")).not_to_have_class(ACTIVE)
    page.wait_for_timeout(9000)  # past the moment the original countdown would have logged out
    expect(page.locator("#app")).to_have_class(ACTIVE)
    assert page.locator("#login-screen").is_hidden()


def test_activity_in_one_tab_keeps_the_other_and_logout_reaches_both(browser):
    context = browser.new_context()  # one browser profile: shared cookie and localStorage
    tab_a, tab_b = context.new_page(), context.new_page()
    _login(tab_a)
    tab_b.goto(SITE + "/")  # signs in from the shared refresh cookie
    expect(tab_b.locator("#app")).to_have_class(ACTIVE)

    _keep_active(tab_a, 32)  # longer than the 20-s idle window; tab B gets no input at all
    expect(tab_b.locator("#app")).to_have_class(ACTIVE)
    expect(tab_a.locator("#app")).to_have_class(ACTIVE)

    tab_a.click("#logout-btn")
    expect(tab_b.locator("#login-screen")).to_be_visible(timeout=5000)
    context.close()


def test_the_hard_limit_ends_an_active_session(browser):
    page = browser.new_page()
    _login(page)
    _keep_active(page, 50)
    expect(page.locator("#app")).to_have_class(ACTIVE)  # still in, thanks to the activity
    expect(page.locator("#login-error")).to_contain_text("Your session expired, please log in again", timeout=20000)


def test_session_lists_for_the_user_and_the_admin(browser):
    other = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0")
    _login(other.new_page())  # a second device
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _login(page)

    page.click("#my-sessions-btn")
    rows = page.locator("#sessions-tbody tr")
    expect(rows.filter(has_text="this device")).to_have_count(1)
    expect(rows.filter(has_text="Firefox on Windows")).to_have_count(1)
    _shot(page, "iter-10-my-sessions.png")
    page.click("#sessions-modal-close")

    page.click("#admin-nav-item")
    page.locator(f"[data-user-sessions-name='{ADMIN_USERNAME}']").click()
    expect(page.locator("#sessions-modal-title")).to_have_text(f"Sessions — {ADMIN_USERNAME}")
    firefox = rows.filter(has_text="Firefox on Windows")
    expect(firefox.locator("[data-revoke-session-id]")).to_have_count(1)
    _shot(page, "iter-10-admin-sessions.png")
    firefox.locator("[data-revoke-session-id]").click()
    expect(rows.filter(has_text="Firefox on Windows")).to_have_count(0)
    other.close()
