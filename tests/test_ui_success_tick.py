"""UI smoke (loop iteration 15 part C): saves show an animated tick, failures a
cross; logo uploads tick only once the saved image loads back; reduced motion
gets a static tick; no sound code anywhere. Skipped when Playwright for Python
isn't installed. Set UI_SCREENSHOT_DIR to also save screenshots."""
import base64
import os
import pathlib
import time

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")
WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
# 1x1 transparent PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _open(p, view, **ctx):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800}, **ctx)
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.click(f".nav-item[data-view={view}]")
    return browser, page


def _create_product(page, name):
    page.click("#toggle-product-form")
    page.fill("#product-name", name)
    page.click("#product-form-submit-btn")


def _anim(page, sel):
    return page.evaluate("s => getComputedStyle(document.querySelector(s)).animationName", sel)


def test_product_save_shows_tick_then_cleans_up(admin):
    name = unique("Tick")
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, "products")
        try:
            _create_product(page, name)
            tick = page.locator(".tick-toast.tick-ok")
            expect(tick).to_be_visible()
            assert "tick-draw" in _anim(page, ".tick-toast.tick-ok circle")
            assert "Product saved" in tick.inner_text()
            assert page.locator(".tick-toast.tick-err").count() == 0
            if SHOTS:
                page.wait_for_timeout(250)
                page.screenshot(path=os.path.join(SHOTS, "iter-15-tick-mid.png"), clip={"x": 980, "y": 0, "width": 300, "height": 90})
                page.wait_for_timeout(600)
                page.screenshot(path=os.path.join(SHOTS, "iter-15-tick-finished.png"), clip={"x": 980, "y": 0, "width": 300, "height": 90})
            start = time.monotonic()
            expect(page.locator(".tick-toast")).to_have_count(0, timeout=3000)
            assert time.monotonic() - start < 2.5
        finally:
            browser.close()
    for prod in admin.get("/products", params={"q": name}).json():
        admin.delete(f"/products/{prod['id']}")


def test_failed_save_shows_cross_not_tick():
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, "products")
        try:
            page.route("**/api/products", lambda route: route.fulfill(
                status=400, content_type="application/json", body='{"detail": "Forced failure"}')
                if route.request.method == "POST" else route.continue_())
            _create_product(page, unique("Tick fail"))
            cross = page.locator(".tick-toast.tick-err")
            expect(cross).to_be_visible()
            assert "Forced failure" in cross.inner_text()
            assert "tick-shake" in _anim(page, ".tick-toast.tick-err")
            assert page.locator(".tick-toast.tick-ok").count() == 0
            if SHOTS:
                page.wait_for_timeout(700)
                page.screenshot(path=os.path.join(SHOTS, "iter-15-tick-error.png"), clip={"x": 980, "y": 0, "width": 300, "height": 90})
        finally:
            browser.close()


@pytest.mark.parametrize("image_loads", [True, False])
def test_logo_upload_ticks_only_after_image_loads(admin, tmp_path, image_loads):
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG)
    name = unique("Tick Co")
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, "company")
        image_requests = []
        try:
            def on_image(route):  # the presigned logo URL (S3 signature in the query string)
                image_requests.append(route.request.url)
                route.continue_() if image_loads else route.abort()
            page.route(lambda url: "X-Amz-" in url, on_image)
            page.click("#toggle-company-form")
            page.fill("#company-name", name)
            page.set_input_files("#company-logo", str(logo))
            page.click("#company-form-submit-btn")
            if image_loads:
                expect(page.locator(".tick-toast.tick-ok")).to_be_visible()
                assert page.locator(".tick-toast.tick-err").count() == 0
            else:
                expect(page.locator(".tick-toast.tick-err")).to_contain_text("could not be loaded back")
                assert page.locator(".tick-toast.tick-ok").count() == 0
            assert image_requests, "the saved logo was never loaded back"
        finally:
            browser.close()
    for c in admin.get("/companies").json():
        if c["name"] == name:
            admin.delete(f"/companies/{c['id']}")


def test_reduced_motion_tick_is_static(admin):
    name = unique("Tick still")
    with sync_api.sync_playwright() as p:
        browser, page = _open(p, "products", reduced_motion="reduce")
        try:
            _create_product(page, name)
            expect(page.locator(".tick-toast.tick-ok")).to_be_visible()
            assert _anim(page, ".tick-toast.tick-ok") == "none"
            assert _anim(page, ".tick-toast.tick-ok circle") == "none"
            # still removed afterwards, even with no animationend
            expect(page.locator(".tick-toast")).to_have_count(0, timeout=3000)
        finally:
            browser.close()
    for prod in admin.get("/products", params={"q": name}).json():
        admin.delete(f"/products/{prod['id']}")


def test_no_sound_code():
    for f in WEB.rglob("*"):
        if f.suffix in {".html", ".js"}:
            text = f.read_text(encoding="utf-8")
            for word in ("<audio", "AudioContext", "new Audio", "navigator.vibrate"):
                assert word not in text, f"{word} in {f}"
