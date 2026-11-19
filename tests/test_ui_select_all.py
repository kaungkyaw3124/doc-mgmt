"""UI (loop iteration 24): "Select all" on Products, Documents, Customers and
Projects selects exactly the rows the filters leave on screen; changing a
filter clears it, sorting keeps it; the superuser's "Move selected to group…"
moves exactly the ticked ids; a group user sees the column too. Throwaway
loop-test stack only (it backdates rows straight in Postgres). Skipped without
Playwright. UI_SCREENSHOT_DIR saves the evidence screenshots."""
import os
import random

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, destructive, unique
from test_backup_restore import dc
from test_group_move import PASSWORD, _ok, depts  # noqa: F401
from test_ui_group_isolation import _list_text, _login

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, name))


def _browser_page(p, errors):
    page = p.chromium.launch().new_context(viewport={"width": 1500, "height": 900}).new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page


def _checked_ids(page, table):
    return set(page.eval_on_selector_all(f"#{table}-tbody .row-select:checked", "els => els.map(e => e.value)"))


def _header(page, table):
    return page.eval_on_selector(f"#{table}-select-all", "e => [e.checked, e.indeterminate, e.disabled]")


def _bar(page, table):
    return page.locator(f"#{table}-selection-bar")


def test_select_all_selects_exactly_the_filtered_products(admin, group_id):
    expect = sync_api.expect
    token = unique("selall")
    ids = [_ok(admin.post("/products", json={"group_id": group_id, "name": f"{token} {i:03d}", "unit_price": "1"}), 201)["id"]
           for i in range(100)]
    # created_at cannot be set through the API: backdate 10 of them to a day
    # no other test row has (random per run so re-runs do not pile up).
    day = f"{random.randint(1990, 2009)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    ten = ids[:10]
    sql = f"UPDATE products SET created_at = '{day} 12:00' WHERE id IN ({','.join(repr(i) for i in ten)})"
    r = dc("exec", "-T", "postgres", "psql", "-U", "docmgmt", "-d", "catalogue", "-v", "ON_ERROR_STOP=1", "-c", sql)
    assert r.returncode == 0 and "UPDATE 10" in r.stdout, r.stdout + r.stderr

    errors = []
    with sync_api.sync_playwright() as p:
        page = _browser_page(p, errors)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        _list_text(page, "products", "products-tbody", token)
        expect(page.locator("#products-select-all")).to_have_attribute("aria-label", "Select all shown")
        expect(page.locator("#products-tbody .row-select").first).to_have_attribute("aria-label", "Select row")
        expect(_bar(page, "products")).to_be_hidden()

        # date filter: 10 of 100
        page.fill("#products-date-from", day)
        page.fill("#products-date-to", day)
        page.dispatch_event("#products-date-to", "change")
        expect(page.locator("#products-tbody tr")).to_have_count(10)
        page.check("#products-select-all")
        expect(_bar(page, "products")).to_be_visible()
        expect(page.locator("#products-selection-count")).to_have_text("10 selected")
        assert _checked_ids(page, "products") == set(ten)
        assert _header(page, "products") == [True, False, False]
        _shot(page, "iter-24-select-all-products.png")

        # sorting keeps the selection
        page.click('.sort-btn[data-sort-table="products"][data-sort-col="name"]')
        expect(page.locator("#products-selection-count")).to_have_text("10 selected")
        assert _checked_ids(page, "products") == set(ten)

        # indeterminate: untick one
        page.uncheck(f'.row-select[value="{ten[0]}"]')
        expect(page.locator("#products-selection-count")).to_have_text("9 selected")
        assert _header(page, "products") == [False, True, False]
        _shot(page, "iter-24-select-all-indeterminate.png")
        page.check(f'.row-select[value="{ten[0]}"]')
        assert _header(page, "products") == [True, False, False]

        # a filter change clears the selection and hides the bar
        page.fill("#products-date-from", "")
        page.dispatch_event("#products-date-from", "change")
        expect(_bar(page, "products")).to_be_hidden()
        assert _checked_ids(page, "products") == set()

        # search for the token only (other tests leave products behind): 100
        page.fill("#products-date-to", "")
        page.fill("#products-filter", token)
        page.dispatch_event("#products-filter", "input")
        expect(page.locator("#products-tbody tr")).to_have_count(100)
        page.check("#products-select-all")
        expect(page.locator("#products-selection-count")).to_have_text("100 selected")
        assert _checked_ids(page, "products") == set(ids)
        ms = page.eval_on_selector("#products-select-all", "e => { const t = performance.now(); e.click(); return performance.now() - t; }")
        assert ms < 100, f"un-select-all of 100 rows took {ms} ms"
        expect(_bar(page, "products")).to_be_hidden()
        assert _checked_ids(page, "products") == set()

        # Reset filters clears; Clear button clears
        page.check("#products-select-all")
        page.click("#products-selection-bar button:has-text('Clear')")
        expect(_bar(page, "products")).to_be_hidden()
        page.fill("#products-filter", token)
        page.dispatch_event("#products-filter", "input")
        page.check("#products-select-all")
        page.click("#products-reset-filters-btn")
        expect(_bar(page, "products")).to_be_hidden()

        # empty list: Select all disabled
        page.fill("#products-filter", unique("nothing"))
        page.dispatch_event("#products-filter", "input")
        expect(page.locator("#products-tbody")).to_contain_text("No products match.")
        assert _header(page, "products")[2] is True
        page.close()
    assert not errors, errors
    for i in ids:
        admin.delete(f"/products/{i}")


