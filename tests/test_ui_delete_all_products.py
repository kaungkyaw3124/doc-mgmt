"""UI smoke (loop iteration 15, DEV-ONLY — delete this file with the
feature): one click on "Delete ALL products (dev)" wipes the catalogue with
no confirm dialog and reports the count; the button is hidden when the
server says the environment is production.

WARNING: this permanently deletes every product in the stack it runs
against — that is the feature. Skipped when Playwright for Python isn't
installed. Set UI_SCREENSHOT_DIR to also save screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _open_products(page):
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.click(".nav-item[data-view=products]")
    page.wait_for_selector("#toggle-product-form", state="visible")


def test_one_click_deletes_all_products(admin):
    # start from an empty catalogue so the banner count is exactly 3
    assert admin.delete("/products").status_code == 200
    for i in range(3):
        assert admin.post("/products", json={"name": unique(f"Wipe UI {i}")}).status_code == 201

    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        try:
            _open_products(page)
            btn = page.locator("#delete-all-products-btn")
            expect(btn).to_be_visible()
            expect(page.locator("#products-tbody tr")).to_have_count(3)
            _shot(page, "iter-15-products-before.png")

            btn.click()
            expect(page.locator("#products-banner")).to_contain_text("Deleted 3 products")
            expect(page.locator("#products-tbody")).to_contain_text("No products match.")
            _shot(page, "iter-15-products-after.png")
            assert dialogs == [], f"a dialog fired: {dialogs}"
        finally:
            browser.close()
    assert admin.get("/products").json() == []
    assert admin.get("/products/trash").json() == []


def test_button_hidden_in_production():
    """The test stack can't run with ENVIRONMENT=production (secrets_check
    refuses its placeholder secrets), so the server's answer is stubbed —
    the server-side 403 is covered by the catalogue unit tests."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/api/products/dev-config", lambda route: route.fulfill(json={"delete_all_enabled": False}))
        try:
            _open_products(page)
            page.wait_for_load_state("networkidle")
            sync_api.expect(page.locator("#delete-all-products-btn")).to_be_hidden()
        finally:
            browser.close()
