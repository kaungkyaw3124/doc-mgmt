"""Loop iteration 21 — safe simultaneous use, through the gateway against the
real stack (Postgres, so the races are real): unique gapless numbers under
parallel creates, version conflicts (409) for all five entities, Idempotency-Key
replay under parallel repeats, Cache-Control on API answers, and (destructive,
loop-test stack only) counters across a restart, the migration on existing
data, and search catching up after a failed index update via reindex.sh."""
import io
import os
import re
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import openpyxl
import pytest

from conftest import BASE_URL, destructive, poll, unique

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = BASE_URL.removesuffix("/api")
N = 30
CONFLICT = "Someone else changed this. Reload to see their changes."


def dc(*args, check=True):
    cmd = ["docker", "compose", "-f", os.path.join(REPO, "infra/docker-compose.yml")]
    if os.environ.get("COMPOSE_OVERRIDE"):
        cmd += ["-f", os.path.join(REPO, os.environ["COMPOSE_OVERRIDE"])]
    r = subprocess.run(cmd + list(args), capture_output=True, text=True)
    assert not check or r.returncode == 0, r.stderr[-2000:]
    return r


def parallel(fn, n=N):
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(fn, range(n)))


def numbers(values, pattern):
    return sorted(int(re.fullmatch(pattern, v).group(1)) for v in values)


