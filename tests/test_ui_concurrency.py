"""UI (loop iteration 21): safe simultaneous use in the browser.
- Two browser contexts edit the same company: the second save gets "Someone
  else changed this…" with a Reload button, and keeps the typed text until
  Reload; after Reload it saves fine.
- Save is disabled (and says "Saving…") while the request runs; a double
  click and even a double submit of the form create one customer.
- A new product whose catalogue file fails to upload is not left behind.
Skipped when Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR to
save screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _login(browser):
    page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector(".nav-item[data-view=company]")
    return page


def _open_company(page, company_id):
    page.click(".nav-item[data-view=company]")
    page.click(f"[data-edit-company-id='{company_id}']")
    page.wait_for_selector("#company-modal.active")


def test_two_people_edit_one_company(admin):
    company = admin.post("/companies", json={"name": unique("UI Race Co")}).json()
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                a, b = _login(browser), _login(browser)
                _open_company(a, company["id"])
                _open_company(b, company["id"])  # both loaded version 1

                a.fill("#company-address", "Address typed by A")
                a.click("#company-form-submit-btn")
                a.wait_for_selector("#company-modal.active", state="hidden")

                b.fill("#company-address", "Address typed by B")
                b.click("#company-form-submit-btn")
                banner = b.locator("#company-modal-banner")
                expect(banner).to_contain_text("Someone else changed this. Reload to see their changes.")
                expect(b.locator("#company-address")).to_have_value("Address typed by B")  # nothing lost
                expect(banner.locator(".conflict-reload-btn")).to_be_in_viewport()
                _shot(b, "iter-21-conflict-banner.png")
                assert admin.get(f"/companies/{company['id']}").json()["address"] == "Address typed by A"

                banner.locator(".conflict-reload-btn").click()
                expect(b.locator("#company-address")).to_have_value("Address typed by A")  # their changes
                expect(banner).to_be_empty()
                b.fill("#company-address", "B after reload")
                b.click("#company-form-submit-btn")
                b.wait_for_selector("#company-modal.active", state="hidden")
            finally:
                browser.close()
        assert admin.get(f"/companies/{company['id']}").json()["address"] == "B after reload"
    finally:
        admin.delete(f"/companies/{company['id']}")


def test_save_is_disabled_while_running_and_double_submit_makes_one_record(admin):
    name, name2 = unique("UI Double"), unique("UI Twice")
    try:
        with sync_api.sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = _login(browser)
                page.click(".nav-item[data-view=customers]")

                # 1. held request: the button is disabled and says Saving…
                held = []
                page.route("**/api/customers", lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
                page.click("#toggle-customer-form")
                page.wait_for_selector("#customer-modal.active")
                page.fill("#customer-name", name)
                btn = page.locator("#customer-form-submit-btn")
                btn.dblclick()
                expect(btn).to_be_disabled()
                expect(btn).to_have_text("Saving…")
                _shot(page, "iter-21-saving-disabled.png")
                page.wait_for_timeout(300)
                assert len(held) == 1  # the second click hit a disabled button
                held[0].continue_()
                page.unroute("**/api/customers")
                page.wait_for_selector("#customer-modal.active", state="hidden")
                expect(btn).to_be_enabled()

                # 2. two submits that both get through (the button can't stop a
                # script/Enter-key race): same Idempotency-Key -> one record
                keys = []
                page.on("request", lambda r: keys.append(r.headers.get("idempotency-key"))
                        if r.method == "POST" and r.url.endswith("/api/customers") else None)
                page.click("#toggle-customer-form")
                page.wait_for_selector("#customer-modal.active")
                page.fill("#customer-name", name2)
                page.evaluate("() => { const f = document.getElementById('customer-form'); f.requestSubmit(); f.requestSubmit(); }")
                page.wait_for_selector("#customer-modal.active", state="hidden")
                page.wait_for_timeout(500)
                assert len(keys) == 2 and keys[0] and keys[0] == keys[1]
                expect(page.locator("#customers-table, body")).to_contain_text(name2)
            finally:
                browser.close()
        listed = [c["name"] for c in admin.get("/customers").json()]
        assert listed.count(name) == 1 and listed.count(name2) == 1
    finally:
        for c in admin.get("/customers").json():
            if c["name"] in (name, name2):
                admin.delete(f"/customers/{c['id']}")


def test_new_product_is_not_left_behind_when_its_file_fails(admin, tmp_path):
    name = unique("UI No Orphan")
    pdf = tmp_path / "catalogue.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = _login(browser)
            page.click(".nav-item[data-view=products]")
            page.route("**/api/products/*/file", lambda route: route.fulfill(status=502, json={"detail": "file storage unavailable"}))
            page.click("#toggle-product-form")
            page.wait_for_selector("#product-modal.active")
            page.fill("#product-name", name)
            page.set_input_files("#product-file", str(pdf))
            page.click("#product-form-submit-btn")
            expect(page.locator("#product-modal-banner")).to_contain_text("Nothing was saved")
            _shot(page, "iter-21-product-file-failed.png")
        finally:
            browser.close()
    assert name not in [x["name"] for x in admin.get("/products").json()]
