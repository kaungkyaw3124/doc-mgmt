"""
Loop iteration 19 — department data separation, end to end through Nginx.

Two departments (Operations, CityMart), an Editor-like user in each, one user
in both, one user in no group and the superuser admin. An Operations user's
documents, customers, projects and products must be invisible to CityMart
everywhere — lists, direct ids (404), trash, duplicates, exports, file URLs,
audit log, search — and the other way round; the superuser sees both.

Creates groups and users, which can't be fully removed again, so it only
runs against the throwaway loop-test stack (same gate as the restore tests).
"""
import httpx
import pytest

from conftest import BASE_URL, auth_headers, destructive, poll, unique

pytestmark = destructive

SERVICES = ("documents", "products", "search", "audit-log", "documents-delete")
PASSWORD = "a long throwaway password 123"


def _ok(resp, code=200):
    assert resp.status_code == code, f"{resp.request.method} {resp.request.url}: {resp.status_code} {resp.text}"
    return resp.json() if resp.content else None


def _login(username):
    resp = httpx.post(f"{BASE_URL}/auth/login", json={"username": username, "password": PASSWORD}, timeout=60)
    token = _ok(resp)["access_token"]
    return httpx.Client(base_url=BASE_URL, timeout=60.0, headers=auth_headers(token))


@pytest.fixture(scope="module")
def world(admin):
    tag = unique("iso")
    groups, roles = {}, {}
    for dept in ("Operations", "CityMart"):
        g = _ok(admin.post("/admin/groups", json={"name": f"{dept} {tag}"}), 201)
        role = _ok(admin.post(f"/admin/groups/{g['id']}/roles", json={"name": "Dept Editor"}), 201)
        for service in SERVICES:
            _ok(admin.post(f"/admin/roles/{role['id']}/access", json={"service_name": service, "access_level": "edit"}), 201)
        groups[dept], roles[dept] = g["id"], role["id"]

    users = {}
    for key, dept in (("ops", "Operations"), ("city", "CityMart"), ("both", "Operations")):
        name = f"{key}-{tag}"
        _ok(admin.post(f"/admin/groups/{groups[dept]}/users",
                       json={"username": name, "password": PASSWORD, "role_id": roles[dept]}), 201)
        users[key] = name
    _ok(admin.post(f"/admin/groups/{groups['CityMart']}/members", json={"username": users["both"]}), 201)
    _ok(admin.post(f"/admin/roles/{roles['CityMart']}/assign", json={"username": users["both"]}), 201)
    users["nobody"] = f"nobody-{tag}"
    _ok(admin.post("/auth/users", json={"username": users["nobody"], "password": PASSWORD, "is_superuser": False}), 201)

    clients = {k: _login(v) for k, v in users.items()}
    category = _ok(admin.post("/categories", json={"name": unique("iso-cat"), "short_term": "I" + tag[-6:].upper()}), 201)
    company = _ok(admin.post("/companies", json={"name": unique("Iso Co"), "short_name": "IS"}), 201)
    made = {}

    def make(dept_key, group_key):
        c = clients[dept_key]
        token = unique(f"tok{dept_key}")
        cust = _ok(c.post("/customers", json={"name": f"Customer {token}"}), 201)
        proj = _ok(c.post("/projects", json={"name": f"Project {token}"}), 201)
        prod = _ok(c.post("/products", json={"name": f"Product {token}", "category": category["name"], "unit_price": "4.00"}), 201)
        _ok(c.post(f"/products/{prod['id']}/file", files={"file": ("spec.pdf", b"%PDF-1.4 iso", "application/pdf")}))
        doc = _ok(c.post("/documents", json={
            "doc_type": "quotation", "customer_id": cust["id"], "project_id": proj["id"], "company_id": company["id"],
            "items": [{"product_id": prod["id"], "quantity": "1"}, {"description": f"line {token}", "quantity": "1", "unit_price": "3"}],
        }), 201)
        _ok(c.post(f"/documents/{doc['id']}/file", files={"file": ("note.txt", b"hello", "text/plain")}))
        assert {r["group_id"] for r in (cust, proj, prod, doc)} == {groups[group_key]}
        made[dept_key] = {"token": token, "customer": cust, "project": proj, "product": prod, "document": doc}

    make("ops", "Operations")
    make("city", "CityMart")
    yield {"groups": groups, "users": users, "clients": clients, "made": made, "company": company, "category": category}

    for key in ("ops", "city"):
        rows = made.get(key, {})
        if "document" in rows:
            admin.delete(f"/documents/{rows['document']['id']}")
        for path, kind in (("/customers", "customer"), ("/projects", "project"), ("/products", "product")):
            if kind in rows:
                admin.delete(f"{path}/{rows[kind]['id']}")
    for c in clients.values():
        c.close()
    for u in admin.get("/admin/users").json():
        if u["username"] in users.values():
            admin.patch(f"/admin/users/{u['id']}/active", json={"is_active": False})