def test_select_all_on_documents_customers_projects_and_move_selected(depts, admin):
    """Group user: sees the column, select all picks only their own rows.
    Superuser: Move selected moves exactly the ticked ids; the reload after
    the move clears the selection."""
    expect = sync_api.expect
    ops, city = depts["ops"], depts["city"]
    token = unique("selmv")
    made = {"documents": [], "customers": [], "projects": []}
    for i in range(3):
        c = _ok(ops["client"].post("/customers", json={"name": f"Cust {token} {i}"}), 201)
        pr = _ok(ops["client"].post("/projects", json={"name": f"Proj {token} {i}"}), 201)
        d = _ok(ops["client"].post("/documents", json={
            "doc_type": "quotation", "customer_id": c["id"], "project_id": pr["id"],
            "items": [{"description": f"line {token}", "quantity": "1", "unit_price": "3"}]}), 201)
        made["customers"].append(c["id"]); made["projects"].append(pr["id"]); made["documents"].append(d["id"])
    _ok(city["client"].post("/customers", json={"name": f"Cust {token} other group"}), 201)

    errors = []
    with sync_api.sync_playwright() as p:
        # group user (not a superuser): column + select all, own rows only
        page = _browser_page(p, errors)
        _login(page, ops["username"], PASSWORD)
        for table, view in (("documents", "documents"), ("customers", "customers"), ("projects", "projects")):
            page.click(f".nav-item[data-view={view}]")
            expect(page.locator(f"#{table}-tbody tr").first).to_be_visible(timeout=15000)
            expect(page.locator(f"#{table}-select-all")).to_be_visible()
            if table != "documents":  # documents are numbered, search by number is not by token
                page.fill(f"#{table}-filter", token)
                page.dispatch_event(f"#{table}-filter", "input")
                expect(page.locator(f"#{table}-tbody tr")).to_have_count(3)
            page.check(f"#{table}-select-all")
            shown = page.locator(f"#{table}-tbody tr").count()
            expect(page.locator(f"#{table}-selection-count")).to_have_text(f"{shown} selected")
            picked = _checked_ids(page, table)
            own = {x["id"] for x in _ok(ops["client"].get(f"/{table}"))}
            assert picked <= own and len(picked) == shown  # never another group's row
            if table != "documents":
                assert picked == set(made[table])
            expect(page.locator(f"#{table}-move-selected-btn")).to_be_hidden()  # not a superuser
        assert f"other group" not in page.locator("#customers-tbody").inner_text()
        page.close()

        # superuser: tick 2 of 3 customers via select all + untick, move them
        page = _browser_page(p, errors)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        _list_text(page, "customers", "customers-tbody", token)
        page.fill("#customers-filter", f"Cust {token} ")
        page.dispatch_event("#customers-filter", "input")
        page.select_option("#customers-group-filter", ops["group_id"])
        expect(page.locator("#customers-tbody tr")).to_have_count(3)
        page.check("#customers-select-all")
        page.uncheck(f'.row-select[value="{made["customers"][2]}"]')
        assert _header(page, "customers") == [False, True, False]
        expect(page.locator("#customers-selection-count")).to_have_text("2 selected")
        page.click("#customers-move-selected-btn")
        page.select_option("#move-group-target", city["group_id"])
        expect(page.locator("#move-group-preview")).to_contain_text("2 customers", timeout=10000)
        page.click("#move-group-submit")
        expect(page.locator(".tick-toast.tick-ok")).to_be_visible(timeout=10000)
        expect(_bar(page, "customers")).to_be_hidden(timeout=10000)  # reload after the move cleared it
        city_ids = {x["id"] for x in _ok(city["client"].get("/customers"))}
        ops_ids = {x["id"] for x in _ok(ops["client"].get("/customers"))}
        assert set(made["customers"][:2]) <= city_ids and made["customers"][2] in ops_ids
        page.close()
    assert not errors, errors
