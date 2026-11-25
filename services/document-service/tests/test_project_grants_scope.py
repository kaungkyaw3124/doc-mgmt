"""
Loop iteration 26 — GET /projects with both gateway headers: X-Group-Ids
(group scope) and X-Allowed-Projects (per-user grants). The result is
always scope ∩ grants; grants outside the caller's groups show nothing.
"""
from tests.test_group_scope import AS_CITY, AS_OPS, AS_SUPER, ctx, _create  # noqa: F401 (ctx is a fixture)


def _names(client, headers, allowed):
    r = client.get("/projects", headers={**headers, "X-Allowed-Projects": allowed})
    assert r.status_code == 200, r.text
    return sorted(p["name"] for p in r.json())


def test_list_projects_group_scope_and_grants(ctx):
    client, _ = ctx
    a1, a2, _a3 = (_create(client, "/projects", AS_CITY, name=f"city-{i}") for i in range(3))
    b1 = _create(client, "/projects", AS_OPS, name="ops-1")

    # granted ∩ group -> exactly the granted ones
    assert _names(client, AS_CITY, f"{a1['id']},{a2['id']}") == ["city-0", "city-1"]
    # a grant outside the group is dropped, the in-group one kept
    assert _names(client, AS_CITY, f"{a1['id']},{b1['id']}") == ["city-0"]
    # grants entirely outside the group -> empty (the reported symptom)
    assert _names(client, AS_CITY, b1["id"]) == []
    # no grants (ALL / header missing) -> every project of the own group
    assert _names(client, AS_CITY, "ALL") == ["city-0", "city-1", "city-2"]
    r = client.get("/projects", headers=AS_CITY)
    assert sorted(p["name"] for p in r.json()) == ["city-0", "city-1", "city-2"]
    # NONE -> nothing
    assert _names(client, AS_CITY, "NONE") == []
    # superuser: grants still narrow, across groups
    assert _names(client, AS_SUPER, f"{a1['id']},{b1['id']}") == ["city-0", "ops-1"]
    # a malformed id is dropped (fails closed), not a 500
    assert _names(client, AS_CITY, f"not-a-uuid,{a1['id']}") == ["city-0"]
    # single-project lookup follows the same grants
    assert client.get(f"/projects/{a1['id']}", headers={**AS_CITY, "X-Allowed-Projects": a1["id"]}).status_code == 200
    assert client.get(f"/projects/{a2['id']}", headers={**AS_CITY, "X-Allowed-Projects": a1["id"]}).status_code == 403
