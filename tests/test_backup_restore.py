"""Backup & Restore (loop iteration 16): API auth, archive validation, the
engine round trip, maintenance mode and the one-at-a-time lock.

Tests marked `destructive` wipe or replace the whole stack's data and drive
it with `docker compose exec`, so they only run with ALLOW_DESTRUCTIVE_TESTS=1
against the throwaway loop-test stack (see infra/docker-compose.loop-test.yml;
COMPOSE_PROJECT_NAME / COMPOSE_OVERRIDE pick the stack, as for verify.sh)."""
import hashlib
import io
import json
import os
import struct
import subprocess
import tarfile
import time
import zlib

import httpx
import pytest
from pypdf import PdfReader

from conftest import BASE_URL, auth_headers, destructive, poll, relogin, unique

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = BASE_URL.removesuffix("/api")


def dc_cmd():
    cmd = ["docker", "compose", "-f", os.path.join(REPO, "infra/docker-compose.yml")]
    if os.environ.get("COMPOSE_OVERRIDE"):
        cmd += ["-f", os.path.join(REPO, os.environ["COMPOSE_OVERRIDE"])]
    return cmd


def dc(*args):
    return subprocess.run(dc_cmd() + list(args), capture_output=True, text=True)


def png(rgb) -> bytes:
    """A tiny valid PNG without needing Pillow in the test venv."""
    raw = b"".join(b"\x00" + bytes(rgb) * 8 for _ in range(8))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def create_backup(admin) -> str:
    r = admin.post("/backup/create", timeout=600)
    assert r.status_code == 201, r.text
    return r.json()["name"]


def download(admin, name, password="") -> bytes:
    t = admin.post("/backup/download-ticket", json={"name": name, "password": password})
    assert t.status_code == 200, t.text
    with httpx.stream("GET", SITE + t.json()["url"], timeout=600) as r:  # no token: the ticket is the authorization
        assert r.status_code == 200, r.read()[:300]
        body = b"".join(r.iter_bytes())
    again = httpx.get(SITE + t.json()["url"])
    assert again.status_code == 404, "a download ticket must be single-use"
    return body


def manifest_of(archive: bytes) -> dict:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as t:
        return json.load(t.extractfile("manifest.json"))


def repack(archive: bytes, edit_manifest=None, edit_member=None, extra=None) -> bytes:
    """Copy of a backup with its manifest / one member changed."""
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as src, tarfile.open(fileobj=out, mode="w:gz") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read()
            if m.name == "manifest.json" and edit_manifest:
                man = json.loads(data)
                edit_manifest(man)
                data = json.dumps(man).encode()
            elif edit_member and m.name == edit_member:
                data = data + b"tampered"
            m.size = len(data)
            dst.addfile(m, io.BytesIO(data))
        for name, data in (extra or {}).items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            dst.addfile(info, io.BytesIO(data))
    return out.getvalue()


def validate(admin, data: bytes, password=""):
    return admin.post("/backup/restore/validate", files={"file": ("b.tar.gz", data)},
                      data={"password": password}, timeout=600)


@pytest.fixture(scope="module")
def backup_bytes(admin):
    return download(admin, create_backup(admin))


# ---------- access ----------
def test_non_superuser_gets_403_everywhere(client, editor_user):
    login = client.post("/auth/login", json={"username": editor_user["username"], "password": editor_user["password"]})
    h = auth_headers(login.json()["access_token"])
    calls = [
        client.get("/backup/list", headers=h),
        client.post("/backup/create", headers=h),
        client.post("/backup/download-ticket", headers=h, json={"name": "x.tar.gz"}),
        client.post("/backup/restore/validate", headers=h, files={"file": ("b.tar.gz", b"x")}),
        client.post("/backup/restore", headers=h, json={"upload_id": "x", "confirm": "RESTORE"}),
    ]
    assert [r.status_code for r in calls] == [403] * len(calls), [r.text for r in calls]
    assert client.get("/backup/list").status_code == 401


def test_status_is_public_and_minimal(client):
    r = client.get("/backup/status")
    assert r.status_code == 200
    assert set(r.json()) == {"state", "step", "message", "maintenance"}


def test_fetch_with_unknown_ticket_is_404(client):
    assert client.get("/backup/fetch/not-a-ticket").status_code == 404


# ---------- validation ----------
def test_download_is_a_valid_backup_and_listed(admin, backup_bytes):
    man = manifest_of(backup_bytes)
    assert man["format"] == 1 and set(man["databases"]) == {"docmgmt", "catalogue", "auth"}
    assert man["alembic"]["document-service"]
    names = [b["name"] for b in admin.get("/backup/list").json()]
    assert any(n.startswith("backup-") for n in names)
    r = validate(admin, backup_bytes)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["created_at"] == man["created_at"]


