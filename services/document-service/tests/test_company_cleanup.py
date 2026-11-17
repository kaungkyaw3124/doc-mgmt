"""
Loop iteration 12 — company form cleanup:
  * the company "Signer position" is gone from the API and the exports; the
    Supplier Position is the selected MD's own (now required) position;
  * migration 0007 fills NULL/blank MD positions;
  * short names are 2–6 letters/digits, stored uppercase — old invalid ones
    are left alone and keep numbering documents as before.

Reuses test_manual_end_user.py's SQLite + real-HTTP fixture and
test_supplier_position_e2e.py's export readers.
"""
import importlib.util
import pathlib
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from app import models
from tests.test_manual_end_user import ctx  # noqa: F401  (ctx is a fixture)
from tests.test_supplier_position_e2e import _quotation, _supplier_block, _xlsx_rows

COMPANY = {"name": "Winterfell Co.,Ltd", "short_name": "WF", "address": "Mayangon",
           "contact_no": "+9599", "support_email": "a@b.example"}


def _company(client, **body):
    return client.post("/companies", json={**COMPANY, **body})


def test_company_create_and_update_ignore_position(ctx):
    client, Session = ctx
    r = _company(client)
    assert r.status_code == 201, r.text
    assert "position" not in r.json()
    r = _company(client, position="Director")  # an old client still sending it
    assert r.status_code == 201 and "position" not in r.json()
    cid = r.json()["id"]
    assert Session().get(models.Company, uuid.UUID(cid)).position is None  # not stored either
    r = client.patch(f"/companies/{cid}", json={"position": "CEO", "address": "Hlaing"})
    assert r.status_code == 200 and "position" not in r.json() and r.json()["address"] == "Hlaing"
    assert "position" not in client.get(f"/companies/{cid}").json()

    # a company position already in the DB never reaches the exports
    db = Session()
    db.get(models.Company, uuid.UUID(cid)).position = "Director"
    db.commit()
    md = client.post(f"/companies/{cid}/directors", json={"name": "Aung Aung", "position": "Founder"}).json()["id"]
    doc = _quotation(client, cid, md)
    block = _supplier_block(client, doc)
    assert "Founder" in block and "Director" not in block
    rows = _xlsx_rows(client, doc)
    assert next(r for r in rows if r[0] == "Position")[1] == "Founder"
    assert "Director" not in [v for r in rows for v in r]


def test_md_position_is_required_and_defaults_on_create(ctx):
    client, _ = ctx
    cid = _company(client).json()["id"]
    url = f"/companies/{cid}/directors"
    assert client.post(url, json={"name": "A"}).json()["position"] == "Managing Director"
    assert client.post(url, json={"name": "B", "position": "  CEO "}).json()["position"] == "CEO"
    for blank in ("", "   ", None):
        r = client.post(url, json={"name": "C", "position": blank})
        assert r.status_code == 422 and "Position is required" in r.json()["detail"]
    did = client.post(url, json={"name": "D"}).json()["id"]
    for blank in ("", "   ", None):
        assert client.patch(f"{url}/{did}", json={"position": blank}).status_code == 422
    assert client.patch(f"{url}/{did}", json={"name": "D2"}).json()["position"] == "Managing Director"

    doc = _quotation(client, cid, did)
    assert "Managing Director" in _supplier_block(client, doc)
    assert next(r for r in _xlsx_rows(client, doc) if r[0] == "Position")[1] == "Managing Director"


@pytest.mark.parametrize("short_name", ["W", "TOOLONG7", "Winterfell Co.,Ltd", "W F", "WF-1"])
def test_invalid_short_names_are_rejected(ctx, short_name):
    client, _ = ctx
    r = _company(client, short_name=short_name)
    assert r.status_code == 422 and "2–6 letters or digits" in r.json()["detail"]
    cid = _company(client).json()["id"]
    r = client.patch(f"/companies/{cid}", json={"short_name": short_name})
    assert r.status_code == 422 and "2–6 letters or digits" in r.json()["detail"]


def test_short_name_is_trimmed_and_uppercased(ctx):
    client, _ = ctx
    r = _company(client, short_name=" wf ")
    assert r.status_code == 201 and r.json()["short_name"] == "WF"
    cid = r.json()["id"]
    assert client.patch(f"/companies/{cid}", json={"short_name": "ab12cd"}).json()["short_name"] == "AB12CD"
    assert client.patch(f"/companies/{cid}", json={"short_name": ""}).json()["short_name"] is None  # still optional


def test_existing_invalid_short_name_is_kept_and_still_numbers_documents(ctx):
    client, Session = ctx
    cid = _company(client).json()["id"]
    db = Session()
    db.get(models.Company, uuid.UUID(cid)).short_name = "Winterfell Co.,Ltd"  # as saved before iteration 12
    db.commit()
    # editing other fields leaves it untouched; numbering uses it exactly as before
    assert client.patch(f"/companies/{cid}", json={"address": "Hlaing"}).json()["short_name"] == "Winterfell Co.,Ltd"
    r = client.post("/documents", json={"doc_type": "quotation", "company_id": cid,
                                        "issue_date": str(date(2026, 10, 30)), "items": []})
    assert r.status_code == 201
    today = date.today()
    assert r.json()["doc_number"] == f"WINTERFELL CO.,LTD-{today:%Y%m%d}/001"


def _load_0007():
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_md_position_default.py"
    spec = importlib.util.spec_from_file_location("m0007", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0007_fills_md_positions_and_runs_twice_safely():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    m0007 = _load_0007()
    assert m0007.down_revision == "0006_customer_organization" and len(m0007.revision) <= 32

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE companies (id VARCHAR PRIMARY KEY, name VARCHAR, position VARCHAR)"))
        conn.execute(text("CREATE TABLE company_directors (id VARCHAR PRIMARY KEY, company_id VARCHAR, position VARCHAR)"))
        conn.execute(text("INSERT INTO companies VALUES ('c1', 'Winterfell', ' Director '), "
                          "('c2', 'Winterfell Co.,Ltd', 'winterfell co.,ltd'), ('c3', 'Blank Co', '  '), ('c4', 'None Co', NULL)"))
        conn.execute(text("INSERT INTO company_directors VALUES ('d1', 'c1', NULL), ('d2', 'c2', NULL), "
                          "('d3', 'c3', ''), ('d4', 'c4', '   '), ('d5', 'c1', 'Founder')"))
        with Operations.context(MigrationContext.configure(conn)):
            m0007.upgrade()
            m0007.upgrade()  # second run: nothing left to fill -> no-op
        got = dict(conn.execute(text("SELECT id, position FROM company_directors")).all())
    assert got == {"d1": "Director", "d2": "Managing Director", "d3": "Managing Director",
                   "d4": "Managing Director", "d5": "Founder"}
