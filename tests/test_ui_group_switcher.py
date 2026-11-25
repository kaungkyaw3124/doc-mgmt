"""UI (loop iteration 29): the superuser's sidebar "Viewing" switcher filters
Products, Documents, Customers and Projects at once (All groups / one group /
Unassigned), shows "Showing: <group>" on each list, counts the open list in
its options, clears a selection when changed, pre-selects the group in the
New product form, is remembered across a reload and cleared on logout.
A group user has no switcher and no per-list group dropdown.
Creates groups, so loop-test stack only. Skipped without Playwright.
UI_SCREENSHOT_DIR saves the evidence screenshots."""
import os
import re

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, destructive, unique
from test_group_move import PASSWORD, _doc_set, _ok, depts  # noqa: F401
from test_ui_group_isolation import _login

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")
LISTS = ["products", "documents", "customers", "projects"]


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _rows(page, table):
    return page.locator(f"#{table}-tbody tr")


def test_switcher_filters_all_four_lists(admin, depts):
    expect = sync_api.expect
    ops, city = depts["ops"], depts["city"]
    token = unique("sw")
    made = {}
    for key, d in (("ops", ops), ("city", city)):
        doc, cust, proj = _doc_set(d["client"], f"{key} {token}")
        prod = _ok(d["client"].post("/products", json={"name": f"Product {key} {token}"}), 201)
        made[key] = {"documents": doc["id"], "customers": cust["id"], "projects": proj["id"], "products": prod["id"]}

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        switcher = page.locator("#group-switcher")
        expect(switcher).to_be_visible()
        expect(switcher).to_have_value("")
        assert page.locator("[id$='-group-filter']").count() == 0  # the per-list dropdowns are gone
        page.click(".nav-item[data-view=products]")
        expect(page.locator(f'.row-select[value="{made["city"]["products"]}"]')).to_have_count(1, timeout=15000)
        _shot(page, "iter-29-switcher-sidebar.png")

        # one group: all four lists show only its rows, with the header label
        switcher.select_option(ops["group_id"])
        for table in LISTS:
            page.click(f".nav-item[data-view={table}]")
            expect(page.locator(f"#{table}-group-showing")).to_have_text(f"Showing: {ops['group_name']}")
            expect(page.locator(f'.row-select[value="{made["ops"][table]}"]')).to_have_count(1, timeout=15000)
            expect(page.locator(f'.row-select[value="{made["city"][table]}"]')).to_have_count(0)
            n = _rows(page, table).count()
            expect(_rows(page, table).filter(has_text=ops["group_name"])).to_have_count(n)
            # the option counts the list that is open
            label = switcher.locator(f'option[value="{ops["group_id"]}"]').inner_text()
            assert label == f"{ops['group_name']} ({n})", (table, label)
        page.click(".nav-item[data-view=products]")
        _shot(page, "iter-29-switcher-products-citymart.png")

        # changing the switcher clears a selection
        page.check(f'.row-select[value="{made["ops"]["products"]}"]')
        expect(page.locator("#products-selection-bar")).to_be_visible()
        switcher.select_option(city["group_id"])
        expect(page.locator("#products-selection-bar")).to_be_hidden()
        expect(page.locator(f'.row-select[value="{made["city"]["products"]}"]')).to_have_count(1)

        # New product pre-selects the viewed group (still changeable)
        page.click("#toggle-product-form")
        expect(page.locator("#product-form-group")).to_have_value(city["group_id"])
        page.select_option("#product-form-group", ops["group_id"])
        expect(page.locator("#product-form-group")).to_have_value(ops["group_id"])
        page.click("#product-modal-close")

        # Unassigned: only rows without a group; All groups: both
        switcher.select_option("none")
        for table in LISTS:
            page.click(f".nav-item[data-view={table}]")
            expect(page.locator(f"#{table}-group-showing")).to_have_text("Showing: Unassigned")
            expect(page.locator(f'.row-select[value="{made["ops"][table]}"]')).to_have_count(0)
            assert {t.strip() for t in page.locator(f"#{table}-tbody .group-tag").all_inner_texts()} <= {"Unassigned"}
        switcher.select_option("")
        for table in LISTS:
            page.click(f".nav-item[data-view={table}]")
            expect(page.locator(f"#{table}-group-showing")).to_be_hidden()
            for key in ("ops", "city"):
                expect(page.locator(f'.row-select[value="{made[key][table]}"]')).to_have_count(1, timeout=15000)
        label = switcher.locator('option[value=""]').inner_text()
        assert re.fullmatch(rf"All groups \({_rows(page, 'projects').count()}\)", label), label

        # remembered across a reload
        switcher.select_option(ops["group_id"])
        page.reload()
        expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
        expect(switcher).to_have_value(ops["group_id"])
        expect(page.locator("#documents-group-showing")).to_have_text(f"Showing: {ops['group_name']}")

        # a group user: no switcher, no per-list dropdown, nothing remembered for them
        page.click("#logout-btn")
        expect(page.locator("#login-screen")).to_be_visible(timeout=15000)
        assert page.evaluate("localStorage.getItem('ledger_group_view')") is None  # cleared on logout
        _login(page, ops["username"], PASSWORD)
        expect(switcher).to_be_hidden()
        assert page.locator("[id$='-group-filter']").count() == 0
        page.click(".nav-item[data-view=products]")
        expect(page.locator(f'.row-select[value="{made["ops"]["products"]}"]')).to_have_count(1, timeout=15000)
        expect(page.locator("#products-group-showing")).to_be_hidden()

        # back as the superuser: starts on All groups
        page.click("#logout-btn")
        expect(page.locator("#login-screen")).to_be_visible(timeout=15000)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        expect(switcher).to_have_value("")
        browser.close()