@pytest.mark.parametrize("make, expect", [
    (lambda b: b"definitely not a tarball", "not a readable backup archive"),
    (lambda b: b[: len(b) // 2], "not a readable backup archive"),
    (lambda b: repack(b, edit_member="db/catalogue.dump"), "Checksum mismatch"),
    (lambda b: repack(b, edit_manifest=lambda m: m["alembic"].update({"document-service": "9999_from_the_future"})),
     "newer version of the app"),
    (lambda b: repack(b, extra={"../escape.txt": b"x"}), "Unsafe path"),
    (lambda b: repack(b, extra={"db/extra.dump": b"x"}), "do not match its manifest"),
], ids=["garbage", "truncated", "checksum", "newer-revision", "path-traversal", "unlisted-file"])
def test_bad_backups_are_refused(admin, backup_bytes, make, expect):
    r = validate(admin, make(backup_bytes))
    assert r.status_code == 400, r.text
    assert expect in r.json()["detail"]


def test_encrypted_backup_needs_the_right_password(admin):
    name = create_backup(admin)
    enc = download(admin, name, password="correct horse battery")
    assert enc.startswith(b"Salted__")
    assert "encrypted" in validate(admin, enc).json()["detail"]
    wrong = validate(admin, enc, password="wrong password")
    assert wrong.status_code == 400 and "Wrong password" in wrong.json()["detail"]
    ok = validate(admin, enc, password="correct horse battery")
    assert ok.status_code == 200, ok.text


def test_restore_requires_typing_restore(admin, backup_bytes):
    upload_id = validate(admin, backup_bytes).json()["upload_id"]
    for confirm in ("", "restore", "yes"):
        r = admin.post("/backup/restore", json={"upload_id": upload_id, "confirm": confirm})
        assert r.status_code == 400 and "RESTORE" in r.json()["detail"]


# ---------- stack-level (throwaway stack only) ----------
@destructive
def test_maintenance_mode_blocks_api_but_not_status():
    assert dc("exec", "-T", "backup-service", "touch", "/maintenance/on").returncode == 0
    try:
        assert httpx.get(f"{BASE_URL}/products").status_code == 503
        assert httpx.post(f"{BASE_URL}/auth/login", json={}).status_code == 503
        assert httpx.get(f"{SITE}/documents/x").status_code == 503
        status = httpx.get(f"{BASE_URL}/backup/status")
        assert status.status_code == 200 and status.json()["maintenance"] is True
        assert httpx.get(SITE + "/").status_code == 200  # the UI itself still loads
    finally:
        dc("exec", "-T", "backup-service", "rm", "-f", "/maintenance/on")
    assert httpx.get(f"{BASE_URL}/products").status_code == 401


@destructive
def test_second_backup_or_restore_is_rejected_while_one_runs(admin, backup_bytes):
    upload_id = validate(admin, backup_bytes).json()["upload_id"]
    holder = subprocess.Popen(
        dc_cmd() + ["exec", "-T", "backup-service", "python", "-c",
                    "from app import engine; import time; engine.acquire_lock(); print('held', flush=True); time.sleep(3)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        create = admin.post("/backup/create")
        restore = admin.post("/backup/restore", json={"upload_id": upload_id, "confirm": "RESTORE"})
        assert create.status_code == 409 and "already running" in create.json()["detail"]
        assert restore.status_code == 409
    finally:
        # Not kill(): that stops only the local docker client, while the
        # process in the container would keep the lock for the rest of its sleep.
        holder.wait(timeout=60)


def pdf_images(admin, doc_id) -> tuple[int, str]:
    r = admin.get(f"/documents/{doc_id}/export/quotation-pdf")
    assert r.status_code == 200, r.text[:300]
    reader = PdfReader(io.BytesIO(r.content))
    return sum(len(p.images) for p in reader.pages), "\n".join(p.extract_text() for p in reader.pages)


WIPE = """
import boto3, os, subprocess
for db in ("docmgmt", "catalogue"):
    subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"], check=True)
s3 = boto3.client("s3", endpoint_url=os.environ["MINIO_URL"], aws_access_key_id=os.environ["MINIO_ACCESS_KEY"], aws_secret_access_key=os.environ["MINIO_SECRET_KEY"])
n = 0
for b in s3.list_buckets()["Buckets"]:
    for o in s3.list_objects_v2(Bucket=b["Name"]).get("Contents", []):
        s3.delete_object(Bucket=b["Name"], Key=o["Key"]); n += 1
print("wiped", n)
"""


@destructive
@pytest.mark.restores_data
def test_round_trip_restore_is_lossless(admin):
    # --- seed one of everything, with files ---
    cat = admin.post("/categories", json={"name": unique("bk-cat"), "short_term": "BK" + unique("")[-6:].upper()}).json()
    product = admin.post("/products", json={"name": unique("Backup Widget"), "category": cat["name"], "unit_price": "9.50"}).json()
    assert admin.post(f"/products/{product['id']}/file",
                      files={"file": ("spec.pdf", b"%PDF-1.4\n% backup test\n%%EOF\n", "application/pdf")}).status_code in (200, 201)
    company = admin.post("/companies", json={"name": unique("Backup Co"), "short_name": "BK" + unique("")[-3:].upper()}).json()
    for kind, color in (("logo", (200, 30, 30)), ("seal", (30, 30, 200))):
        r = admin.post(f"/companies/{company['id']}/{kind}", files={"file": (f"{kind}.png", png(color), "image/png")})
        assert r.status_code == 200, r.text
    customer = admin.post("/customers", json={"name": unique("Backup Customer")}).json()
    project = admin.post("/projects", json={"name": unique("Backup Project")}).json()
    doc = admin.post("/documents", json={
        "doc_type": "quotation", "customer_id": customer["id"], "project_id": project["id"], "company_id": company["id"],
        "currency": "USD", "items": [{"description": unique("bk-item"), "quantity": "1", "unit_price": "9.50"}]}).json()
    assert admin.post(f"/documents/{doc['id']}/file",
                      files={"file": ("signed.pdf", b"%PDF-1.4\n% signed\n%%EOF\n", "application/pdf")}).status_code in (200, 201)
    images_before, text_before = pdf_images(admin, doc["id"])
    assert images_before >= 2, "the seeded quotation should carry logo + seal"

    # --- back up, download, wipe ---
    archive = download(admin, create_backup(admin))
    before = manifest_of(archive)
    assert before["object_count"] >= 4
    wiped = dc("exec", "-T", "backup-service", "python", "-c", WIPE)
    assert wiped.returncode == 0, wiped.stderr
    assert admin.get("/products").status_code >= 500 or admin.get("/products").json() == []

    # --- restore through the API ---
    v = validate(admin, archive)
    assert v.status_code == 200, v.text
    s = v.json()["summary"]
    assert s["products"] == before["row_counts"]["catalogue"]["products"] and s["files"] == before["object_count"]
    r = admin.post("/backup/restore", json={"upload_id": v.json()["upload_id"], "confirm": "RESTORE"})
    assert r.status_code == 202, r.text
    seen, deadline = set(), time.monotonic() + 300
    while time.monotonic() < deadline:
        seen.add(httpx.get(f"{BASE_URL}/products", headers=admin.headers).status_code)
        st = httpx.get(f"{BASE_URL}/backup/status").json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    assert "pre-restore-" in st["message"]
    assert 503 in seen, f"API calls during the restore should get 503, saw {seen}"

    # --- everything is back, byte for byte ---
    relogin(admin)  # the restore replaced the sessions table
    after = manifest_of(download(admin, create_backup(admin)))
    for db in ("docmgmt", "catalogue"):
        assert after["row_counts"][db] == before["row_counts"][db], db
    assert after["row_counts"]["auth"]["users"] == before["row_counts"]["auth"]["users"]
    minio = lambda m: {k: v for k, v in m["files"].items() if k.startswith("minio/")}
    assert minio(after) == minio(before)
    assert after["objects"] == before["objects"]  # content types too

    logo_key = admin.get(f"/companies/{company['id']}").json()["logo_object_key"]
    logo = httpx.get(admin.get(f"/companies/{company['id']}/logo-url").json()["url"])
    assert hashlib.sha256(logo.content).hexdigest() == before["files"][f"minio/documents/{logo_key}"]

    images_after, text_after = pdf_images(admin, doc["id"])
    assert images_after == images_before and text_after == text_before
    hits = poll(lambda: [h for h in admin.get("/search", params={"q": product["name"], "index": "products"}).json()
                         .get("products", []) if h.get("id") == product["id"]], timeout=20)
    assert hits, "restored product should be searchable (index rebuilt)"
    assert any(b["name"].startswith("pre-restore-") for b in admin.get("/backup/list").json())
