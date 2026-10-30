"""UI (loop iteration 11): logging out wipes ALL of the previous user's UI
state, so the next user to log in on the same page sees only their own
menu and data. Every logout path reloads the page; the logout reason
survives the reload via sessionStorage. Tests 2–3 need the short session
values from iteration 10 (see tests/test_ui_sessions.py); the rest run on
any stack. Skipped when Playwright for Python isn't installed. Set
UI_SCREENSHOT_DIR to save the proof screenshot."""
import os
import re

import httpx
import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
ACTIVE = re.compile(r"\bactive\b")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _idle_timeout_s():
    r = httpx.post(BASE_URL + "/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    httpx.post(BASE_URL + "/auth/logout", cookies=r.cookies)
    return r.json()["session"]["idle_timeout_s"]


needs_short_sessions = pytest.mark.skipif(
    _idle_timeout_s() > 60, reason="needs a stack started with short session values (see tests/test_ui_sessions.py)")


def _login(page, username, password):
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-form button[type=submit]")
    expect(page.locator("#app")).to_have_class(ACTIVE)


def _assert_clean_editor_screen(page):
    """What a plain Editor must see right after logging in: no admin menu,
    no admin page, the Documents view, and none of the admin's user list."""
    expect(page.locator("#admin-nav-item")).to_be_hidden()
    expect(page.locator("#view-adminpanel")).to_be_hidden()
    expect(page.locator("#view-documents")).to_be_visible()
    expect(page.locator("#all-users-tbody")).not_to_contain_text(ADMIN_USERNAME)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _admin_on_admin_page(page):
    page.goto(SITE + "/")
    _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
    page.click("#admin-nav-item")
    expect(page.locator("#all-users-tbody")).to_contain_text(ADMIN_USERNAME)  # the admin's user list is on screen


def test_sign_out_then_editor_sees_no_admin_state(browser, editor_user):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    _admin_on_admin_page(page)
    page.click("#logout-btn")
    expect(page.locator("#login-screen")).to_be_visible()
    _login(page, editor_user["username"], editor_user["password"])  # same tab, no manual reload
    _assert_clean_editor_screen(page)
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, "iter-11-after-switch.png"))


def test_logout_in_one_tab_reloads_the_other(browser):
    context = browser.new_context()
    tab_a, tab_b = context.new_page(), context.new_page()
    tab_a.goto(SITE + "/")
    _login(tab_a, ADMIN_USERNAME, ADMIN_PASSWORD)
    tab_b.goto(SITE + "/")
    expect(tab_b.locator("#app")).to_have_class(ACTIVE)
    tab_b.evaluate("() => { window.__beforeLogout = true; }")

    tab_a.click("#logout-btn")
    expect(tab_b.locator("#login-screen")).to_be_visible(timeout=5000)
    expect(tab_b.locator("#app")).not_to_have_class(ACTIVE)
    assert tab_b.evaluate("() => window.__beforeLogout") is None  # tab B really reloaded
    context.close()


@needs_short_sessions
def test_idle_logout_then_editor_sees_no_admin_state_and_the_message_survives(browser, editor_user):
    page = browser.new_page()
    _admin_on_admin_page(page)
    expect(page.locator("#login-error")).to_contain_text("Logged out after", timeout=30000)  # idle logout + reload
    expect(page.locator("#login-screen")).to_be_visible()
    _login(page, editor_user["username"], editor_user["password"])
    _assert_clean_editor_screen(page)
