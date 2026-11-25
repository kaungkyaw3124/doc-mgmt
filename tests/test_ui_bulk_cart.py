"""UI (loop iteration 25): on Products, filter → Select all → "Add to cart (N)"
adds exactly the selected products with quantity 1, skips ones already in the
cart (quantities unchanged), refuses more than 200, and the cart still imports
into a new document. A group user can only cart their own group's products.
Throwaway loop-test stack only (it backdates rows straight in Postgres).
Skipped without Playwright. UI_SCREENSHOT_DIR saves the evidence screenshot."""
import random

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, destructive, unique
from test_backup_restore import dc
from test_group_move import PASSWORD, _ok, depts  # noqa: F401
from test_ui_group_isolation import _list_text, _login, _saved
from test_ui_select_all import _bar, _browser_page, _shot

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive


def _cart(page):
    """{product_id: quantity} as the cart modal shows it."""
    page.click("#open-cart-btn")
    page.wait_for_selector("#cart-modal.active")
    rows = page.eval_on_selector_all("#cart-tbody .cart-qty-input", "els => els.map(e => [e.dataset.qtyId, e.value])")
    return {i: int(q) for i, q in rows}


def _close_cart(page):
    page.click("#cart-modal-close")


def _filter(page, text):
    page.fill("#products-filter", text)
    page.dispatch_event("#products-filter", "input")


def _make(admin, group_id, token, n):
    return [_ok(admin.post("/products", json={"group_id": group_id, "name": f"{token} {i:03d}", "unit_price": "1"}), 201)["id"]
            for i in range(n)]


def test_filter_select_all_add_to_cart(admin, group_id):
    expect = sync_api.expect
    token = unique("bcart")
    ids = _make(admin, group_id, token, 100)
    day = f"{random.randint(1990, 2009)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    ten = ids[:10]
    sql = f"UPDATE products SET created_at = '{day} 12:00' WHERE id IN ({','.join(repr(i) for i in ten)})"
    r = dc("exec", "-T", "postgres", "psql", "-U", "docmgmt", "-d", "catalogue", "-v", "ON_ERROR_STOP=1", "-c", sql)
    assert r.returncode == 0 and "UPDATE 10" in r.stdout, r.stdout + r.stderr
    cust = _ok(admin.post("/customers", json={"group_id": group_id, "name": f"Cust {token}"}), 201)
    proj = _ok(admin.post("/projects", json={"group_id": group_id, "name": f"Proj {token}"}), 201)

    errors = []
    with sync_api.sync_playwright() as p:
        page = _browser_page(p, errors)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        _list_text(page, "products", "products-tbody", token)
        expect(page.locator("#products-cart-selected-btn")).to_be_hidden()  # empty selection

        # date filter → 10 → select all → add
        page.fill("#products-date-from", day)
        page.fill("#products-date-to", day)
        page.dispatch_event("#products-date-to", "change")
        expect(page.locator("#products-tbody tr")).to_have_count(10)
        page.check("#products-select-all")
        expect(page.locator("#products-cart-selected-btn")).to_have_text("Add to cart (10)")
        _shot(page, "iter-25-bulk-cart-selection.png")
        page.click("#products-cart-selected-btn")
        expect(page.locator(".tick-toast.tick-ok")).to_contain_text("10 added to cart")
        expect(_bar(page, "products")).to_be_hidden()  # selection cleared
        expect(page.locator("#cart-count-badge")).to_have_text("(10)")
        assert _cart(page) == {i: 1 for i in ten}
        _shot(page, "iter-25-bulk-cart.png")
        _close_cart(page)

        # 5 of them already in the cart (one bumped to qty 3) + 5 new
        page.evaluate("ids => { cartItems = cartItems.filter(p => ids.includes(p.id)); cartItems[0].quantity = 3; updateCartBadge(); }", ten[:5])
        page.dispatch_event("#products-date-to", "change")  # re-render
        page.check("#products-select-all")
        page.click("#products-cart-selected-btn")
        expect(page.locator(".tick-toast.tick-ok").last).to_contain_text("5 added, 5 already in cart")
        cart = _cart(page)
        assert cart == {**{i: 1 for i in ten}, ten[0]: 3}
        expect(page.locator("#cart-count-badge")).to_have_text("(12)")
        _close_cart(page)

        # cart → new document: one line per cart item with its quantity
        page.click(".nav-item[data-view=documents]")
        page.click("#toggle-doc-form")
        page.wait_for_selector("#doc-modal.active")
        page.fill("#doc-customer", f"Cust {token}")
        page.fill("#doc-project", f"Proj {token}")
        page.click("#import-from-cart-btn")
        expect(page.locator("#line-items .line-item-row")).to_have_count(10)
        for price in page.locator("#line-items .li-price").all():  # prices are per document (as today)
            price.fill("2")
        page.click("#doc-form-submit-btn")
        _saved(page, "doc-modal")
        doc = next(d for d in _ok(admin.get("/documents")) if d.get("customer_id") == cust["id"])
        items = _ok(admin.get(f"/documents/{doc['id']}"))["items"]
        assert {i["product_id"]: float(i["quantity"]) for i in items} == {k: float(v) for k, v in cart.items()}

        # no filter (token search only): all 100 added
        page.evaluate("emptyCart()")
        page.click(".nav-item[data-view=products]")
        page.click("#products-reset-filters-btn")
        _filter(page, token)
        expect(page.locator("#products-tbody tr")).to_have_count(100)
        page.check("#products-select-all")
        page.click("#products-cart-selected-btn")
        expect(page.locator(".tick-toast.tick-ok").last).to_contain_text("100 added to cart")
        assert _cart(page) == {i: 1 for i in ids}
        _close_cart(page)
        page.close()
    assert not errors, errors
    for i in ids:
        admin.delete(f"/products/{i}")


