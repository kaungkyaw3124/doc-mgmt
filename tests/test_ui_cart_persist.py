"""UI (loop iteration 28): the cart survives a page reload (sessionStorage),
drops products that no longer exist, and is gone after logging out and in as
another user. Skipped without Playwright."""
import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, unique
from test_ui_bulk_cart import _cart, _close_cart, _filter
from test_ui_group_isolation import _login
from test_ui_select_all import _browser_page, _shot

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect


def test_cart_survives_reload_and_not_another_user(admin, group_id, editor_user):
    token = unique("cartkeep")
    ids = []
    for i in range(3):
        r = admin.post("/products", json={"group_id": group_id, "name": f"{token} {i}", "unit_price": "1"})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    errors = []
    try:
        with sync_api.sync_playwright() as p:
            page = _browser_page(p, errors)
            _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
            page.click('.nav-item[data-view="products"]')
            _filter(page, token)
            for pid in ids:
                page.click(f'[data-add-to-cart-id="{pid}"]')
            expect(page.locator("#cart-count-badge")).to_have_text("(3)")

            page.reload()
            expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
            expect(page.locator("#cart-count-badge")).to_have_text("(3)")
            page.click('.nav-item[data-view="products"]')  # the app reopens on Documents
            assert _cart(page) == {i: 1 for i in ids}
            _shot(page, "iter-28-cart-after-reload.png")
            _close_cart(page)

            # a product deleted meanwhile drops out of the restored cart
            assert admin.delete(f"/products/{ids[0]}").status_code in (200, 204)
            page.reload()
            expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
            expect(page.locator("#cart-count-badge")).to_have_text("(2)")

            page.click("#logout-btn")
            expect(page.locator("#login-screen")).to_be_visible()
            _login(page, editor_user["username"], editor_user["password"])
            expect(page.locator("#cart-count-badge")).to_have_text("")
            page.click('.nav-item[data-view="products"]')
            assert _cart(page) == {}
        assert not errors, errors
    finally:
        for pid in ids:
            admin.delete(f"/products/{pid}")