ENTITIES = {"document": "/documents", "customer": "/customers", "project": "/projects", "product": "/products"}


@pytest.mark.parametrize("owner,other", [("ops", "city"), ("city", "ops")])
@pytest.mark.parametrize("entity", list(ENTITIES))
def test_lists_and_direct_access_are_separated(world, owner, other, entity):
    path, row = ENTITIES[entity], world["made"][owner][entity]
    mine, theirs = world["clients"][owner], world["clients"][other]
    assert row["id"] in {r["id"] for r in _ok(mine.get(path))}
    assert row["id"] not in {r["id"] for r in _ok(theirs.get(path))}
    assert {r["group_id"] for r in _ok(theirs.get(path))} <= {world["groups"]["Operations" if other == "ops" else "CityMart"]}

    patch_body = {"items": []} if entity == "document" else {"name": "hijacked"}
    for method, url, kw in (
        ("get", f"{path}/{row['id']}", {}),
        ("patch", f"{path}/{row['id']}", {"json": patch_body}),
        ("patch", f"{path}/{row['id']}/trash", {}),
        ("patch", f"{path}/{row['id']}/restore", {}),
        ("delete", f"{path}/{row['id']}", {}),
    ):
        assert getattr(theirs, method)(url, **kw).status_code == 404, f"{method} {url}"
    assert _ok(mine.get(f"{path}/{row['id']}"))["id"] == row["id"]


@pytest.mark.parametrize("owner,other", [("ops", "city"), ("city", "ops")])
def test_trash_is_separated(world, owner, other):
    mine, theirs = world["clients"][owner], world["clients"][other]
    tmp = _ok(mine.post("/customers", json={"name": unique("trash-me")}), 201)
    _ok(mine.patch(f"/customers/{tmp['id']}/trash"))
    assert tmp["id"] in {r["id"] for r in _ok(mine.get("/customers/trash"))}
    assert tmp["id"] not in {r["id"] for r in _ok(theirs.get("/customers/trash"))}
    _ok(mine.delete(f"/customers/{tmp['id']}"), 204)


