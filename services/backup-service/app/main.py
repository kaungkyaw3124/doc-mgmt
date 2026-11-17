"""
Backup & Restore API — a thin layer over app/engine.py. Reached only through
nginx: /api/backup/* requires a superuser (auth_request with X-Service
"backup", see auth-service's /verify), except /status (public, so the UI can
still read the outcome once the restore has replaced the sessions table) and
/fetch/<ticket> (authorised by the single-use ticket itself, so the browser
can download with a plain link and stream straight to disk).
"""
import hmac
import os
import secrets
import shutil
import threading
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import engine

app = FastAPI(title="Backup Service", version="0.1.0")

INTERNAL_SHARED_SECRET = os.environ.get("INTERNAL_SHARED_SECRET", "")
TICKET_SECONDS = 60

STATUS = {"state": "idle", "step": None, "message": None}
UPLOADS: dict[str, tuple[str, dict]] = {}  # upload_id -> (workdir, manifest); at most one
TICKETS: dict[str, tuple[str, str, float]] = {}  # ticket -> (backup name, password, expires)
_guard = threading.Lock()  # protects the three dicts above


@app.middleware("http")
async def verify_gateway_secret(request: Request, call_next):
    # Same trust boundary as the other services' app/core/gateway_auth.py:
    # only nginx (which ran the superuser check) knows this secret.
    if request.url.path != "/health" and not hmac.compare_digest(
        request.headers.get("x-internal-secret", ""), INTERNAL_SHARED_SECRET
    ):
        return JSONResponse(status_code=401, content={"detail": "missing or invalid gateway credential"})
    return await call_next(request)


@app.on_event("startup")
def on_startup():
    # A restart means nothing is running in this container any more: clear a
    # maintenance flag or temp files a crash may have left behind, so the app
    # can never stay stuck at 503.
    # Only when no CLI job (cron / backup.sh `exec`) holds the lock: its
    # temp files and maintenance flag are live.
    try:
        with engine.locked():
            engine.maintenance(False)
            shutil.rmtree(engine.tmp_root(), ignore_errors=True)
            engine.tmp_root()
    except engine.BackupError:
        pass


@app.get("/health")
def health():
    return {"status": "ok"}


def _http(e: engine.BackupError, code: int = 400):
    return HTTPException(status_code=409 if "already running" in str(e) else code, detail=str(e))


@app.get("/backup/status")
def status():
    return {**STATUS, "maintenance": os.path.exists(engine.MAINTENANCE_FLAG)}


@app.get("/backup/list")
def list_backups():
    return engine.list_backups()


@app.post("/backup/create", status_code=201)
def create():
    try:
        with engine.locked():
            return {"name": engine.create_backup("backup")}
    except engine.BackupError as e:
        raise _http(e, 500)


class TicketIn(BaseModel):
    name: str
    password: str = ""


@app.post("/backup/download-ticket")
def download_ticket(body: TicketIn):
    try:
        engine.backup_path(body.name)
    except engine.BackupError as e:
        raise _http(e, 404)
    ticket = secrets.token_urlsafe(32)
    with _guard:
        now = time.time()
        for t in [t for t, v in TICKETS.items() if v[2] < now]:
            del TICKETS[t]
        TICKETS[ticket] = (body.name, body.password, now + TICKET_SECONDS)
    return {"url": f"/api/backup/fetch/{ticket}"}


@app.get("/backup/fetch/{ticket}")
def fetch(ticket: str):
    with _guard:
        name, password, expires = TICKETS.pop(ticket, (None, None, 0))
    if not name or expires < time.time():
        raise HTTPException(status_code=404, detail="This download link has expired — click Download again.")
    try:
        path = engine.backup_path(name)
    except engine.BackupError as e:
        raise _http(e, 404)
    if not password:
        return FileResponse(path, media_type="application/gzip", filename=name)
    return StreamingResponse(engine.encrypt_stream(path, password), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{name}.enc"'})


def _drop_uploads():
    with _guard:
        for work, _ in UPLOADS.values():
            shutil.rmtree(work, ignore_errors=True)
        UPLOADS.clear()


@app.post("/backup/restore/validate")
def validate(file: UploadFile = File(...), password: str = Form("")):
    """Checks an uploaded backup and keeps it ready for /backup/restore.
    Only the latest upload is kept."""
    _drop_uploads()
    work = engine.workdir()
    src = os.path.join(work, "upload")
    try:
        with open(src, "wb") as f:
            shutil.copyfileobj(file.file, f, 1 << 20)
        prepared, manifest = engine.prepare_restore(src, password or None)
    except engine.BackupError as e:
        raise _http(e)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    upload_id = secrets.token_urlsafe(16)
    with _guard:
        UPLOADS[upload_id] = (prepared, manifest)
    return {"upload_id": upload_id, "summary": engine.summary(manifest)}


class RestoreIn(BaseModel):
    upload_id: str
    confirm: str = ""


@app.post("/backup/restore", status_code=202)
def start_restore(body: RestoreIn):
    if body.confirm != "RESTORE":
        raise HTTPException(status_code=400, detail="Type RESTORE to confirm.")
    try:
        fd = engine.acquire_lock()
    except engine.BackupError as e:
        raise _http(e)
    with _guard:
        prepared = UPLOADS.pop(body.upload_id, None)
    if not prepared:
        engine.release_lock(fd)
        raise HTTPException(status_code=404, detail="That upload is no longer available — upload the file again.")
    STATUS.update(state="running", step="starting", message=None)

    def run():
        try:
            pre = engine.restore(*prepared, on_step=lambda s: STATUS.update(step=s))
            STATUS.update(state="done", step=None, message=f"Restore complete. The data from before it is saved as {pre}.")
        except Exception as e:  # BackupError carries the step + rollback file
            STATUS.update(state="failed", message=str(e))
        finally:
            engine.release_lock(fd)

    threading.Thread(target=run, daemon=True).start()
    return {"started": True}
