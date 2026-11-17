"""
Backup / restore engine. The one implementation behind every way of taking
or restoring a backup: the web UI (app/main.py), the nightly cron and
scripts/backup.sh / scripts/restore.sh / scripts/deploy.sh (this module's
CLI, run with `docker compose exec backup-service python -m app.engine`).

A backup is one .tar.gz:
  manifest.json          what is inside, row counts, sha256 of every file
  db/<database>.dump     pg_dump custom format, one per database
  minio/<bucket>/<key>   every MinIO object, byte for byte

Not in it: .env files / secrets, the Meilisearch index (rebuilt from the
database after a restore), and auth's refresh_tokens rows (live session
credentials — a restore therefore signs everyone out).

pg_dump's custom format (-Fc) rather than plain SQL: it is compressed, it can
be read back by pg_restore to check it, and it restores without depending on
whatever owner/role names the source server had (--no-owner). The restore
turns it into SQL and runs it inside ONE transaction per database together
with the DROP SCHEMA that empties it first — a dump that fails half-way rolls
back to the database as it was, instead of leaving it half-empty.
"""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
MAINTENANCE_FLAG = os.environ.get("MAINTENANCE_FLAG", "/maintenance/on")
DATABASES = ["docmgmt", "catalogue", "auth"]
EXCLUDE_TABLE_DATA = {"auth": ["refresh_tokens"]}
FORMAT = 1
KEEP_NIGHTLY = 14
OPENSSL = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:BACKUP_PASSWORD"]
SERVICES = {"document-service": "http://document-service:8000", "catalogue-service": "http://catalogue-service:8000"}


class BackupError(Exception):
    """A failure with a message that is safe and useful to show the user."""


