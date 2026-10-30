"""UI smoke (loop iteration 7): in the New document form, switch the customer
to "Enter manually", type a one-time customer, save, and see the document in
the list (Customer column) and on its detail view. Skipped when Playwright for
Python isn't installed (it's not in tests/requirements.txt). Set
UI_SCREENSHOT_DIR to also save screenshots of each step."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def test_create_document_with_manual_customer(admin):
    name = unique("UI Manual Buyer")
    doc_id = None
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        try:
            page.goto(SITE + "/")
            page.fill("#login-username", ADMIN_USERNAME)
            page.fill("#login-password", ADMIN_PASSWORD)
            page.click("#login-form button[type=submit]")
            page.wait_for_selector("#toggle-doc-form", state="visible")

            page.click("#toggle-doc-form")
            page.wait_for_selector("#doc-modal.active")
            # expect(), not wait_for_function: the page's CSP (script-src 'self') blocks string eval
            sync_api.expect(page.locator("#doc-number")).not_to_have_value("")
            assert page.is_visible("#doc-customer") and not page.is_visible("#doc-end-user-name")
            _shot(page, "iter-7-form-saved.png")

            page.check("#doc-customer-mode-manual")
            assert not page.is_visible("#doc-customer") and not page.is_visible("#doc-new-customer-btn")
            page.fill("#doc-end-user-name", name)
            page.fill("#doc-end-user-organization", "UI Manual Org")
            page.fill("#doc-end-user-address", "No. 3, UI Street, Yangon")
            page.fill("#doc-end-user-phone", "09-123")
            page.fill("#doc-end-user-email", "ui@x.example")
            page.fill("#line-items .li-remark", "UI test item")  # a non-product line needs a remark
            page.fill("#line-items .li-qty", "1")
            page.fill("#line-items .li-price", "5")
            page.locator("#doc-customer-mode-row").scroll_into_view_if_needed()
            _shot(page, "iter-7-form-manual.png")
            doc_number = page.input_value("#doc-number")

            page.click("#doc-form-submit-btn")
            page.wait_for_selector("#doc-modal.active", state="hidden")
            row_btn = page.locator("#documents-tbody [data-view-doc-id]", has_text=doc_number)
            row_btn.wait_for()
            doc_id = row_btn.get_attribute("data-view-doc-id")
            row = page.locator("#documents-tbody tr", has=page.locator(f"[data-view-doc-id='{doc_id}']"))
            sync_api.expect(row).to_contain_text(name)
            row_btn.scroll_into_view_if_needed()
            _shot(page, "iter-7-list.png")

            row_btn.click()
            page.wait_for_selector("#view-document-modal.active")
            page.wait_for_selector(f"#view-document-content >> text=Customer: {name}")
            _shot(page, "iter-7-detail.png")
        finally:
            browser.close()
            if doc_id:
                admin.delete(f"/documents/{doc_id}")

    stored = [c["name"] for c in admin.get("/customers").json()]
    assert name not in stored, "a one-time customer must never be added to Customers"
