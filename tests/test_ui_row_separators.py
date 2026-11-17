"""UI (loop iteration 13): repeated form blocks are clearly separated — MDs
get an "MD n" header (renumbered after Remove) and a solid line between them;
document line items and product sub-items get the same line. Skipped when
Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR to save
screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name, scroll_to=None):
    if SHOTS:
        page.set_viewport_size({"width": 1280, "height": 1800})  # tall enough to show several blocks
        if scroll_to is not None:
            scroll_to.scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(SHOTS, name))
        page.set_viewport_size({"width": 1280, "height": 1000})


def _top_border(locator):
    return locator.evaluate("el => { const s = getComputedStyle(el);"
                            " return [parseFloat(s.borderTopWidth), s.borderTopStyle, s.borderTopColor]; }")


def _assert_separated(rows):
    """No line above the first row; a visible solid line above every later one."""
    width, style, _ = _top_border(rows.nth(0))
    assert width == 0 or style == "none"
    for i in range(1, rows.count()):
        width, style, colour = _top_border(rows.nth(i))
        assert width >= 1 and style == "solid", (i, width, style)
        assert colour not in ("transparent", "rgba(0, 0, 0, 0)"), colour


@pytest.fixture
def page():
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.goto(SITE + "/")
        page.fill("#login-username", ADMIN_USERNAME)
        page.fill("#login-password", ADMIN_PASSWORD)
        page.click("#login-form button[type=submit]")
        page.wait_for_selector("#toggle-doc-form", state="visible")
        try:
            yield page
        finally:
            browser.close()


def test_md_rows_are_numbered_separated_and_still_save(page, admin):
    name = unique("UI Separator Co")
    try:
        page.click(".nav-item[data-view=company]")
        page.click("#toggle-company-form")
        page.wait_for_selector("#company-modal.active")
        page.click("#add-director-row-btn")
        page.click("#add-director-row-btn")
        rows = page.locator("#director-rows .director-row")
        expect(rows.locator(".director-row-title")).to_have_text(["MD 1", "MD 2", "MD 3"])
        _assert_separated(rows)
        for i, md in enumerate(("U Aung Aung", "Daw Hla Hla", "U Kyaw Kyaw")):
            rows.nth(i).locator(".director-name").fill(md)
        page.fill("#company-name", name)
        _shot(page, "iter-13-md-rows.png", rows.nth(2))

        # Remove MD 1 (a new, unsaved row: no confirm) -> the rest close up to "MD 1", "MD 2"
        rows.nth(0).locator(".director-remove-btn").click()
        expect(rows.locator(".director-row-title")).to_have_text(["MD 1", "MD 2"])
        _assert_separated(rows)

        page.click("#company-form-submit-btn")
        page.wait_for_selector("#company-modal.active", state="hidden")
        company = next(c for c in admin.get("/companies").json() if c["name"] == name)
        saved = sorted(d["name"] for d in admin.get(f"/companies/{company['id']}/directors").json())
        assert saved == ["Daw Hla Hla", "U Kyaw Kyaw"]
    finally:
        for c in admin.get("/companies").json():
            if c["name"] == name:
                admin.delete(f"/companies/{c['id']}")


def test_document_line_items_are_separated(page):
    page.click("#toggle-doc-form")
    page.wait_for_selector("#doc-modal.active")
    page.click("#add-line-btn")
    page.click("#add-line-btn")
    rows = page.locator("#line-items .line-item-row")
    expect(rows).to_have_count(3)
    _assert_separated(rows)
    for i, remark in enumerate(("Laptop", "Monitor", "Keyboard")):
        rows.nth(i).locator(".li-remark").fill(remark)
    _shot(page, "iter-13-line-items.png", rows.nth(2))


@pytest.fixture
def parts(admin, category):
    made = []
    for part in ("CPU", "RAM", "GPU"):
        r = admin.post("/products", json={"name": unique(f"UI Part {part}"), "category": category["name"],
                                          "unit_price": "1.00"})
        assert r.status_code == 201, r.text
        made.append(r.json())
    yield made
    for part in made:
        admin.delete(f"/products/{part['id']}")


# `parts` before `page`: the app loads the product list once, at sign-in
def test_product_sub_items_are_separated(parts, page):
    page.click(".nav-item[data-view=products]")
    page.click("#toggle-product-form")
    page.wait_for_selector("#product-modal.active")
    for part in parts:
        page.fill("#sub-item-search", f"{part['sku']} — {part['name']}")
    rows = page.locator("#sub-items-list .line-item-row")
    expect(rows).to_have_count(3)
    _assert_separated(rows)
    _shot(page, "iter-13-sub-items.png", rows.nth(2))
