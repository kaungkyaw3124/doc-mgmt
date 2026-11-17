"""UI smoke (loop iteration 15 part B): a modal's heading and Close button stay
visible after its body is scrolled to the bottom. Skipped when Playwright for
Python isn't installed. Set UI_SCREENSHOT_DIR to also save screenshots."""
import io
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")
openpyxl = pytest.importorskip("openpyxl")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _login(p):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(SITE + "/")
    page.fill("#login-username", ADMIN_USERNAME)
    page.fill("#login-password", ADMIN_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector(".nav-item[data-view=products]", state="visible")
    return browser, page


def _assert_header_visible(page, modal_id, close_id):
    vh = page.viewport_size["height"]
    for sel in (f"#{close_id}", f"#{modal_id} h3"):
        loc = page.locator(sel)
        sync_api.expect(loc).to_be_visible()
        box = loc.bounding_box()
        assert box and 0 <= box["y"] and box["y"] + box["height"] <= vh, (sel, box)
        # nothing in the scrolled body is painted over it
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        assert page.evaluate(
            "([s, x, y]) => document.querySelector(s).contains(document.elementFromPoint(x, y))",
            [sel, cx, cy]), f"{sel} is covered after scrolling"


def _scroll_box_to_bottom(page, modal_id):
    return page.evaluate(
        """id => { const b = document.querySelector('#' + id + ' .modal-box');
                   b.scrollTop = b.scrollHeight; return b.scrollTop; }""", modal_id)


def test_bulk_import_close_stays_visible(admin):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Name", "Description"])
    tag = unique("Sticky")
    for i in range(45):
        ws.append([f"{tag} {i}", "row for the sticky-header test"])
    path = os.path.join(SHOTS or "/tmp", f"{tag}.xlsx")
    buf = io.BytesIO()
    wb.save(buf)
    with open(path, "wb") as f:
        f.write(buf.getvalue())

    with sync_api.sync_playwright() as p:
        browser, page = _login(p)
        try:
            page.click(".nav-item[data-view=products]")
            page.evaluate("document.getElementById('open-bulk-import-btn').click()")
            sync_api.expect(page.locator("#bulk-import-modal")).to_be_visible()
            page.set_input_files("#bulk-import-excel", path)
            page.click("#bulk-import-submit-btn")
            sync_api.expect(page.locator("#bulk-import-results")).to_contain_text("Created", timeout=30000)

            assert _scroll_box_to_bottom(page, "bulk-import-modal") > 0, "results did not overflow the modal"
            _assert_header_visible(page, "bulk-import-modal", "bulk-import-close")
            if SHOTS:
                page.screenshot(path=os.path.join(SHOTS, "iter-15-bulk-import-scrolled.png"))
            page.click("#bulk-import-close")
            sync_api.expect(page.locator("#bulk-import-modal")).to_be_hidden()
        finally:
            browser.close()
            os.remove(path)
    # tidy up the imported products
    for prod in admin.get("/products", params={"q": tag}).json():
        admin.delete(f"/products/{prod['id']}")


@pytest.mark.parametrize("modal_id,close_id", [
    ("audit-log-modal", "audit-log-close"),
    ("create-user-modal", "create-user-close"),
    ("customer-modal", "customer-modal-close"),
    ("edit-role-modal", "edit-role-close"),
])
def test_other_modals_close_stays_visible(modal_id, close_id):
    with sync_api.sync_playwright() as p:
        browser, page = _login(p)
        try:
            # open directly and pad the body so it is taller than the box
            page.evaluate(
                """id => { const m = document.getElementById(id); m.classList.add('active');
                           const pad = document.createElement('div'); pad.style.height = '3000px';
                           pad.textContent = 'filler';
                           const b = m.querySelector('.table-scroll-wrap') || m.querySelector('.modal-box');
                           b.appendChild(pad); b.scrollTop = b.scrollHeight; }""", modal_id)
            _assert_header_visible(page, modal_id, close_id)
            page.click(f"#{close_id}")
            sync_api.expect(page.locator(f"#{modal_id}")).to_be_hidden()
        finally:
            browser.close()
