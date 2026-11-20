"""UI (loop iteration 26): the superuser's Manage user -> Project access list
shows one checkbox per row, only the projects of the user's own groups, each
with its group tag. Ticking one makes the user see exactly that project.
Throwaway loop-test stack only (creates groups/users). Skipped without
Playwright. UI_SCREENSHOT_DIR saves the evidence screenshots."""
import os

import pytest

from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, BASE_URL, destructive, unique

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
pytestmark = destructive

SITE = BASE_URL.removesuffix("/api")
SHOTS = os.environ.get("UI_SCREENSHOT_DIR")
PASSWORD = "a long throwaway password 123"


def _ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.url}: {resp.status_code} {resp.text}"
    return resp.json() if resp.content else None


def _login(page, username, password):
    page.goto(SITE + "/")
    page.fill("#login-username", username)
    page.fill("#login-password", password)
    page.click("#login-form button[type=submit]")
    expect(page.locator("#whoami-username")).to_contain_text(username, timeout=15000)


def test_manage_user_project_access_lists_own_group_projects_stacked(admin):
    tag = unique("uipa")
    city = _ok(admin.post("/admin/groups", json={"name": f"City {tag}"}), 201)
    ops = _ok(admin.post("/admin/groups", json={"name": f"Ops {tag}"}), 201)
    role = _ok(admin.post(f"/admin/groups/{city['id']}/roles", json={"name": "Editor"}), 201)
    _ok(admin.post(f"/admin/roles/{role['id']}/access", json={"service_name": "documents", "access_level": "edit"}), 201)
    user = f"ets-{tag}"
    _ok(admin.post(f"/admin/groups/{city['id']}/users", json={"username": user, "password": PASSWORD, "role_id": role["id"]}), 201)
    mine = [_ok(admin.post("/projects", json={"name": f"City P{i} {tag}", "group_id": city["id"]}), 201) for i in range(2)]
    other = _ok(admin.post("/projects", json={"name": f"Ops P {tag}", "group_id": ops["id"]}), 201)

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)
        page.click("#admin-nav-item")
        page.fill("#all-users-filter", user)
        page.click(f"#all-users-tbody tr:has-text('{user}') [data-edit-user-id]")
        boxes = page.locator("#edit-user-project-checkboxes label")
        expect(boxes).to_have_count(2, timeout=15000)
        texts = boxes.all_inner_texts()
        assert all(city["name"] in t for t in texts), texts  # group tag shown
        assert sorted(t.split(city["name"])[0].strip() for t in texts) == sorted(m["name"] for m in mine)
        assert not any(other["name"] in t for t in texts)
        # stacked: each row starts at the same x, on its own line
        first, second = boxes.nth(0).bounding_box(), boxes.nth(1).bounding_box()
        assert first["x"] == second["x"] and second["y"] >= first["y"] + first["height"]

        boxes.filter(has_text=mine[0]["name"]).locator("input").check()
        expect(boxes.filter(has_text=mine[0]["name"]).locator("input")).to_be_checked()
        page.wait_for_timeout(500)  # let the POST land before the screenshot / next login
        if SHOTS:
            page.locator("#edit-user-modal .modal-box").first.screenshot(path=os.path.join(SHOTS, "iter-26-manage-user-projects.png"))
        browser.close()

    uid = next(u["id"] for u in _ok(admin.get("/admin/users")) if u["username"] == user)
    assert _ok(admin.get(f"/admin/users/{uid}/project-access")) == [mine[0]["id"]]

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _login(page, user, PASSWORD)
        page.click(".nav-item[data-view=projects]")
        rows = page.locator("#projects-tbody tr")
        expect(rows).to_have_count(1, timeout=15000)
        expect(rows.first).to_contain_text(mine[0]["name"])
        if SHOTS:
            page.screenshot(path=os.path.join(SHOTS, "iter-26-group-user-projects.png"))
        browser.close()
