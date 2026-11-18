"""
Loop iteration 20 — a superuser moves documents, customers, projects and
products to another group, end to end through Nginx: the link rules, the
audit entry, search re-indexing (the old group stops finding it, the new
group finds it), non-superusers get 403, and the unassigned counts.

Creates groups and users, so it only runs against the throwaway loop-test
stack (same gate as test_group_isolation.py).
"""
import httpx
import pytest

from conftest import BASE_URL, auth_headers, destructive, poll, unique

pytestmark = destructive
PASSWORD = "a long throwaway password 123"


def _ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.method} {resp.request.url}: {resp.status_code} {resp.text}"
    return resp.json() if resp.content else None


def _login(username):
    token = _ok(httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": PASSWORD}, timeout=60))["access_token"]
    return httpx.Client(base_url=BASE_URL, timeout=60.0, headers=auth_headers(token))


@pytest.fixture(scope="module")
def depts(admin):
    """Operations and CityMart, an editor in each; the Operations editor is
    also *group admin* of both groups (group admins may not move data)."""
    tag = unique("mv")
    out = {}
    for key in ("ops", "city"):
        g = _ok(admin.post("/admin/groups", json={"name": f"{key.title()} {tag}"}), 201)
        role = _ok(admin.post(f"/admin/groups/{g['id']}/roles", json={"name": "Dept Editor"}), 201)
        for service in ("documents", "products", "search", "audit-log", "documents-delete"):
            _ok(admin.post(f"/admin/roles/{role['id']}/access", json={"service_name": service, "access_level": "edit"}), 201)
        user = f"{key}-{tag}"
        _ok(admin.post(f"/admin/groups/{g['id']}/users", json={"username": user, "password": PASSWORD, "role_id": role["id"]}), 201)
        out[key] = {"group_id": g["id"], "group_name": g["name"], "username": user}
    clients = {k: _login(d["username"]) for k, d in out.items()}
    for k, d in out.items():
        d["client"] = clients[k]
    yield out
    for c in clients.values():
        c.close()
    for u in admin.get("/admin/users").json():
        if u["username"] in {d["username"] for d in out.values()}:
            admin.patch(f"/admin/users/{u['id']}/active", json={"is_active": False})


def _doc_set(client, token):
    cust = _ok(client.post("/customers", json={"name": f"Customer {token}"}), 201)
    proj = _ok(client.post("/projects", json={"name": f"Project {token}"}), 201)
    doc = _ok(client.post("/documents", json={
        "doc_type": "quotation", "customer_id": cust["id"], "project_id": proj["id"],
        "items": [{"description": f"line {token}", "quantity": "1", "unit_price": "3"}],
    }), 201)
    return doc, cust, proj


def _hits(client, token, index):
    return [h["id"] for h in _ok(client.get("/search", params={"q": token, "index": index})).get(index, [])]


def test_move_document_with_links_reindexes_and_audits(admin, depts):
    ops, city = depts["ops"], depts["city"]
    token = unique("mvdoc")
    doc, cust, proj = _doc_set(ops["client"], token)
    assert poll(lambda: doc["id"] in _hits(ops["client"], token, "documents"))

    # without the box: refused, nothing changes
    r = admin.post(f"/documents/{doc['id']}/move", json={"group_id": city["group_id"], "with_links": False})
    assert r.status_code == 409 and "1 customer, 1 project" in r.json()["detail"]
    assert _ok(admin.post(f"/documents/{doc['id']}/move", json={"group_id": city["group_id"], "dry_run": True}))["moved"] == \
        {"documents": 1, "customers": 1, "projects": 1}
    assert ops["client"].get(f"/documents/{doc['id']}").status_code == 200

    moved = _ok(admin.post(f"/documents/{doc['id']}/move", json={"group_id": city["group_id"]}))
    assert moved == {"moved": {"documents": 1, "customers": 1, "projects": 1}, "dry_run": False}
    for path, row in (("/documents", doc), ("/customers", cust), ("/projects", proj)):
        assert ops["client"].get(f"{path}/{row['id']}").status_code == 404
        assert _ok(city["client"].get(f"{path}/{row['id']}"))["group_id"] == city["group_id"]
    assert poll(lambda: doc["id"] in _hits(city["client"], token, "documents")), "new group can't find it"
    assert poll(lambda: doc["id"] not in _hits(ops["client"], token, "documents")), "old group still finds it"
    log = _ok(admin.get(f"/documents/{doc['id']}/audit-log", headers={}))
    assert any(e["action"] == "moved_group" for e in log), log


def test_customer_and_project_rules_and_bulk(admin, depts):
    ops, city = depts["ops"], depts["city"]
    doc, cust, proj = _doc_set(ops["client"], unique("mvcust"))
    r = admin.post(f"/customers/{cust['id']}/move", json={"group_id": city["group_id"], "with_links": False})
    assert r.status_code == 409 and "also move its documents" in r.json()["detail"]
    assert _ok(admin.post(f"/projects/{proj['id']}/move", json={"group_id": city["group_id"]}))["moved"] == \
        {"documents": 1, "customers": 1, "projects": 1}
    # bulk: two unused customers, one unknown id -> all or nothing
    a = _ok(ops["client"].post("/customers", json={"name": unique("Bulk A")}), 201)
    b = _ok(ops["client"].post("/customers", json={"name": unique("Bulk B")}), 201)
    bad = admin.post("/customers/move", json={"ids": [a["id"], "00000000-0000-0000-0000-000000000000"], "group_id": city["group_id"]})
    assert bad.status_code == 404 and ops["client"].get(f"/customers/{a['id']}").status_code == 200
    assert _ok(admin.post("/customers/move", json={"ids": [a["id"], b["id"]], "group_id": city["group_id"]}))["moved"]["customers"] == 2
    assert {c["id"] for c in _ok(city["client"].get("/customers"))} >= {a["id"], b["id"]}


def test_move_products_single_and_bulk(admin, depts):
    ops, city = depts["ops"], depts["city"]
    token = unique("mvprod")
    p1 = _ok(ops["client"].post("/products", json={"name": f"Product {token}", "unit_price": "2"}), 201)
    p2 = _ok(ops["client"].post("/products", json={"name": f"Other {token}", "unit_price": "2"}), 201)
    assert poll(lambda: p1["id"] in _hits(ops["client"], token, "products"))
    assert _ok(admin.post(f"/products/{p1['id']}/move", json={"group_id": city["group_id"]}))["moved"] == {"products": 1}
    assert ops["client"].get(f"/products/{p1['id']}").status_code == 404
    assert poll(lambda: p1["id"] in _hits(city["client"], token, "products"))
    assert poll(lambda: p1["id"] not in _hits(ops["client"], token, "products"))
    assert _ok(admin.post("/products/move", json={"ids": [p1["id"], p2["id"]], "group_id": ops["group_id"]}))["moved"] == {"products": 1}  # p2 is already there
    assert ops["client"].get(f"/products/{p1['id']}").status_code == 200


def test_non_superusers_get_403(admin, depts):
    ops, city = depts["ops"], depts["city"]
    prod = _ok(ops["client"].post("/products", json={"name": unique("Prod 403"), "unit_price": "1"}), 201)
    cust = _ok(ops["client"].post("/customers", json={"name": unique("Cust 403")}), 201)
    # even a group admin (here of CityMart, and member of both) may not move data across groups
    _ok(admin.post(f"/admin/groups/{city['group_id']}/members", json={"username": ops["username"], "is_group_admin": True}), 201)
    c = _login(ops["username"])
    for path, body in ((f"/customers/{cust['id']}/move", {"group_id": city["group_id"]}),
                       ("/customers/move", {"ids": [cust["id"]], "group_id": city["group_id"]}),
                       (f"/products/{prod['id']}/move", {"group_id": city["group_id"]}),
                       ("/products/move", {"ids": [prod["id"]], "group_id": city["group_id"]})):
        assert c.post(path, json=body).status_code == 403, path
    assert c.get("/documents/unassigned-counts").status_code == 403
    assert c.get("/products/unassigned-counts").status_code == 403
    c.close()


def test_unassigned_counts_for_superuser(admin):
    docs = _ok(admin.get("/documents/unassigned-counts"))
    assert set(docs) == {"documents", "customers", "projects"} and all(isinstance(v, int) for v in docs.values())
    assert set(_ok(admin.get("/products/unassigned-counts"))) == {"products"}
