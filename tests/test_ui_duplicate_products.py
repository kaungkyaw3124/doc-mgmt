"""UI smoke (loop iteration 8): creating the same product twice shows "This
item already exists"; a same-name product with a different description
shows the warning dialog and "Create anyway" saves it; bulk import reports
skipped duplicates; the "Possible duplicates" page lists the groups.
Skipped when Playwright for Python isn't installed. Set UI_SCREENSHOT_DIR
to also save screenshots of each step."""
import io
import os

import openpyxl
import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _xlsx(tmp_path, rows):
    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Description"])
    for r in rows:
        wb.active.append(r)
    path = tmp_path / "products.xlsx"
    wb.save(path)
    return str(path)


def _fill_product(page, name, description):
    page.click("#toggle-product-form")
    page.wait_for_selector("#product-modal.active")
    page.fill("#product-name", name)
    page.fill("#product-description", description)
    page.click("#product-form-submit-btn")


def test_duplicate_products_in_the_ui(admin, tmp_path):
    name = unique("UI Dup Shelf")
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
                page.click(".nav-item[data-view=products]")
                page.wait_for_selector("#toggle-product-form", state="visible")

                _fill_product(page, name, "Steel, grey")
                page.wait_for_selector("#product-modal.active", state="hidden")

                # 1. exact duplicate -> blocked with a link to the existing product
                _fill_product(page, name, "steel,  GREY")
                expect(page.locator("#product-modal-banner")).to_contain_text("This item already exists")
                expect(page.locator("#product-exists-link")).to_be_visible()
                _shot(page, "iter-8-exact-block.png")
                page.click("#product-modal-close")

                # 2. same name, different description -> warning -> Create anyway
                _fill_product(page, name, "Steel, white")
                page.wait_for_selector("#product-dup-warning-modal.active")
                expect(page.locator("#product-dup-warning-tbody")).to_contain_text(name)
                _shot(page, "iter-8-warning.png")
                page.click("#product-dup-warning-create")
                page.wait_for_selector("#product-modal.active", state="hidden")
                assert len([x for x in admin.get("/products", params={"q": name}).json() if x["name"] == name]) == 2

                # 3. bulk import: one existing duplicate, one within-file duplicate, one same-name, one new
                page.click("#open-bulk-import-btn")
                page.wait_for_selector("#bulk-import-modal.active")
                page.set_input_files("#bulk-import-excel", _xlsx(tmp_path, [
                    [name, "Steel, grey"], [name + " Bracket", "Pair"], [name + " bracket ", "pair"], [name, "Steel, black"],
                ]))
                page.click("#bulk-import-submit-btn")
                expect(page.locator("#bulk-import-results")).to_contain_text("Skipped 2 duplicates")
                expect(page.locator("#bulk-import-results")).to_contain_text("duplicate of row 2")
                page.locator("#bulk-import-results p").last.scroll_into_view_if_needed()
                _shot(page, "iter-8-import-result.png")
                page.click("#bulk-import-close")

                # 4. possible duplicates page
                page.click("#open-product-duplicates-btn")
                expect(page.locator("#product-duplicates-content")).to_contain_text("Exact duplicates")
                expect(page.locator("#product-duplicates-content")).to_contain_text(name)
                _shot(page, "iter-8-duplicates-page.png")
            finally:
                browser.close()
    finally:
        for prod in admin.get("/products", params={"q": name}).json():
            admin.delete(f"/products/{prod['id']}")
