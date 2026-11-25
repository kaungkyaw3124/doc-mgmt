"""
Loop iteration 26 — per-user project grants combined with group separation,
end to end through Nginx on real Postgres.

Group A has 3 projects, group B has 1. A user in A granted 2 of A's projects
must see exactly those 2; a user with no grants sees all of A's; a grant
outside the user's groups is refused (that kind of grant is what left a
user seeing "No projects match."), and the grant picker lists only A's projects.

Creates groups and users, so it only runs on the throwaway loop-test stack.
"""
import httpx
import pytest

from conftest import BASE_URL, auth_headers, destructive, unique

pytestmark = destructive

PASSWORD = "a long throwaway password 123"


def _ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.method} {resp.request.url}: {resp.status_code} {resp.text}"
    return resp.json() if resp.content else None


def _login(username):
    token = _ok(httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": PASSWORD}, timeout=60))["access_token"]
    return httpx.Client(base_url=BASE_URL, timeout=60.0, headers=auth_headers(token))


@pytest.fixture(scope="module")
def world(admin):
    tag = unique("grant")
    ga = _ok(admin.post("/admin/groups", json={"name": f"A {tag}"}), 201)["id"]
    gb = _ok(admin.post("/admin/groups", json={"name": f"B {tag}"}), 201)["id"]
    role = _ok(admin.post(f"/admin/groups/{ga}/roles", json={"name": "Editor"}), 201)
    _ok(admin.post(f"/admin/roles/{role['id']}/access", json={"service_name": "documents", "access_level": "edit"}), 201)
    names = {}
    for key in ("granted", "plain", "stray"):
        names[key] = f"{key}-{tag}"
        _ok(admin.post(f"/admin/groups/{ga}/users", json={"username": names[key], "password": PASSWORD, "role_id": role["id"]}), 201)
    ids = {u["username"]: u["id"] for u in _ok(admin.get("/admin/users"))}
    a = [_ok(admin.post("/projects", json={"name": f"A{i} {tag}", "group_id": ga}), 201)["id"] for i in range(3)]
    b = _ok(admin.post("/projects", json={"name": f"B0 {tag}", "group_id": gb}), 201)["id"]
    return {"a": a, "b": b, "ga": ga, "gb": gb, "uid": {k: ids[v] for k, v in names.items()}, "names": names}


def _project_ids(client):
    return sorted(p["id"] for p in _ok(client.get("/projects")))


def test_grantable_projects_are_only_the_users_groups(admin, world):
    for key in ("granted", "plain"):
        body = _ok(admin.get(f"/admin/users/{world['uid'][key]}/available-projects"))
        assert sorted(body["project_ids"]) == sorted(world["a"])


def test_grant_outside_users_groups_is_rejected(admin, world):
    r = admin.post(f"/admin/users/{world['uid']['granted']}/project-access", json={"project_id": world["b"]})
    assert r.status_code == 403, r.text
    assert world["b"] not in _ok(admin.get(f"/admin/users/{world['uid']['granted']}/project-access"))


def test_unknown_project_grant_is_rejected(admin, world):
    r = admin.post(f"/admin/users/{world['uid']['granted']}/project-access",
                   json={"project_id": "00000000-0000-0000-0000-000000000099"})
    assert r.status_code == 403, r.text


def test_user_with_grants_sees_exactly_them_and_no_grants_sees_whole_group(admin, world):
    uid = world["uid"]["granted"]
    for pid in world["a"][:2]:
        _ok(admin.post(f"/admin/users/{uid}/project-access", json={"project_id": pid}), 201)
    assert _project_ids(_login(world["names"]["granted"])) == sorted(world["a"][:2])
    assert _project_ids(_login(world["names"]["plain"])) == sorted(world["a"])
    # the ungranted project of the own group is 403 on a direct id, the other group's 404
    granted = _login(world["names"]["granted"])
    assert granted.get(f"/projects/{world['a'][2]}").status_code == 403
    assert granted.get(f"/projects/{world['b']}").status_code == 404


def test_grants_left_outside_users_groups_show_nothing(admin, world):
    """How a stray grant arises now: granted in-group, then the project is
    moved to another group. The user then sees no projects (scope ∩ grants is
    empty); the grant still lists in project-access so the admin can remove it."""
    uid = world["uid"]["stray"]
    a_extra = _ok(admin.post("/projects", json={"name": unique("moving"), "group_id": world["ga"]}), 201)
    _ok(admin.post(f"/admin/users/{uid}/project-access", json={"project_id": a_extra["id"]}), 201)
    _ok(admin.post(f"/projects/{a_extra['id']}/move", json={"group_id": world["gb"]}))
    assert _project_ids(_login(world["names"]["stray"])) == []
    assert a_extra["id"] in _ok(admin.get(f"/admin/users/{uid}/project-access"))
    assert a_extra["id"] not in _ok(admin.get(f"/admin/users/{uid}/available-projects"))["project_ids"]
    # removing the stray grant gives the user their whole group back
    _ok(admin.delete(f"/admin/users/{uid}/project-access/{a_extra['id']}"), 204)
    assert _project_ids(_login(world["names"]["stray"])) == sorted(world["a"])
