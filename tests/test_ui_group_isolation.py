"""UI (loop iteration 19): two users in two departments each create a
product, customer, project and document in the browser; each then sees only
their own in the four lists and in Search. Single-group users get no Group
select; the superuser does, and it is required. Throwaway loop-test stack
only (creates groups/users). Skipped without Playwright. UI_SCREENSHOT_DIR
saves the evidence screenshots."""
import os

import httpx
import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, auth_headers, destructive, unique

sync_api = pytest.importorskip("playwright.sync_api")
pytestmark = destructive

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")
PASSWORD = "a long throwaway password 123"


def _ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.url}: {resp.status_code} {resp.text}"
    return resp.json() if resp.content else None


@pytest.fixture(scope="module")
def depts(admin):
    tag = unique("uiiso")
    out = {}
    for key in ("ops", "city"):
        g = _ok(admin.post("/admin/groups", json={"name": f"{key.title()} {tag}"}), 201)
        role = _ok(admin.post(f"/admin/groups/{g['id']}/roles", json={"name": "Dept Editor"}), 201)
        for service in ("documents", "products", "search", "documents-delete"):
            _ok(admin.post(f"/admin/roles/{role['id']}/access", json={"service_name": service, "access_level": "edit"}), 201)
        user = f"{key}-{tag}"
        _ok(admin.post(f"/admin/groups/{g['id']}/users", json={"username": user, "password": PASSWORD, "role_id": role["id"]}), 201)
        out[key] = {"group_id": g["id"], "group_name": g["name"], "username": user, "token": unique(f"UI{key}")}
    yield out
    for u in admin.get("/admin/users").json():
        if u["username"] in {d["username"] for d in out.values()}:
            admin.patch(f"/admin/users/{u['id']}/active", json={"is_active": False})


def _login(page, username, password):
    page.goto(SITE + "/")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-form button[type=submit]")
    sync_api.expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)


def _saved(page, modal):
    sync_api.expect(page.locator(f"#{modal}.active")).to_have_count(0, timeout=15000)


def _create_all(page, token):
    expect = sync_api.expect
    page.click(".nav-item[data-view=products]")
    page.click("#toggle-product-form")
    page.wait_for_selector("#product-modal.active")
    expect(page.locator("#product-form-group-wrap")).to_be_hidden()  # one group: nothing to choose
    page.fill("#product-name", f"Product {token}")
    page.click("#product-form-submit-btn")
    _saved(page, "product-modal")

    page.click(".nav-item[data-view=customers]")
    page.click("#toggle-customer-form")
    page.wait_for_selector("#customer-modal.active")
    page.fill("#customer-name", f"Customer {token}")
    page.click("#customer-form-submit-btn")
    _saved(page, "customer-modal")

    page.click(".nav-item[data-view=projects]")
    page.click("#toggle-project-form")
    page.wait_for_selector("#project-modal.active")
    page.fill("#project-name", f"Project {token}")
    page.click("#project-form-submit-btn")
    _saved(page, "project-modal")

    page.click(".nav-item[data-view=documents]")
    page.click("#toggle-doc-form")
    page.wait_for_selector("#doc-modal.active")
    expect(page.locator("#doc-form-group-wrap")).to_be_hidden()
    page.fill("#doc-customer", f"Customer {token}")
    page.fill("#doc-project", f"Project {token}")
    line = page.locator("#line-items .line-item-row").first
    line.locator(".li-qty").fill("1")
    line.locator(".li-price").fill("5")
    line.locator(".li-remark").fill(f"Line {token}")  # a free-text line item
    page.click("#doc-form-submit-btn")
    _saved(page, "doc-modal")


def _list_text(page, view, tbody, wait_for):
    page.click(f".nav-item[data-view={view}]")
    sync_api.expect(page.locator(f"#{tbody}")).to_contain_text(wait_for, timeout=15000)
    return page.locator(f"#{tbody}").inner_text()


def _search_text(page, q):
    page.click(".nav-item[data-view=search]")
    page.fill("#search-input", q)
    page.click("#search-btn")
    page.wait_for_timeout(1500)
    return page.locator("#search-results").inner_text()


def test_two_departments_see_only_their_own(depts, admin, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pages = {}
            for key, d in depts.items():
                page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
                _login(page, d["username"], PASSWORD)
                _create_all(page, d["token"])
                pages[key] = page

            # documents carry the group (checked through the API as the user)
            for key, d in depts.items():
                with httpx.Client(base_url=BASE_URL, timeout=60) as c:
                    tok = c.post("/auth/login", json={"username": d["username"], "password": PASSWORD}).json()["access_token"]
                    docs = c.get("/documents", headers=auth_headers(tok)).json()
                assert docs and {doc["group_id"] for doc in docs} == {d["group_id"]}
                d["doc_number"] = docs[0]["doc_number"]

            for key, other in (("ops", "city"), ("city", "ops")):
                page, mine, theirs = pages[key], depts[key], depts[other]
                for view, tbody, text_mine, text_theirs in (
                    ("products", "products-tbody", f"Product {mine['token']}", f"Product {theirs['token']}"),
                    ("customers", "customers-tbody", f"Customer {mine['token']}", f"Customer {theirs['token']}"),
                    ("projects", "projects-tbody", f"Project {mine['token']}", f"Project {theirs['token']}"),
                    ("documents", "documents-tbody", mine["doc_number"], theirs["doc_number"]),
                ):
                    page.reload()
                    sync_api.expect(page.locator("#whoami-username")).not_to_have_text("—", timeout=15000)
                    text = _list_text(page, view, tbody, text_mine)
                    assert text_mine in text, (key, view)
                    assert text_theirs not in text, (key, view)
                if SHOTS:
                    _list_text(page, "products", "products-tbody", f"Product {mine['token']}")
                    page.screenshot(path=os.path.join(SHOTS, f"iter-19-{'ops' if key == 'ops' else 'citymart'}-view.png"))
                found = _search_text(page, mine["token"])
                assert f"Product {mine['token']}" in found
                assert theirs["token"] not in _search_text(page, theirs["token"])

            # superuser: the Group select appears and is required
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
            page.click(".nav-item[data-view=products]")
            page.click("#toggle-product-form")
            page.wait_for_selector("#product-modal.active")
            select = page.locator("#product-form-group")
            sync_api.expect(select).to_be_visible()
            assert select.evaluate("el => el.required")
            names = select.locator("option").all_inner_texts()
            assert {depts["ops"]["group_name"], depts["city"]["group_name"]} <= set(names)
            select.select_option("")
            name = unique("No group chosen")
            page.fill("#product-name", name)
            page.click("#product-form-submit-btn")
            sync_api.expect(page.locator("#product-modal.active")).to_be_visible()  # browser validation stopped it
            assert not [x for x in admin.get("/products", params={"q": name}).json() if x["name"] == name]
            select.select_option(depts["city"]["group_id"])
            if SHOTS:
                page.locator("#product-modal .modal-box").evaluate("el => el.scrollTop = 0")
                page.screenshot(path=os.path.join(SHOTS, "iter-19-superuser-create-form.png"))
            page.click("#product-form-submit-btn")
            _saved(page, "product-modal")
            created = [x for x in admin.get("/products", params={"q": name}).json() if x["name"] == name]
            assert created and created[0]["group_id"] == depts["city"]["group_id"]
            admin.delete(f"/products/{created[0]['id']}")
        finally:
            browser.close()
