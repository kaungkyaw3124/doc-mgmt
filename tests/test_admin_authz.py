"""Loop iteration 11 (server check): a plain Editor — not a superuser, not
a group admin — gets no admin data through the gateway. Most admin reads
are 403; the two that answer 200 by design return nothing the user isn't
entitled to: /admin/users/pending is an empty list for a non-admin, and
/admin/groups lists only the user's own groups. Group/role reads are
checked on a group the editor is NOT a member of (members may read their
own group's members/roles, by design)."""
import httpx
import pytest

from conftest import BASE_URL, auth_headers, unique


@pytest.fixture
def other_group(admin):
    """A group (with one role) the editor doesn't belong to; deleted afterwards."""
    group = admin.post("/admin/groups", json={"name": unique("authz-group")})
    assert group.status_code == 201, group.text
    role = admin.post(f"/admin/groups/{group.json()['id']}/roles", json={"name": "Probe role"})
    assert role.status_code == 201, role.text
    yield group.json(), role.json()
    admin.delete(f"/admin/groups/{group.json()['id']}")


def test_editor_gets_no_admin_data(admin, editor_user, other_group):
    group, role = other_group
    admin_id = next(u["id"] for u in admin.get("/admin/users").json() if u["is_superuser"])
    login = httpx.post(BASE_URL + "/auth/login", json={"username": editor_user["username"], "password": editor_user["password"]})
    assert login.status_code == 200, login.text
    as_editor = httpx.Client(base_url=BASE_URL, headers=auth_headers(login.json()["access_token"]), timeout=30)

    forbidden = [
        ("GET", "/admin/users"),
        ("GET", f"/admin/users/{admin_id}/sessions"),
        ("GET", f"/admin/users/{admin_id}/roles"),
        ("GET", f"/admin/users/{admin_id}/groups"),
        ("GET", f"/admin/users/{admin_id}/project-access"),
        ("GET", f"/admin/users/{admin_id}/available-projects"),
        ("GET", f"/admin/groups/{group['id']}/members"),
        ("GET", f"/admin/groups/{group['id']}/roles"),
        ("GET", f"/admin/groups/{group['id']}/project-access"),
        ("GET", f"/admin/roles/{role['id']}"),
        ("POST", f"/admin/users/{admin_id}/logout-all"),
        ("PATCH", f"/admin/users/{admin_id}/active"),
    ]
    results = {}
    for method, path in forbidden:
        kwargs = {"json": {"is_active": False}} if method == "PATCH" else {}
        results[f"{method} {path}"] = as_editor.request(method, path, **kwargs).status_code
    print("\n" + "\n".join(f"{code}  {call}" for call, code in results.items()))
    assert all(code == 403 for code in results.values()), results

    pending = as_editor.get("/admin/users/pending")
    assert pending.status_code == 200 and pending.json() == []
    own_groups = as_editor.get("/admin/groups")
    assert own_groups.status_code == 200 and [g["name"] for g in own_groups.json()] == ["Operation"]
    print(f"200  GET /admin/users/pending -> {pending.json()}\n200  GET /admin/groups -> {[g['name'] for g in own_groups.json()]}")