@pytest.mark.parametrize("owner,other", [("ops", "city"), ("city", "ops")])
def test_exports_files_audit_and_product_extras_are_separated(world, owner, other):
    made = world["made"][owner]
    mine, theirs = world["clients"][owner], world["clients"][other]
    did, pid = made["document"]["id"], made["product"]["id"]
    for url in (f"/documents/{did}/export/quotation-pdf", f"/documents/{did}/export/quotation",
                f"/documents/{did}/file-url", f"/documents/{did}/audit-log",
                f"/products/{pid}/file-url", f"/products/{pid}/sub-items", f"/products/{pid}/file-content"):
        assert theirs.get(url).status_code == 404, url
    pdf = mine.get(f"/documents/{did}/export/quotation-pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert mine.get(f"/documents/{did}/export/quotation").status_code == 200
    assert mine.get(f"/documents/{did}/file-url").status_code == 200
    assert mine.get(f"/documents/{did}/audit-log").status_code == 200
    assert mine.get(f"/products/{pid}/file-url").status_code == 200

    name = made["product"]["name"]
    assert _ok(theirs.get("/products/duplicate-check", params={"name": name}))["exact"] == []
    assert len(_ok(mine.get("/products/duplicate-check", params={"name": name}))["exact"]) == 1
    flat = [p["id"] for groups in _ok(theirs.get("/products/duplicates")).values() for g in groups for p in g]
    assert pid not in flat
    assert _ok(theirs.get(f"/documents/by-customer/{made['customer']['id']}")) == []
    assert _ok(theirs.get(f"/documents/by-product/{pid}")) == []


@pytest.mark.parametrize("owner,other", [("ops", "city"), ("city", "ops")])
def test_search_is_separated(world, owner, other):
    token = world["made"][owner]["token"]
    mine, theirs = world["clients"][owner], world["clients"][other]

    def hits(client, index):
        return [h["id"] for h in _ok(client.get("/search", params={"q": token, "index": index})).get(index, [])]

    assert poll(lambda: world["made"][owner]["document"]["id"] in hits(mine, "documents")), "document not searchable"
    assert poll(lambda: world["made"][owner]["product"]["id"] in hits(mine, "products")), "product not searchable"
    assert hits(theirs, "documents") == [] and hits(theirs, "products") == []


def test_superuser_and_multi_group_user_see_both(world, admin):
    for client in (admin, world["clients"]["both"]):
        for entity, path in ENTITIES.items():
            ids = {r["id"] for r in _ok(client.get(path))}
            assert {world["made"]["ops"][entity]["id"], world["made"]["city"][entity]["id"]} <= ids, (path, client)


def test_create_rules(world, admin):
    groups, clients = world["groups"], world["clients"]
    # in no group: refused (no group = no role = no access to the services at all)
    assert clients["nobody"].get("/documents").status_code == 403
    assert clients["nobody"].post("/customers", json={"name": "x"}).status_code == 403
    # several groups: must choose; a foreign group is refused; the superuser must choose too
    assert clients["both"].post("/customers", json={"name": unique("x")}).status_code == 422
    assert clients["ops"].post("/customers", json={"name": unique("x"), "group_id": groups["CityMart"]}).status_code == 403
    assert admin.post("/products", json={"name": unique("x")}).status_code == 422
    row = _ok(clients["both"].post("/customers", json={"name": unique("x"), "group_id": groups["CityMart"]}), 201)
    assert row["group_id"] == groups["CityMart"]
    _ok(clients["both"].delete(f"/customers/{row['id']}"), 204)


def test_no_cross_group_links(world, admin):
    city_customer = world["made"]["city"]["customer"]
    r = admin.post("/documents", json={"doc_type": "quotation", "group_id": world["groups"]["Operations"],
                                       "customer_id": city_customer["id"]})
    assert r.status_code == 422 and "another group" in r.json()["detail"]
    # an Operations user can't even see it
    r = world["clients"]["ops"].post("/documents", json={"doc_type": "quotation", "customer_id": city_customer["id"]})
    assert r.status_code == 400


def test_client_cannot_spoof_the_scope_headers(world):
    spoof = {"X-Group-Ids": world["groups"]["CityMart"], "X-Is-Superuser": "1"}
    ops = world["clients"]["ops"]
    city_doc = world["made"]["city"]["document"]["id"]
    assert ops.get(f"/documents/{city_doc}", headers=spoof).status_code == 404
    for path in ENTITIES.values():
        ids = {r["id"] for r in _ok(ops.get(path, headers=spoof))}
        assert all(world["made"]["city"][e]["id"] not in ids for e in ENTITIES)
    token = world["made"]["city"]["token"]
    assert _ok(ops.get("/search", params={"q": token}, headers=spoof)).get("documents") == []


def test_shared_data_stays_shared(world):
    for key in ("ops", "city"):
        client = world["clients"][key]
        assert world["company"]["id"] in {c["id"] for c in _ok(client.get("/companies"))}
        assert world["category"]["name"] in {c["name"] for c in _ok(client.get("/categories"))}
    # numbering is per company across both groups: two documents, two numbers
    numbers = {world["made"][k]["document"]["doc_number"] for k in ("ops", "city")}
    assert len(numbers) == 2 and all(n.startswith("IS-") for n in numbers)
