"""
Loop iteration 30 — a Unit on products (No, M, Set …): normalizing, the
create/edit field, the bulk-import column + template, POST /products/set-unit
and the guarded 0004 migration. Real API + ORM on SQLite (test_group_scope.py).
"""
import importlib.util
import io
import pathlib

import openpyxl
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.schemas import normalize_unit
from tests.test_group_scope import AS_CITY, AS_OPS, _product, ctx  # noqa: F401


@pytest.mark.parametrize("raw,stored", [
    (None, "No"), ("", "No"), ("  ", "No"), ("no", "No"), ("NOS", "No"), ("Number", "No"),
    ("m", "M"), ("Meter", "M"), ("metres", "M"), ("pc", "Pcs"), ("Pieces", "Pcs"),
    ("feet", "Ft"), ("FOOT", "Ft"), ("kgs", "Kg"), ("Set", "Set"), (" Roll ", "Roll"), ("box", "Box"), ("LOT", "Lot"), ("Drum", "Drum"), ("drum", "drum"),
])
def test_normalizer(raw, stored):
    assert normalize_unit(raw) == stored


def test_create_edit_default_and_validation(ctx):
    client, _ = ctx
    assert _product(client, AS_OPS, "Plain")["unit"] == "No"
    cable = _product(client, AS_OPS, "Cable 2.5mm", unit="meter")
    assert cable["unit"] == "M"
    r = client.patch(f"/products/{cable['id']}", json={"unit": "Roll"}, headers=AS_OPS)
    assert r.status_code == 200 and r.json()["unit"] == "Roll"
    assert client.post("/products", json={"name": "Long", "unit": "x" * 21}, headers=AS_OPS).status_code == 422
    assert client.patch(f"/products/{cable['id']}", json={"unit": "x" * 21}, headers=AS_OPS).status_code == 422
    assert client.get(f"/products/{cable['id']}", headers=AS_OPS).json()["unit"] == "Roll"


def test_unit_is_not_part_of_the_duplicate_key(ctx):
    client, _ = ctx
    _product(client, AS_OPS, "Cable", description="2.5mm", unit="M")
    r = client.post("/products", json={"name": "Cable", "description": "2.5mm", "unit": "Roll"}, headers=AS_OPS)
    assert r.status_code == 409, r.text


def _xlsx(header, rows):
    wb = openpyxl.Workbook()
    wb.active.append(header)
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return ("p.xlsx", buf.getvalue(), "application/octet-stream")


def test_bulk_import_unit_column(ctx):
    client, _ = ctx
    sheet = _xlsx(["Name", "UNIT"], [["Wire", "meter"], ["Bolt", None], ["Tape", "Roll"], ["Odd", "y" * 21]])
    r = client.post("/products/bulk-import", files={"excel_file": sheet}, headers=AS_OPS)
    assert r.status_code == 200, r.text
    assert [c["name"] for c in r.json()["created"]] == ["Wire", "Bolt", "Tape"]
    assert r.json()["skipped"] == [{"row": 4, "name": "Odd", "reason": "unit is longer than 20 characters", "existing_sku": ""}]
    units = {p["name"]: p["unit"] for p in client.get("/products", headers=AS_OPS).json()}
    assert units == {"Wire": "M", "Bolt": "No", "Tape": "Roll"}


def test_bulk_import_without_unit_column_defaults_to_no(ctx):
    client, _ = ctx
    r = client.post("/products/bulk-import", files={"excel_file": _xlsx(["Name"], [["Lamp"]])}, headers=AS_OPS)
    assert r.status_code == 200 and len(r.json()["created"]) == 1
    assert client.get("/products", headers=AS_OPS).json()[0]["unit"] == "No"


def test_template_has_unit_header(ctx):
    client, _ = ctx
    ws = openpyxl.load_workbook(io.BytesIO(client.get("/products/bulk-import-template", headers=AS_OPS).content)).active
    assert [c.value for c in ws[1]] == ["Name", "Category", "Price", "Description", "Unit"]
    assert ws["E2"].value == "No"


def test_set_unit_is_group_scoped_and_all_or_nothing(ctx):
    client, _ = ctx
    a, b = _product(client, AS_OPS, "Cable A"), _product(client, AS_OPS, "Cable B")
    theirs = _product(client, AS_CITY, "Cable C")
    r = client.post("/products/set-unit", json={"ids": [a["id"], theirs["id"]], "unit": "M"}, headers=AS_OPS)
    assert r.status_code == 404
    assert {p["unit"] for p in client.get("/products", headers=AS_OPS).json()} == {"No"}
    assert client.get(f"/products/{theirs['id']}", headers=AS_CITY).json()["unit"] == "No"
    r = client.post("/products/set-unit", json={"ids": [a["id"], b["id"]], "unit": "metre"}, headers=AS_OPS)
    assert r.status_code == 200 and r.json() == {"updated": 2}
    rows = {p["name"]: p for p in client.get("/products", headers=AS_OPS).json()}
    assert rows["Cable A"]["unit"] == rows["Cable B"]["unit"] == "M"
    assert rows["Cable A"]["version"] == 2  # an edit like any other: stale forms get 409
    view = {**AS_OPS, "X-Access-Level": "view"}
    assert client.post("/products/set-unit", json={"ids": [a["id"]], "unit": "Set"}, headers=view).status_code == 403
    assert client.post("/products/set-unit", json={"ids": [a["id"]], "unit": "z" * 21}, headers=AS_OPS).status_code == 422


def test_sub_items_carry_the_unit(ctx):
    client, _ = ctx
    parent, part = _product(client, AS_OPS, "Kit"), _product(client, AS_OPS, "Wire", unit="M")
    assert client.post(f"/products/{parent['id']}/sub-items", json={"product_id": part["id"]}, headers=AS_OPS).json()["unit"] == "M"
    assert client.get(f"/products/{parent['id']}/sub-items", headers=AS_OPS).json()[0]["unit"] == "M"


def test_0004_migration_is_guarded():
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0004_product_unit.py"
    spec = importlib.util.spec_from_file_location("c0004", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0003_duplicate_dismissals"
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR)"))
        conn.execute(text("INSERT INTO products (name) VALUES ('old')"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.upgrade()  # runs again on every startup
            assert conn.execute(text("SELECT unit FROM products")).scalar() == "No"
            mod.downgrade()
            assert {c["name"] for c in inspect(conn).get_columns("products")} == {"id", "name"}
