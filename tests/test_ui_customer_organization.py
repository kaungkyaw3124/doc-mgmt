"""UI (loop iteration 9, part B): the customer form has separate Name and
Organization fields; the customers table shows the organization, or the
name (muted) when there is none. Skipped when Playwright for Python isn't
installed. Set UI_SCREENSHOT_DIR to save a screenshot of the form."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def test_customer_form_and_table_show_the_organization(admin):
    plain = unique("UI Name Only")
    named = unique("UI With Org")
    org = unique("UI Org Holdings")
    expect = sync_api.expect
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            try:
                page.goto(SITE + "/")
                page.fill("#login-username", ADMIN_USERNAME)
                page.fill("#login-password", ADMIN_PASSWORD)
                page.click("#login-form button[type=submit]")
                page.click(".nav-item[data-view=customers]")

                for name, organization in ((plain, ""), (named, org)):
                    page.click("#toggle-customer-form")
                    page.wait_for_selector("#customer-modal.active")
                    page.fill("#customer-name", name)
                    page.fill("#customer-organization", organization)
                    if organization and SHOTS:
                        page.screenshot(path=os.path.join(SHOTS, "iter-9-customer-form.png"))
                    page.click("#customer-form-submit-btn")
                    page.wait_for_selector("#customer-modal.active", state="hidden")

                rows = page.locator("#customers-tbody tr")
                plain_row = rows.filter(has_text=plain)
                expect(plain_row.locator("td").nth(1)).to_have_text(plain)  # name shown (muted) as the organization
                expect(rows.filter(has_text=named).locator("td").nth(1)).to_have_text(org)

                page.fill("#customers-filter", org)  # the filter matches the organization too
                expect(rows.filter(has_text=named)).to_have_count(1)
                expect(rows.filter(has_text=plain)).to_have_count(0)
            finally:
                browser.close()
        stored = {c["name"]: c["organization"] for c in admin.get("/customers").json()}
        assert stored[plain] is None and stored[named] == org
    finally:
        for c in admin.get("/customers").json():
            if c["name"] in (plain, named):
                admin.delete(f"/customers/{c['id']}")
