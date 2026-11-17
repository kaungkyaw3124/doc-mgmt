"""UI (loop iteration 12): the company form has no Signer position, uses the
new labels, prefills a new MD row's Position with "Managing Director",
uppercases the short name, and shows the API's message for an invalid one
(an old long short name loaded into the edit form). Skipped when Playwright
for Python isn't installed. Set UI_SCREENSHOT_DIR to save screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def test_company_form_cleanup(admin):
    good = unique("UI Company WF")
    bad = unique("UI Company Old")
    expect = sync_api.expect
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            try:
                page.goto(SITE + "/")
                page.fill("#login-username", ADMIN_USERNAME)
                page.fill("#login-password", ADMIN_PASSWORD)
                page.click("#login-form button[type=submit]")
                page.click(".nav-item[data-view=company]")

                page.click("#toggle-company-form")
                page.wait_for_selector("#company-modal.active")
                modal = page.locator("#company-modal")
                expect(page.locator("#company-position")).to_have_count(0)
                expect(modal).not_to_contain_text("Signer position")
                for label in ('Short name (2–6 letters/digits, used in document numbers, e.g. "WF")',
                              "Office address", "Used when the MD has no address.",
                              "Customer service phone", "Customer service email",
                              "Printed in the footer of every quotation."):
                    expect(modal).to_contain_text(label)
                md_position = page.locator("#director-rows .director-position").first
                expect(md_position).to_have_value("Managing Director")
                expect(md_position).to_have_attribute("required", "")

                page.fill("#company-name", good)
                page.locator("#company-short-name").press_sequentially("wf")
                expect(page.locator("#company-short-name")).to_have_value("WF")
                page.fill("#director-rows .director-name", "U Aung Aung")
                if SHOTS:
                    page.screenshot(path=os.path.join(SHOTS, "iter-12-company-form.png"))
                page.click("#company-form-submit-btn")
                page.wait_for_selector("#company-modal.active", state="hidden")

                # invalid short name: the API's message is shown and nothing is saved
                page.click("#toggle-company-form")
                page.wait_for_selector("#company-modal.active")
                page.fill("#company-name", bad)
                # maxlength stops typing it, but an old long value loaded into the edit form gets here
                page.eval_on_selector("#company-short-name", "el => { el.value = 'Winterfell Co.,Ltd'; }")
                page.fill("#director-rows .director-name", "")
                page.click("#company-form-submit-btn")
                banner = page.locator("#company-modal-banner")
                expect(banner).to_contain_text("Short name must be 2–6 letters or digits")
                if SHOTS:
                    page.locator("#company-short-name").scroll_into_view_if_needed()
                    page.screenshot(path=os.path.join(SHOTS, "iter-12-shortname-error.png"))
            finally:
                browser.close()
        companies = {c["name"]: c for c in admin.get("/companies").json()}
        assert companies[good]["short_name"] == "WF" and "position" not in companies[good]
        assert bad not in companies
        mds = admin.get(f"/companies/{companies[good]['id']}/directors").json()
        assert [d["position"] for d in mds] == ["Managing Director"]
    finally:
        for c in admin.get("/companies").json():
            if c["name"] in (good, bad):
                admin.delete(f"/companies/{c['id']}")