def test_more_than_200_selected_adds_nothing(admin, group_id):
    expect = sync_api.expect
    token = unique("bcap")
    ids = _make(admin, group_id, token, 201)
    errors = []
    with sync_api.sync_playwright() as p:
        page = _browser_page(p, errors)
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        _list_text(page, "products", "products-tbody", token)
        _filter(page, token)
        expect(page.locator("#products-tbody tr")).to_have_count(201)
        page.check("#products-select-all")
        expect(page.locator("#products-cart-selected-btn")).to_have_text("Add to cart (201)")
        page.click("#products-cart-selected-btn")
        expect(page.locator(".tick-toast.tick-err")).to_contain_text("Select up to 200 at a time; 201 selected")
        expect(page.locator("#cart-count-badge")).to_have_text("")
        expect(page.locator("#products-selection-count")).to_have_text("201 selected")  # kept so the user can narrow it
        assert _cart(page) == {}
        page.close()
    assert not errors, errors
    for i in ids:
        admin.delete(f"/products/{i}")


def test_group_user_only_carts_own_products(depts, admin):
    expect = sync_api.expect
    ops, city = depts["ops"], depts["city"]
    token = unique("bgrp")
    own = _make(ops["client"], ops["group_id"], token, 3)
    _make(city["client"], city["group_id"], token, 2)
    errors = []
    with sync_api.sync_playwright() as p:
        page = _browser_page(p, errors)
        _login(page, ops["username"], PASSWORD)
        _list_text(page, "products", "products-tbody", token)
        _filter(page, token)
        expect(page.locator("#products-tbody tr")).to_have_count(3)
        page.check("#products-select-all")
        page.click("#products-cart-selected-btn")  # not a superuser: still has the button
        expect(page.locator(".tick-toast.tick-ok")).to_contain_text("3 added to cart")
        assert _cart(page) == {i: 1 for i in own}
        page.close()
    assert not errors, errors
