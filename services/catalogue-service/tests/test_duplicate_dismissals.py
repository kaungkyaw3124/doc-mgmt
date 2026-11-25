"""
Loop iteration 27 — "Not a duplicate": same-name groups can be dismissed on
the duplicates page; the dismissal holds only while both products are
unchanged, can be restored, never applies to exact duplicates, and is per
group. Real HTTP API + ORM on SQLite, like test_duplicates.py.
"""
import uuid

from app import models
from tests.scope_headers import ONE_GROUP, TEST_GROUP
from tests.test_duplicates import ctx, _ok  # noqa: F401  (ctx is a fixture)

GID = uuid.UUID(TEST_GROUP)
OTHER = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
AS_OTHER = {"X-Group-Ids": str(OTHER), "X-Is-Superuser": "0"}
AS_SUPER = {"X-Group-Ids": f"{GID},{OTHER}", "X-Is-Superuser": "1"}


def _add(Session, *rows):
    db = Session()
    products = [models.Product(id=uuid.uuid4(), group_id=g, sku=sku, name=n, description=d) for g, sku, n, d in rows]
    db.add_all(products)
    db.commit()
    ids = [str(p.id) for p in products]
    db.close()
    return ids


def _same_name(client, headers=None):
    r = _ok(client.get("/products/duplicates", headers=headers or {}), 200)
    return [[p["sku"] for p in g] for g in r["same_name_groups"]], r["hidden_count"]


def _dismiss(client, ids, headers=None):
    return client.post("/products/duplicates/dismiss", json={"product_ids": ids}, headers=headers or {})


def test_dismiss_hides_edit_brings_back_restore_brings_back(ctx):
    client, Session, _ = ctx
    a, b = _add(Session, (GID, "HD-1", "External Hard Disk", "1 TB"), (GID, "HD-2", "External hard disk ", "Seagate 4TB"))
    assert _same_name(client) == ([["HD-1", "HD-2"]], 0)

    assert _ok(_dismiss(client, [a, b]), 200) == {"dismissed_pairs": 1}
    assert _same_name(client) == ([], 1)
    assert _ok(_dismiss(client, [b, a]), 200) == {"dismissed_pairs": 1}  # idempotent
    db = Session()
    assert db.query(models.ProductDuplicateDismissal).count() == 1
    db.close()
    hidden = _ok(client.get("/products/duplicates/dismissed"), 200)
    assert len(hidden) == 1 and {p["sku"] for p in hidden[0]["products"]} == {"HD-1", "HD-2"}

    # editing a description makes the dismissal stale -> listed again, not in "hidden"
    version = _ok(client.get(f"/products/{a}"), 200)["version"]
    _ok(client.patch(f"/products/{a}", json={"description": "1 TB USB-C", "version": version}), 200)
    assert _same_name(client) == ([["HD-1", "HD-2"]], 0)
    assert _ok(client.get("/products/duplicates/dismissed"), 200) == []

    # dismiss again (fingerprints refreshed), then restore
    _ok(_dismiss(client, [a, b]), 200)
    assert _same_name(client) == ([], 1)
    dismissal_id = _ok(client.get("/products/duplicates/dismissed"), 200)[0]["id"]
    assert client.delete(f"/products/duplicates/dismissed/{dismissal_id}").status_code == 204
    assert _same_name(client) == ([["HD-1", "HD-2"]], 0)
    assert client.delete(f"/products/duplicates/dismissed/{dismissal_id}").status_code == 404


def test_group_of_three_then_a_fourth_shows_the_whole_group(ctx):
    client, Session, _ = ctx
    ids = _add(Session, (GID, "E-1", "Edge AI Device", "Jetson Orin Nano"), (GID, "E-2", "Edge AI Device", "Orin NX"),
               (GID, "E-3", "Edge AI Device", "Orin AGX"))
    assert _ok(_dismiss(client, ids), 200) == {"dismissed_pairs": 3}
    assert _same_name(client) == ([], 1)
    _add(Session, (GID, "E-4", "Edge AI Device", "Thor"))
    assert _same_name(client) == ([["E-1", "E-2", "E-3", "E-4"]], 0)  # simpler choice: the whole group comes back


def test_exact_duplicates_are_not_dismissable_and_stay_listed(ctx):
    client, Session, _ = ctx
    a, b, c = _add(Session, (GID, "C-1", "Chair", "Red"), (GID, "C-2", "chair", "red"), (GID, "C-3", "Chair", "Blue"))
    assert _dismiss(client, [a, b]).status_code == 422
    assert _ok(_dismiss(client, [a, b, c]), 200) == {"dismissed_pairs": 2}  # only the different-description pairs
    r = _ok(client.get("/products/duplicates"), 200)
    assert [[p["sku"] for p in g] for g in r["exact_groups"]] == [["C-1", "C-2"]]
    assert r["same_name_groups"] == [] and r["hidden_count"] == 1


def test_bad_requests(ctx):
    client, Session, _ = ctx
    a, b, x = _add(Session, (GID, "N-1", "Mouse", "Wired"), (GID, "N-2", "Keyboard", "Wireless"), (OTHER, "O-1", "Mouse", "Wireless"))
    assert _dismiss(client, [a]).status_code == 422                      # one id
    assert _dismiss(client, [a, b]).status_code == 422                   # different names
    assert _dismiss(client, [a, str(uuid.uuid4())]).status_code == 404   # unknown
    assert _dismiss(client, [a, x], AS_SUPER).status_code == 422          # two groups
    assert _dismiss(client, [a, b], {"X-Access-Level": "view"}).status_code == 403


def test_group_scope(ctx):
    client, Session, _ = ctx
    a, b = _add(Session, (GID, "G-1", "Router", "4 port"), (GID, "G-2", "Router", "8 port"))
    c, d = _add(Session, (OTHER, "H-1", "Router", "4 port"), (OTHER, "H-2", "Router", "8 port"))
    assert _dismiss(client, [a, b], AS_OTHER).status_code == 404  # another group's products
    _ok(_dismiss(client, [a, b]), 200)
    assert _same_name(client) == ([], 1)
    assert _same_name(client, AS_OTHER) == ([["H-1", "H-2"]], 0)  # same names in the other group still warn
    assert _ok(client.get("/products/duplicates/dismissed", headers=AS_OTHER), 200) == []
    dismissal_id = _ok(client.get("/products/duplicates/dismissed"), 200)[0]["id"]
    assert client.delete(f"/products/duplicates/dismissed/{dismissal_id}", headers=AS_OTHER).status_code == 404
    _ok(_dismiss(client, [c, d], AS_SUPER), 200)  # superuser acts on any group
    assert _same_name(client, AS_OTHER) == ([], 1)
    assert client.delete(f"/products/duplicates/dismissed/{dismissal_id}", headers=AS_SUPER).status_code == 204
    assert _same_name(client, ONE_GROUP) == ([["G-1", "G-2"]], 0)
