"""
Loop iteration 9 (part B) — saved customers get an optional Organization,
separate from their Name. Blank -> NULL (never a copy of the name), and the
quotation's END USER block prints `organization or name`, the same rule
one-time (manual) customers follow since iteration 7.

Reuses test_manual_end_user.py's SQLite + real-HTTP fixture and END USER
readers.
"""
import uuid

from sqlalchemy import create_engine, inspect, text

from app import models
from tests.test_manual_end_user import MANUAL, _create, _end_user_pdf, _end_user_xlsx, ctx  # noqa: F401  (ctx is a fixture)


def _customer(client, **body):
    r = client.post("/customers", json={"name": "U Aung Aung", **body})
    assert r.status_code == 201, r.text
    return r.json()


def test_organization_is_stored_trimmed_and_blank_means_null(ctx):
    client, Session = ctx
    assert _customer(client, organization="  Golden Lotus Co., Ltd.  ")["organization"] == "Golden Lotus Co., Ltd."
    assert _customer(client)["organization"] is None
    blank = _customer(client, organization="   ")
    assert blank["organization"] is None

    cid = _customer(client, organization="Old Org")["id"]
    r = client.patch(f"/customers/{cid}", json={"organization": ""})
    assert r.status_code == 200 and r.json()["organization"] is None
    r = client.patch(f"/customers/{cid}", json={"name": "Renamed"})  # org stays NULL, not a copy of the name
    assert r.json()["organization"] is None
    assert Session().get(models.Customer, uuid.UUID(blank["id"])).organization is None


def test_exports_print_the_organization_separately(ctx):
    client, _ = ctx
    cid = _customer(client, organization="Golden Lotus Co., Ltd.")["id"]
    doc_id = _create(client, customer_id=cid).json()["id"]
    pdf = _end_user_pdf(client, doc_id)
    assert "U Aung Aung" in pdf.split("Quotation ID", 1)[0]  # Name row
    assert "Organization Golden Lotus Co., Ltd." in pdf
    xlsx = _end_user_xlsx(client, doc_id)
    assert xlsx["Customer Name"] == "U Aung Aung"
    assert xlsx["Organization"] == "Golden Lotus Co., Ltd."


def test_without_organization_the_name_prints_in_both_rows(ctx):
    client, _ = ctx
    doc_id = _create(client, customer_id=_customer(client)["id"]).json()["id"]
    assert "Organization U Aung Aung" in _end_user_pdf(client, doc_id)
    xlsx = _end_user_xlsx(client, doc_id)
    assert xlsx["Customer Name"] == xlsx["Organization"] == "U Aung Aung"


def test_manual_customer_output_is_unchanged(ctx):
    client, _ = ctx
    doc_id = _create(client, **MANUAL).json()["id"]
    assert "Organization Golden Lotus Trading" in _end_user_pdf(client, doc_id)
    assert _end_user_xlsx(client, doc_id)["Organization"] == "Golden Lotus Trading"


def test_an_old_customer_row_without_organization_still_exports_its_name(ctx):
    client, Session = ctx
    db = Session()
    old = models.Customer(name="Legacy Buyer Ltd.")  # as it exists before 0006: no organization value
    db.add(old)
    db.commit()
    doc_id = _create(client, customer_id=str(old.id)).json()["id"]
    assert "Organization Legacy Buyer Ltd." in _end_user_pdf(client, doc_id)
    assert _end_user_xlsx(client, doc_id)["Organization"] == "Legacy Buyer Ltd."


def test_0006_migration_runs_twice_safely_and_keeps_rows():
    import importlib.util
    import pathlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0006_customer_organization.py"
    spec = importlib.util.spec_from_file_location("m0006", path)
    m0006 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m0006)
    assert m0006.down_revision == "0005_document_manual_end_user" and len(m0006.revision) <= 32

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE customers (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"))
        conn.execute(text("INSERT INTO customers VALUES ('c1', 'Old Customer')"))
        with Operations.context(MigrationContext.configure(conn)):
            m0006.upgrade()
            m0006.upgrade()  # second run: column exists -> no-op
        assert [c["name"] for c in inspect(conn).get_columns("customers")] == ["id", "name", "organization"]
        assert conn.execute(text("SELECT name, organization FROM customers")).all() == [("Old Customer", None)]
