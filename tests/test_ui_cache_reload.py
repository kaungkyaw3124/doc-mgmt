"""UI (loop iteration 9, part A): after app.js changes on the server, a
NORMAL reload (not a hard refresh) runs the new app.js. Edits web/js/app.js
in place (the local stack bind-mounts web/) and always restores it.
Skipped when Playwright for Python isn't installed."""
import pathlib
from urllib.parse import urlsplit

import pytest

from conftest import BASE_URL

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
APP_JS = pathlib.Path(__file__).resolve().parents[1] / "web" / "js" / "app.js"


@pytest.mark.skipif(urlsplit(SITE).hostname not in ("localhost", "127.0.0.1"),
                    reason="edits web/js/app.js, which only the local stack bind-mounts")
def test_normal_reload_runs_the_new_app_js():
    original = APP_JS.read_bytes()
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()  # the browser's HTTP cache is on, as for a real user
        try:
            page.goto(SITE + "/")
            page.wait_for_selector("#login-form")
            assert page.evaluate("() => window.__cacheProbe") is None

            APP_JS.write_bytes(original + b"\nwindow.__cacheProbe = 2;\n")
            page.reload()  # a normal reload, not a hard refresh
            page.wait_for_selector("#login-form")
            assert page.evaluate("() => window.__cacheProbe") == 2
        finally:
            APP_JS.write_bytes(original)
            browser.close()