# ---------- locking: one backup or restore at a time ----------
# flock on a file in the shared backups folder, so the web API and a cron
# `docker compose exec` in the same container exclude each other. The kernel
# drops the lock if the holder dies, so a crash can never leave it stuck.
def acquire_lock() -> int:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    fd = os.open(os.path.join(BACKUP_DIR, ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise BackupError("Another backup or restore is already running — try again when it has finished.")
    return fd


def release_lock(fd: int) -> None:
    os.close(fd)


@contextlib.contextmanager
def locked():
    fd = acquire_lock()
    try:
        yield
    finally:
        release_lock(fd)


# ---------- helpers ----------
def tmp_root() -> str:
    path = os.path.join(BACKUP_DIR, ".tmp")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def workdir() -> str:
    # On the backups volume (not the container's own disk), mode 0700.
    return tempfile.mkdtemp(dir=tmp_root())


def _run(cmd, **kw) -> bytes:
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode:
        raise BackupError(f"{cmd[0]} failed: {r.stderr.decode(errors='replace').strip()[-400:]}")
    return r.stdout


def _psql(db: str, sql: str) -> str:
    return _run(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", sql]).decode()


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(rel: str) -> str:
    """Archive member / object paths must stay inside the extraction folder."""
    parts = rel.split("/")
    if rel.startswith("/") or "\\" in rel or any(p in ("", ".", "..") for p in parts):
        raise BackupError(f"Unsafe path in backup: {rel!r}")
    return rel


def _s3():
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_URL", "http://minio:9000"),
        aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
        aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
    )


def _internal(service: str, path: str, method: str = "GET", timeout: float = 600) -> dict:
    req = urllib.request.Request(
        SERVICES[service] + path, method=method,
        headers={"X-Internal-Secret": os.environ.get("INTERNAL_SHARED_SECRET", "")},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception as e:
        raise BackupError(f"{service} {path} failed: {e}")


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(8) == b"Salted__"  # openssl enc's header


def encrypt_stream(path: str, password: str):
    """Yields the file encrypted with openssl enc (see docs/BACKUP_RESTORE.md
    for the matching manual decrypt command). Streamed, never held in memory."""
    proc = subprocess.Popen(OPENSSL + ["-salt", "-in", path], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env={**os.environ, "BACKUP_PASSWORD": password})
    try:
        for chunk in iter(lambda: proc.stdout.read(1 << 16), b""):
            yield chunk
    finally:
        proc.stdout.close()
        proc.wait()


def maintenance(on: bool) -> None:
    # nginx answers 503 to every API call while this file exists — see
    # infra/nginx/nginx.conf.template.
    if on:
        os.makedirs(os.path.dirname(MAINTENANCE_FLAG), exist_ok=True)
        open(MAINTENANCE_FLAG, "w").close()
    elif os.path.exists(MAINTENANCE_FLAG):
        os.remove(MAINTENANCE_FLAG)


# ---------- backup ----------
def _free_name(prefix: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    name, n = f"{prefix}-{stamp}.tar.gz", 1
    while os.path.exists(os.path.join(BACKUP_DIR, name)):
        n += 1
        name = f"{prefix}-{stamp}-{n}.tar.gz"
    return name


def _row_counts(db: str) -> dict:
    tables = [t for t in _psql(db, "select tablename from pg_tables where schemaname='public' order by 1").split()
              if t not in EXCLUDE_TABLE_DATA.get(db, [])]  # count what the dump holds
    if not tables:
        return {}
    sql = " union all ".join(f"select '{t}', count(*) from public.\"{t}\"" for t in tables)
    return {t: int(n) for t, n in (line.split("|") for line in _psql(db, sql).split())}


def _alembic_revision() -> str | None:
    try:
        return _psql("docmgmt", "select version_num from alembic_version").strip() or None
    except BackupError:
        return None  # table absent: a database that was never migrated


def create_backup(prefix: str = "backup") -> str:
    """Writes <prefix>-YYYY-MM-DD-HHMM.tar.gz into BACKUP_DIR and returns its
    name. The caller holds the lock."""
    work = workdir()
    try:
        files, objects, buckets = {}, {}, []
        os.makedirs(os.path.join(work, "db"))
        for db in DATABASES:
            rel = f"db/{db}.dump"
            _run(["pg_dump", "-Fc", "-d", db, "-f", os.path.join(work, rel)]
                 + [f"--exclude-table-data={t}" for t in EXCLUDE_TABLE_DATA.get(db, [])])
            files[rel] = sha256(os.path.join(work, rel))

        s3 = _s3()
        for b in s3.list_buckets().get("Buckets", []):
            bucket = b["Name"]
            buckets.append(bucket)
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for o in page.get("Contents", []):
                    if o["Key"].endswith("/"):
                        continue  # folder marker, no content (the services never create these)
                    rel = safe_rel(f"minio/{bucket}/{o['Key']}")
                    dest = os.path.join(work, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    obj = s3.get_object(Bucket=bucket, Key=o["Key"])
                    with open(dest, "wb") as f:
                        shutil.copyfileobj(obj["Body"], f, 1 << 20)
                    files[rel] = sha256(dest)
                    objects[rel] = obj.get("ContentType") or "application/octet-stream"

        manifest = {
            "format": FORMAT,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "app_commit": os.environ.get("APP_COMMIT", "unknown"),
            # catalogue-service and auth-service have no Alembic: their schema
            # is created by create_all() and is fully contained in the dump.
            "alembic": {"document-service": _alembic_revision(), "catalogue-service": None, "auth-service": None},
            "databases": DATABASES,
            "buckets": buckets,
            "row_counts": {db: _row_counts(db) for db in DATABASES},
            "object_count": len(objects),
            "files": files,
            "objects": objects,
        }
        with open(os.path.join(work, "manifest.json"), "w") as f:
            json.dump(manifest, f, indent=1)

        out = os.path.join(work, "out.tar.gz")
        with tarfile.open(out, "w:gz", compresslevel=6) as t:
            t.add(os.path.join(work, "manifest.json"), "manifest.json")
            for rel in files:
                t.add(os.path.join(work, rel), rel)
        name = _free_name(prefix)
        os.replace(out, os.path.join(BACKUP_DIR, name))  # same volume: atomic, no half-written backups
        return name
    finally:
        shutil.rmtree(work, ignore_errors=True)


def list_backups() -> list[dict]:
    rows = []
    for name in os.listdir(BACKUP_DIR) if os.path.isdir(BACKUP_DIR) else []:
        path = os.path.join(BACKUP_DIR, name)
        if name.endswith(".tar.gz") and not name.startswith(".") and os.path.isfile(path):
            st = os.stat(path)
            rows.append({"name": name, "size": st.st_size,
                         "created": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(timespec="seconds")})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def backup_path(name: str) -> str:
    """Path of an existing backup by its bare name (refuses anything else)."""
    if "/" in name or name.startswith(".") or not name.endswith(".tar.gz"):
        raise BackupError("No such backup.")
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(path):
        raise BackupError("No such backup.")
    return path


def prune(keep: int = KEEP_NIGHTLY, directory: str | None = None) -> list[str]:
    """Keeps the newest `keep` backup-*.tar.gz (by the timestamp in the name).
    pre-restore-* files are rollback points and are never pruned."""
    directory = directory or BACKUP_DIR
    names = sorted(n for n in os.listdir(directory) if n.startswith("backup-") and n.endswith(".tar.gz"))
    doomed = names[:-keep] if keep > 0 else names
    for n in doomed:
        os.remove(os.path.join(directory, n))
    return doomed


# ---------- restore ----------
def prepare_restore(src: str, password: str | None, known_revisions: list[str] | None = None) -> tuple[str, dict]:
    """Decrypts (if needed), extracts and checks a backup. Returns (workdir,
    manifest); the caller removes workdir. Raises BackupError for anything
    that should not be restored. `known_revisions` defaults to asking
    document-service which Alembic revisions this app has."""
    work = workdir()
    try:
        archive = src
        if is_encrypted(src):
            if not password:
                raise BackupError("This backup is encrypted — enter its password.")
            archive = os.path.join(work, "decrypted.tar.gz")
            r = subprocess.run(OPENSSL + ["-d", "-in", src, "-out", archive], capture_output=True,
                               env={**os.environ, "BACKUP_PASSWORD": password})
            if r.returncode:
                raise BackupError("Wrong password, or the file is damaged — it could not be decrypted.")

        x = os.path.join(work, "x")
        try:
            with tarfile.open(archive, "r:gz") as t:
                members = t.getmembers()
                for m in members:
                    safe_rel(m.name)
                    if not (m.isfile() or m.isdir()):
                        raise BackupError(f"Unexpected entry in backup: {m.name!r}")
                needed = sum(m.size for m in members) * 2 + os.path.getsize(archive)  # extract + pre-restore backup
                free = shutil.disk_usage(BACKUP_DIR).free
                if free < needed:
                    raise BackupError(f"Not enough free disk space to restore: need about {needed >> 20} MB, have {free >> 20} MB.")
                t.extractall(x, filter="data")
        except BackupError:
            raise
        except Exception:
            raise BackupError("This file is not a readable backup archive (corrupt or not a .tar.gz).")
        if archive != src:
            os.remove(archive)  # the decrypted copy is no longer needed

        try:
            with open(os.path.join(x, "manifest.json")) as f:
                manifest = json.load(f)
        except Exception:
            raise BackupError("manifest.json is missing or unreadable — this is not a backup made by this app.")
        if manifest.get("format") != FORMAT:
            raise BackupError(f"Unsupported backup format {manifest.get('format')!r}.")

        found = {os.path.relpath(os.path.join(d, n), x) for d, _, ns in os.walk(x) for n in ns} - {"manifest.json"}
        listed = set(manifest.get("files", {}))
        if found != listed:
            raise BackupError(f"Backup contents do not match its manifest ({len(found ^ listed)} file(s) differ).")
        for rel, digest in manifest["files"].items():
            if sha256(os.path.join(x, rel)) != digest:
                raise BackupError(f"Checksum mismatch for {rel} — the backup is damaged.")

        rev = (manifest.get("alembic") or {}).get("document-service")
        if known_revisions is None:
            known_revisions = _internal("document-service", "/internal/alembic-revisions")["revisions"]
        if rev and rev not in known_revisions:
            raise BackupError(f"This backup is from a newer version of the app (database revision {rev}). "
                              "Update the app first, then restore.")
        return work, manifest
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def summary(manifest: dict) -> dict:
    rc = manifest["row_counts"]
    return {
        "created_at": manifest["created_at"],
        "app_commit": manifest.get("app_commit"),
        "products": rc.get("catalogue", {}).get("products", 0),
        "documents": rc.get("docmgmt", {}).get("documents", 0),
        "companies": rc.get("docmgmt", {}).get("companies", 0),
        "customers": rc.get("docmgmt", {}).get("customers", 0),
        "projects": rc.get("docmgmt", {}).get("projects", 0),
        "users": rc.get("auth", {}).get("users", 0),
        "files": manifest["object_count"],
    }


def _restore_db(work: str, db: str) -> None:
    sql = os.path.join(work, f"{db}.sql")
    _run(["pg_restore", "--no-owner", "--no-privileges", "-f", sql, os.path.join(work, "x", "db", f"{db}.dump")])
    # One transaction: if anything in the dump fails, the DROP is rolled back too.
    _run(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-d", db,
          "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;", "-f", sql])
    os.remove(sql)


def _restore_objects(work: str, manifest: dict) -> None:
    """Each bucket in the backup ends up holding exactly the backup's objects:
    upload first, then delete extras, so no file is ever missing in between.
    Buckets the backup doesn't know about are left alone."""
    s3 = _s3()
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    for bucket in manifest["buckets"]:
        if bucket not in existing:
            s3.create_bucket(Bucket=bucket)
        prefix = f"minio/{bucket}/"
        wanted = {rel[len(prefix):]: rel for rel in manifest["objects"] if rel.startswith(prefix)}
        for key, rel in wanted.items():
            s3.upload_file(os.path.join(work, "x", rel), bucket, key,
                           ExtraArgs={"ContentType": manifest["objects"][rel]})
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for o in page.get("Contents", []):
                if o["Key"] not in wanted:
                    s3.delete_object(Bucket=bucket, Key=o["Key"])


def restore(work: str, manifest: dict, on_step=lambda step: None) -> str:
    """Replaces all data with the prepared backup. The caller holds the lock.
    Returns the pre-restore backup's name. Always leaves maintenance mode off
    and removes `work`. No automatic rollback: on failure the message names
    the pre-restore backup, which is restored the same way."""
    step, pre = "maintenance mode", None
    try:
        on_step(step)
        maintenance(True)
        # Taken inside maintenance mode, so no write can land between this
        # snapshot and the restore and then be lost from the rollback point.
        step = "pre-restore backup"; on_step(step)
        pre = create_backup("pre-restore")
        for db in manifest["databases"]:
            step = f"database {db}"; on_step(step)
            _restore_db(work, db)
        step = "files"; on_step(step)
        _restore_objects(work, manifest)
        step = "migrations and search index"; on_step(step)
        for service in SERVICES:
            _internal(service, "/internal/after-restore", method="POST")
        return pre
    except Exception as e:
        rollback = (f" Your data from before this restore is saved as {pre} — restore that file to roll back."
                    if pre else " Nothing was changed.")
        raise BackupError(f"Restore failed at step '{step}': {e}.{rollback}")
    finally:
        maintenance(False)
        shutil.rmtree(work, ignore_errors=True)


# ---------- CLI ----------
def main(argv: list[str]) -> int:
    """
    python -m app.engine backup [--keep N]   new backup-*.tar.gz, then keep the newest N (default 14)
    python -m app.engine verify FILE         check a backup without restoring it
    python -m app.engine restore FILE        restore (BACKUP_PASSWORD env var if encrypted)
    python -m app.engine prune [--keep N] [--dir D]
    """
    def opt(flag, default):
        return argv[argv.index(flag) + 1] if flag in argv else default

    cmd = argv[0] if argv else ""
    try:
        if cmd == "backup":
            with locked():
                name = create_backup("backup")
                removed = prune(int(opt("--keep", KEEP_NIGHTLY)))
            print(f"created {name}" + (f"; pruned {len(removed)} old backup(s)" if removed else ""))
        elif cmd in ("verify", "restore") and len(argv) > 1:
            src = argv[1] if os.path.isabs(argv[1]) else os.path.join(BACKUP_DIR, argv[1])
            with locked():
                work, manifest = prepare_restore(src, os.environ.get("BACKUP_PASSWORD"))
                print("backup OK: " + json.dumps(summary(manifest)))
                if cmd == "verify":
                    shutil.rmtree(work, ignore_errors=True)
                else:
                    pre = restore(work, manifest, on_step=lambda s: print(f"... {s}", flush=True))
                    print(f"restored; the pre-restore backup is {pre}")
        elif cmd == "prune":
            removed = prune(int(opt("--keep", KEEP_NIGHTLY)), opt("--dir", None))
            print(f"pruned {len(removed)} backup(s)")
        else:
            print(main.__doc__)
            return 2
    except BackupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
