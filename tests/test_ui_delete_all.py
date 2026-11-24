"""UI smoke (loop iteration 15, DEV-ONLY — delete this file with the
feature): each "Delete ALL … (dev)" button asks first (iteration 29), Cancel
changes nothing, OK wipes that list and reports the count; with a group
chosen in the sidebar switcher only that group's rows go. Only a superuser
sees the buttons; they are hidden when the server says production.

WARNING: this permanently deletes every product, document, customer, project
and company in the stack it runs against — that is the feature. So it runs
only on the throwaway loop-test stack (destructive gate). Skipped when
Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR to also save
screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, destructive, unique
from test_group_move import PASSWORD, depts  # noqa: F401

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive

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


def _open(page, view, toggle, username=ADMIN_USERNAME, password=ADMIN_PASSWORD):
    page.goto(SITE + "/")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-form button[type=submit]")
    sync_api.expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
    page.click(f".nav-item[data-view={view}]")
    page.wait_for_selector(f"#{toggle}", state="visible")


# documents first: wiping them is not required by the others, but keeps the run tidy
@pytest.mark.parametrize("name", ["documents", "products", "customers", "projects", "companies"])
def test_confirm_then_deletes_all(admin, name, group_id):
    view, path, toggle, tbody, banner, empty, body = PAGES[name]
    # start empty so the banner count is exactly 3
    assert admin.delete(path).status_code == 200
    for _ in range(3):
        r = admin.post(path, json={**body(), "group_id": group_id})  # companies ignore it (shared)
        assert r.status_code == 201, r.text

    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        dialogs, answer = [], {"ok": False}
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept() if answer["ok"] else d.dismiss()))
        try:
            _open(page, view, toggle)
            btn = page.locator(f"#delete-all-{name}-btn")
            expect(btn).to_be_visible()
            expect(btn).to_have_text("Delete ALL companies (dev)" if name == "companies" else "Delete ALL (dev)")
            expect(page.locator(f"#{tbody} tr")).to_have_count(3)
            _shot(page, f"iter-15-{name}-before.png")

            btn.click()  # Cancel: nothing happens
            page.wait_for_timeout(500)
            assert dialogs == [f"Delete ALL {name}? This cannot be undone."]
            expect(page.locator(f"#{tbody} tr")).to_have_count(3)
            assert len(admin.get(path).json()) == 3

            answer["ok"] = True
            btn.click()
            expect(page.locator(f"#{banner}")).to_contain_text(f"Deleted 3 {name}")
            expect(page.locator(f"#{tbody}")).to_contain_text(empty)
            _shot(page, f"iter-15-{name}-after.png")
        finally:
            browser.close()
    assert admin.get(path).json() == []
    assert admin.get(f"{path}/trash").json() == []


def test_group_chosen_deletes_only_that_group(admin, depts):
    """Switcher on Operations: the button names the group, the dialog says
    exactly what goes, and CityMart's products stay. Group users: no buttons."""
    ops, city = depts["ops"], depts["city"]
    token = unique("dag")
    for d in (ops, city):
        for i in range(2):
            assert d["client"].post("/products", json={"name": f"P{i} {token}"}).status_code == 201
    expect = sync_api.expect
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        try:
            _open(page, "products", "toggle-product-form")
            page.select_option("#group-switcher", ops["group_id"])
            btn = page.locator("#delete-all-products-btn")
            expect(btn).to_have_text(f"Delete ALL in {ops['group_name']} (dev)")
            expect(page.locator("#delete-all-companies-btn")).to_have_text("Delete ALL companies (dev)")
            # a native confirm() is not in screenshots: this shows the button, the test asserts the dialog text
            _shot(page, "iter-29-delete-all-confirm.png")
            btn.click()
            expect(page.locator("#products-banner")).to_contain_text("Deleted 2 products")
            assert dialogs == [f"Delete ALL products in {ops['group_name']}? This cannot be undone."]
            page.select_option("#group-switcher", "none")
            expect(btn).to_have_text("Delete ALL unassigned (dev)")
            page.select_option("#group-switcher", "")
            expect(btn).to_have_text("Delete ALL (dev)")
        finally:
            browser.close()
        assert ops["client"].get("/products").json() == []
        assert len([x for x in city["client"].get("/products").json() if token in x["name"]]) == 2

        # a group user (editor with delete rights) sees none of the five buttons
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            _open(page, "products", "toggle-product-form", ops["username"], PASSWORD)
            page.wait_for_load_state("networkidle")
            for name in PAGES:
                expect(page.locator(f"#delete-all-{name}-btn")).to_be_hidden()
        finally:
            browser.close()


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
