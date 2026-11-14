"""UI smoke (loop iteration 15, DEV-ONLY — delete this file with the
feature): one click on each "Delete ALL … (dev)" button wipes that list with
no confirm dialog and reports the count; the buttons are hidden when the
server says the environment is production.

WARNING: this permanently deletes every product, document, customer, project
and company in the stack it runs against — that is the feature. Skipped when
Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR to also save
screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")

# (nav view, API path, toggle button, tbody, banner, empty-state text, create body)
PAGES = {
    "documents": ("documents", "/documents", "toggle-doc-form", "documents-tbody", "documents-banner", "No documents match.",
                  lambda: {"doc_type": "quotation", "currency": "USD", "items": []}),
    "products": ("products", "/products", "toggle-product-form", "products-tbody", "products-banner", "No products match.",
                 lambda: {"name": unique("Wipe UI")}),
    "customers": ("customers", "/customers", "toggle-customer-form", "customers-tbody", "customers-banner", "No customers match.",
                  lambda: {"name": unique("Wipe UI")}),
    "projects": ("projects", "/projects", "toggle-project-form", "projects-tbody", "projects-banner", "No projects match.",
                 lambda: {"name": unique("Wipe UI")}),
    "companies": ("company", "/companies", "toggle-company-form", "companies-tbody", "company-banner", "No company matches.",
                  lambda: {"name": unique("Wipe UI")}),
}


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _open(page, view, toggle):
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.click(f".nav-item[data-view={view}]")
    page.wait_for_selector(f"#{toggle}", state="visible")


# documents first: wiping them is not required by the others, but keeps the run tidy
@pytest.mark.parametrize("name", ["documents", "products", "customers", "projects", "companies"])
def test_one_click_deletes_all(admin, name):
    view, path, toggle, tbody, banner, empty, body = PAGES[name]
    # start empty so the banner count is exactly 3
    assert admin.delete(path).status_code == 200
    for _ in range(3):
        r = admin.post(path, json=body())
        assert r.status_code == 201, r.text

    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
        try:
            _open(page, view, toggle)
            btn = page.locator(f"#delete-all-{name}-btn")
            expect(btn).to_be_visible()
            expect(page.locator(f"#{tbody} tr")).to_have_count(3)
            _shot(page, f"iter-15-{name}-before.png")

            btn.click()
            expect(page.locator(f"#{banner}")).to_contain_text(f"Deleted 3 {name}")
            expect(page.locator(f"#{tbody}")).to_contain_text(empty)
            _shot(page, f"iter-15-{name}-after.png")
            assert dialogs == [], f"a dialog fired: {dialogs}"
        finally:
            browser.close()
    assert admin.get(path).json() == []
    assert admin.get(f"{path}/trash").json() == []


def test_buttons_hidden_in_production():
    """The test stack can't run with ENVIRONMENT=production (secrets_check
    refuses its placeholder secrets), so the server's answer is stubbed —
    the server-side 403s are covered by the unit tests of both services."""
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/api/products/dev-config", lambda route: route.fulfill(json={"delete_all_enabled": False}))
        try:
            _open(page, "products", "toggle-product-form")
            page.wait_for_load_state("networkidle")
            for name in PAGES:
                sync_api.expect(page.locator(f"#delete-all-{name}-btn")).to_be_hidden()
        finally:
            browser.close()
