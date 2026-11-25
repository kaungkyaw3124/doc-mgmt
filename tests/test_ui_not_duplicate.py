"""UI (loop iteration 27): two same-name products with different
descriptions are listed under "Same name"; "Not a duplicate" hides them
(tick shown, still hidden after a reload); "Hidden pairs (N)" → Restore
brings them back. Skipped without Playwright. UI_SCREENSHOT_DIR saves the
evidence screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, unique

sync_api = pytest.importorskip("playwright.sync_api")

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _open_duplicates(page):
    page.click(".nav-item[data-view=products]")
    page.click("#open-product-duplicates-btn")
    page.wait_for_selector("#product-duplicates-modal.active")
    sync_api.expect(page.locator("#product-duplicates-content")).to_contain_text("Same name")


def test_not_a_duplicate_in_the_ui(admin, group_id):
    name = unique("External Hard Disk")
    ids = []
    for description in ("1 TB", "Seagate 4TB"):
        r = admin.post("/products", json={"group_id": group_id, "name": name, "description": description})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
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
                _open_duplicates(page)

                content = page.locator("#product-duplicates-content")
                button = page.locator(f'[data-dup-dismiss-ids*="{ids[0]}"]')
                expect(content).to_contain_text(name)
                expect(button).to_have_attribute("title", "These are different products — stop warning me")
                button.scroll_into_view_if_needed()
                _shot(page, "iter-27-duplicates-before.png")

                button.click()
                expect(page.locator(".tick-toast.tick-ok")).to_be_visible()
                expect(button).to_have_count(0)
                expect(content.locator("table", has_text=name).filter(has_text="Move to trash")).to_have_count(0)

                page.reload()
                _open_duplicates(page)
                expect(page.locator(f'[data-dup-dismiss-ids*="{ids[0]}"]')).to_have_count(0)
                summary = content.locator("summary", has_text="Hidden pairs")
                expect(summary).to_be_visible()
                summary.click()
                restore = content.locator("details tr", has_text=name).locator("[data-dup-restore-id]")
                expect(restore).to_have_count(1)
                restore.scroll_into_view_if_needed()
                _shot(page, "iter-27-duplicates-hidden.png")

                restore.click()
                expect(page.locator(f'[data-dup-dismiss-ids*="{ids[0]}"]')).to_have_count(1)
                assert not [d for d in admin.get("/products/duplicates/dismissed").json()
                            if ids[0] in {x["id"] for x in d["products"]}]
            finally:
                browser.close()
    finally:
        for product_id in ids:
            admin.delete(f"/products/{product_id}")
