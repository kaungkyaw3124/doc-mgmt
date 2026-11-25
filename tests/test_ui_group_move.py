"""UI (loop iteration 20): the superuser moves an Operations document (with
its customer and project) to CityMart from the Documents list; afterwards the
Operations user no longer sees it and the CityMart user does. Also the
"Unassigned data" card in Admin -> Groups. Throwaway loop-test stack only.
Skipped without Playwright. UI_SCREENSHOT_DIR saves the evidence screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, destructive, unique
from test_group_move import PASSWORD, _doc_set, _ok, depts  # noqa: F401
from test_ui_group_isolation import SITE, _list_text, _login

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def test_superuser_moves_a_document_with_its_customer_and_project(depts, admin):
    expect = sync_api.expect
    ops, city = depts["ops"], depts["city"]
    token = unique("uimv")
    doc, cust, proj = _doc_set(ops["client"], token)
    number = doc["doc_number"]
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
            _list_text(page, "documents", "documents-tbody", number)
            page.select_option("#group-switcher", ops["group_id"])
            row = page.locator("#documents-tbody tr", has_text=number)
            expect(row).to_contain_text(ops["group_name"])
            expect(page.locator("#documents-tbody tr")).to_have_count(1)  # filter: only Operations' row
            row.locator("[data-move-id]").click()
            expect(page.locator("#move-group-modal.active")).to_be_visible()
            expect(page.locator("#move-group-links")).to_be_checked()
            page.select_option("#move-group-target", city["group_id"])
            expect(page.locator("#move-group-preview")).to_have_text("Will move: 1 document, 1 customer, 1 project", timeout=10000)
            _shot(page, "iter-20-move-modal.png")
            page.uncheck("#move-group-links")  # without the box the server refuses, nothing changes
            expect(page.locator("#move-group-banner")).to_contain_text("also move its customer and project", timeout=10000)
            page.check("#move-group-links")
            expect(page.locator("#move-group-preview")).to_contain_text("1 document", timeout=10000)
            page.click("#move-group-submit")
            expect(page.locator(".tick-toast.tick-ok")).to_be_visible(timeout=10000)
            expect(page.locator("#move-group-modal.active")).to_have_count(0)
            page.select_option("#group-switcher", city["group_id"])
            expect(page.locator("#documents-tbody tr", has_text=number)).to_contain_text(city["group_name"], timeout=10000)
            _shot(page, "iter-20-after-move-superuser.png")

            # bulk: select two customers with the checkboxes, move them back
            a = _ok(city["client"].post("/customers", json={"name": f"Bulk A {token}"}), 201)
            b = _ok(city["client"].post("/customers", json={"name": f"Bulk B {token}"}), 201)
            page.reload()
            expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
            _list_text(page, "customers", "customers-tbody", f"Bulk B {token}")
            for c in (a, b):
                page.check(f'.row-select[value="{c["id"]}"]')
            page.click("#customers-move-selected-btn")
            page.select_option("#move-group-target", ops["group_id"])
            expect(page.locator("#move-group-preview")).to_have_text("Will move: 2 customers", timeout=10000)
            page.click("#move-group-submit")
            expect(page.locator(".tick-toast.tick-ok")).to_be_visible(timeout=10000)
            assert {x["id"] for x in _ok(ops["client"].get("/customers"))} >= {a["id"], b["id"]}

            # Unassigned data card (Admin -> Groups)
            page.click("#admin-nav-item")
            card = page.locator("#unassigned-card")
            expect(card).to_be_visible()
            d, pr = _ok(admin.get("/documents/unassigned-counts")), _ok(admin.get("/products/unassigned-counts"))
            expect(page.locator("#unassigned-counts")).to_have_text(
                f"Documents {d['documents']} · Products {pr['products']} · Customers {d['customers']} · Projects {d['projects']}")
            card.scroll_into_view_if_needed()
            _shot(page, "iter-20-unassigned-card.png")
            page.select_option("#unassigned-target", ops["group_id"])
            page.once("dialog", lambda dlg: dlg.accept())
            page.click("#unassigned-assign-btn")
            expect(page.locator("#unassigned-counts")).to_have_text("Documents 0 · Products 0 · Customers 0 · Projects 0", timeout=15000)

            # the two departments' own views
            for user, sees in ((ops, False), (city, True)):
                upage = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
                _login(upage, user["username"], PASSWORD)
                upage.click(".nav-item[data-view=documents]")
                upage.wait_for_timeout(1500)
                text = upage.locator("#documents-tbody").inner_text()
                assert (number in text) is sees, (user["username"], text)
                expect(upage.locator("#group-switcher")).to_be_hidden()  # no move controls
                assert upage.locator("[data-move-id]").count() == 0
                upage.click(".nav-item[data-view=customers]")
                upage.wait_for_timeout(1000)
                assert (f"Customer {token}" in upage.locator("#customers-tbody").inner_text()) is sees
                if sees:
                    _shot(upage, "iter-20-citymart-sees-moved.png")
                else:
                    _shot(upage, "iter-20-ops-no-longer-sees.png")
        finally:
            browser.close()