@pytest.fixture
def hdr(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def prefix_category(admin):
    short = "R" + uuid.uuid4().hex[:7].upper()
    r = admin.post("/categories", json={"name": unique("race-cat"), "short_term": short})
    assert r.status_code in (200, 201), r.text
    return r.json()["name"], short


@pytest.fixture
def company(admin):
    short = "Z" + uuid.uuid4().hex[:4].upper()
    r = admin.post("/companies", json={"name": unique("race-co"), "short_name": short})
    assert r.status_code == 201, r.text
    return r.json()


def post(hdr, path, body, **headers):
    return httpx.post(BASE_URL + path, json=body, headers={**hdr, **headers}, timeout=120)


# ---- A1: numbers ---------------------------------------------------------

def test_parallel_product_creates_get_distinct_gapless_skus(hdr, group_id, prefix_category):
    cat, short = prefix_category
    rs = parallel(lambda i: post(hdr, "/products", {"group_id": group_id, "name": unique(f"race-{i}"), "category": cat}))
    assert [r.status_code for r in rs] == [201] * N, [r.text for r in rs if r.status_code != 201][:3]
    assert numbers([r.json()["sku"] for r in rs], rf"{short}-(\d+)") == list(range(1, N + 1))


def test_parallel_documents_get_distinct_gapless_numbers(hdr, group_id, company):
    body = {"group_id": group_id, "doc_type": "quotation", "company_id": company["id"],
            "items": [{"description": "race", "quantity": "1", "unit_price": "1"}]}
    rs = parallel(lambda i: post(hdr, "/documents", body))
    assert [r.status_code for r in rs] == [201] * N, [r.text for r in rs if r.status_code != 201][:3]
    assert numbers([r.json()["doc_number"] for r in rs], rf"{company['short_name']}-\d{{8}}/(\d+)") == list(range(1, N + 1))


def test_simultaneous_create_and_bulk_import_share_one_sequence(hdr, group_id, prefix_category):
    cat, short = prefix_category
    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Category"])
    for i in range(10):
        wb.active.append([unique(f"imp-{i}"), cat])
    sheet = io.BytesIO()
    wb.save(sheet)

    def job(i):
        if i == 0:
            return httpx.post(BASE_URL + "/products/bulk-import", headers=hdr, timeout=120,
                              data={"group_id": group_id}, files={"excel_file": ("p.xlsx", sheet.getvalue())})
        return post(hdr, "/products", {"group_id": group_id, "name": unique(f"one-{i}"), "category": cat})

    rs = parallel(job, 11)
    assert all(r.status_code in (200, 201) for r in rs), [r.text for r in rs]
    skus = [c["sku"] for c in rs[0].json()["created"]] + [r.json()["sku"] for r in rs[1:]]
    assert numbers(skus, rf"{short}-(\d+)") == list(range(1, 21))


def test_generated_sku_taken_by_hand_is_skipped(admin, group_id, prefix_category):
    cat, short = prefix_category
    first = admin.post("/products", json={"group_id": group_id, "name": unique("a"), "category": cat}).json()
    assert first["sku"] == f"{short}-0001"
    typed = admin.post("/products", json={"group_id": group_id, "name": unique("b"), "sku": f"{short}-0002"})
    assert typed.status_code == 201
    nxt = admin.post("/products", json={"group_id": group_id, "name": unique("c"), "category": cat})
    assert nxt.status_code == 201 and nxt.json()["sku"] == f"{short}-0003"


# ---- A2: version check ---------------------------------------------------

def _make(admin, group_id, path):
    body = {"/customers": {"name": unique("v-cust")}, "/projects": {"name": unique("v-proj")},
            "/companies": {"name": unique("v-co")}, "/products": {"name": unique("v-prod")},
            "/documents": {"doc_type": "quotation", "items": [{"description": "v", "quantity": "1", "unit_price": "1"}]}}[path]
    if path != "/companies":
        body["group_id"] = group_id
    r = admin.post(path, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _patch(hdr, path, row, value, **extra):
    field = "terms_and_conditions" if path == "/documents" else "name"
    body = {field: value, **extra}
    if path == "/documents":
        body["items"] = [{"description": "v", "quantity": "1", "unit_price": "1"}]
    return httpx.patch(f"{BASE_URL}{path}/{row['id']}", json=body, headers=hdr, timeout=60), field


@pytest.mark.parametrize("path", ["/documents", "/products", "/customers", "/projects", "/companies"])
def test_stale_edit_is_refused_for_every_entity(admin, hdr, group_id, path):
    # unique values: a product name must not exactly duplicate an earlier run's
    a_val, b_val, b2_val, plain_val = (unique(x) for x in ("A-wins", "B-stale", "B-reloaded", "no-version"))
    row = _make(admin, group_id, path)
    loaded = row["version"]  # clients A and B both load this
    a, field = _patch(hdr, path, row, a_val, version=loaded)
    assert a.status_code == 200, a.text
    b, _ = _patch(hdr, path, row, b_val, version=loaded)
    assert b.status_code == 409 and b.json()["detail"] == CONFLICT
    assert admin.get(f"{path}/{row['id']}").json()[field] == a_val
    b2, _ = _patch(hdr, path, row, b2_val, version=a.json()["version"])
    assert b2.status_code == 200 and b2.json()[field] == b2_val
    plain, _ = _patch(hdr, path, row, plain_val)
    assert plain.status_code == 200  # scripts / bulk tools without a version keep working


@pytest.mark.parametrize("path", ["/documents", "/products"])
def test_parallel_saves_of_one_version_only_one_wins(admin, hdr, group_id, path):
    row = _make(admin, group_id, path)
    rs = parallel(lambda i: _patch(hdr, path, row, f"writer {i}", version=row["version"])[0], 10)
    assert sorted(r.status_code for r in rs) == [200] + [409] * 9, [r.text for r in rs]


# ---- A3: idempotency -----------------------------------------------------

@pytest.mark.parametrize("path", ["/products", "/documents", "/customers"])
def test_ten_parallel_repeats_of_one_key_make_one_record(admin, hdr, group_id, path):
    name = unique("idem")
    body = {"group_id": group_id, "name": name} if path != "/documents" else {
        "group_id": group_id, "doc_type": "quotation", "terms_and_conditions": name,
        "items": [{"description": name, "quantity": "1", "unit_price": "1"}]}
    key = uuid.uuid4().hex
    rs = parallel(lambda i: post(hdr, path, body, **{"Idempotency-Key": key}), 10)
    assert [r.status_code for r in rs] == [201] * 10, [r.text for r in rs]
    assert len({r.json()["id"] for r in rs}) == 1
    listed = admin.get(path).json()
    match = [x for x in listed if name in (x.get("name"), x.get("terms_and_conditions"))]
    assert len(match) == 1
    if path == "/documents":  # a different key is a new record
        other = post(hdr, path, body, **{"Idempotency-Key": uuid.uuid4().hex})
        assert other.status_code == 201 and other.json()["id"] != rs[0].json()["id"]


# ---- A5/headers: no stale data -------------------------------------------

def test_api_answers_are_not_cached_but_static_files_revalidate(admin):
    for path in ("/documents", "/products", "/customers", "/auth/groups-public"):
        assert admin.get(path).headers.get("cache-control") == "no-store", path
    assert httpx.get(SITE + "/").headers.get("cache-control") == "no-cache"  # unchanged (iteration 9)
    assert httpx.get(SITE + "/js/app.js").headers.get("cache-control") == "no-cache"


# ---- destructive: restart, migration, reindex (loop-test stack only) -----

def _wait_healthy(hdr):
    def up():
        try:  # fresh connections: the restart reset the shared client's keep-alive ones
            return all(httpx.get(BASE_URL + p, headers=hdr).status_code == 200 for p in ("/products", "/documents"))
        except httpx.HTTPError:
            return False
    assert poll(up, timeout=90)


@destructive
def test_counters_continue_after_a_restart(hdr, group_id, prefix_category, company):
    cat, short = prefix_category
    assert post(hdr, "/products", {"group_id": group_id, "name": unique("r1"), "category": cat}).json()["sku"] == f"{short}-0001"
    doc = {"group_id": group_id, "doc_type": "quotation", "company_id": company["id"], "items": [{"description": "r", "quantity": "1", "unit_price": "1"}]}
    assert post(hdr, "/documents", doc).json()["doc_number"].endswith("/001")
    dc("restart", "catalogue-service", "document-service")
    dc("restart", "nginx")
    _wait_healthy(hdr)
    assert post(hdr, "/products", {"group_id": group_id, "name": unique("r2"), "category": cat}).json()["sku"] == f"{short}-0002"
    assert post(hdr, "/documents", doc).json()["doc_number"].endswith("/002")


MIGRATION_CHECK = r'''
import re, sys
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
url = sys.argv[1]
eng = create_engine(url)
from app.core.db import Base
from app import models
Base.metadata.create_all(eng)
# Make it look like the database before this iteration: no counters/keys, no versions.
with eng.begin() as c:
    c.execute(text("DROP TABLE counters")); c.execute(text("DROP TABLE idempotency_keys"))
    for t in TABLES:
        c.execute(text(f"ALTER TABLE {t} DROP COLUMN version"))
    EXTRA
    SEED
cfg = Config("alembic.ini"); cfg.set_main_option("sqlalchemy.url", url)
import app.core.config as conf; conf.settings.database_url = url
command.stamp(cfg, OLD_HEAD)
command.upgrade(cfg, "head")
command.upgrade(cfg, "head")  # guarded: runs again on every startup
with eng.connect() as c:
    print(dict(c.execute(text("SELECT name, value FROM counters")).all()))
    for t in CHECKED:
        assert c.execute(text(f"SELECT count(*) FROM {t} WHERE version = 1")).scalar() == c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
'''


@destructive
@pytest.mark.parametrize("svc,db,tables,old_head,extra,seed,expect", [
    ("catalogue-service", "m21_catalogue", ["products"], "0001_product_group_id", "",
     "c.execute(text(\"INSERT INTO products (id, sku, name, is_deleted) VALUES (gen_random_uuid(), 'COM-0007', 'a', false), (gen_random_uuid(), 'COM-0012', 'b', false), (gen_random_uuid(), 'PRD-0003', 'c', false)\"))",
     {"sku:COM": 12, "sku:PRD": 3}),
    ("document-service", "m21_documents", ["customers", "projects", "companies"], "0009_group_id",
     "c.execute(text('ALTER TABLE documents ALTER COLUMN version DROP NOT NULL'))",
     "c.execute(text(\"INSERT INTO documents (id, doc_type, doc_number, status, is_deleted, version) VALUES (gen_random_uuid(), 'quotation', 'SS-20261101/004', 'draft', false, NULL), (gen_random_uuid(), 'quotation', 'SS-20261101/009', 'draft', false, 1), (gen_random_uuid(), 'quotation', 'AB-20261102/001', 'draft', false, 1)\"))",
     {"doc:SS-20261101/": 9, "doc:AB-20261102/": 1}),
])
def test_migration_seeds_counters_from_existing_data(svc, db, tables, old_head, extra, seed, expect):
    dc("exec", "-T", "postgres", "psql", "-U", "docmgmt", "-d", "docmgmt", "-c", f"DROP DATABASE IF EXISTS {db}")
    dc("exec", "-T", "postgres", "psql", "-U", "docmgmt", "-d", "docmgmt", "-c", f"CREATE DATABASE {db}")
    try:
        checked = tables + (["documents"] if svc == "document-service" else [])
        script = (MIGRATION_CHECK.replace("CHECKED", repr(checked)).replace("TABLES", repr(tables)).replace("OLD_HEAD", repr(old_head))
                  .replace("EXTRA", extra or "pass").replace("SEED", seed))
        pw = os.environ.get("POSTGRES_PASSWORD", "docmgmt")
        r = dc("exec", "-T", svc, "python", "-c", script, f"postgresql://docmgmt:{pw}@postgres:5432/{db}", check=False)
        assert r.returncode == 0, r.stderr[-3000:]
        assert eval(r.stdout.strip().splitlines()[-1]) == expect
    finally:
        dc("exec", "-T", "postgres", "psql", "-U", "docmgmt", "-d", "docmgmt", "-c", f"DROP DATABASE IF EXISTS {db}", check=False)


@destructive
def test_failed_index_update_still_saves_and_reindex_brings_it_back(admin, group_id):
    name = unique("reindex")
    dc("stop", "meilisearch")
    try:
        r = admin.post("/products", json={"group_id": group_id, "name": name})
        assert r.status_code == 201, r.text  # the save does not depend on search
    finally:
        dc("start", "meilisearch")
    assert poll(lambda: admin.get("/search", params={"q": "x", "index": "products"}).status_code == 200, timeout=60)
    pid = r.json()["id"]

    def found():  # by id: Meilisearch also matches an earlier run's "reindex-…" on the word "reindex"
        return any(h["id"] == pid for h in admin.get("/search", params={"q": name, "index": "products"}).json().get("products", []))

    assert not found()
    env = {**os.environ}
    out = subprocess.run([os.path.join(REPO, "scripts/reindex.sh")], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "catalogue-service: re-indexed" in out.stdout
    assert poll(found, timeout=20)
