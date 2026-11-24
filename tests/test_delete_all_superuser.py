"""
Loop iteration 29 — the DEV-ONLY "Delete ALL" endpoints are superuser-only,
end to end through Nginx, and ?group_id= limits a wipe to one group.
A group editor with every right (documents-delete included) gets 403 on all
five and nothing changes. The no-parameter / =none wipes are covered by the
unit tests of both services and tests/test_ui_delete_all.py (they would wipe
other tests' rows here). Creates groups, so loop-test stack only.
"""
from conftest import destructive, unique
from test_group_move import _doc_set, _ok, depts  # noqa: F401

pytestmark = destructive
PATHS = ["/products", "/documents", "/customers", "/projects", "/companies"]


def _seed(client, token):
    _doc_set(client, token)
    _ok(client.post("/products", json={"name": f"Product {token}"}), 201)


def test_non_superuser_gets_403_and_nothing_changes(depts):
    ops = depts["ops"]["client"]
    _seed(ops, unique("dax"))
    before = {p: len(_ok(ops.get(p))) for p in PATHS}
    for path in PATHS:
        r = ops.delete(path)
        assert r.status_code == 403, (path, r.text)
    assert ops.get("/products/dev-config").json() == {"delete_all_enabled": False}
    assert {p: len(_ok(ops.get(p))) for p in PATHS} == before


def test_superuser_wipes_only_the_chosen_group(admin, depts):
    ops, city = depts["ops"], depts["city"]
    _seed(ops["client"], unique("daops"))
    _seed(city["client"], unique("dacity"))
    assert admin.get("/products/dev-config").json() == {"delete_all_enabled": True}
    city_before = {p: len(_ok(city["client"].get(p))) for p in PATHS[:4]}
    for path in PATHS[:4]:
        assert admin.delete(f"{path}?group_id=not-a-uuid").status_code == 422
        r = admin.delete(f"{path}?group_id={ops['group_id']}")
        assert r.status_code == 200 and r.json()["deleted"] >= 1, (path, r.text)
        assert _ok(ops["client"].get(path)) == []
    assert {p: len(_ok(city["client"].get(p))) for p in PATHS[:4]} == city_before
