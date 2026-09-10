import logging

from fastapi import FastAPI

from app.core.db import Base, engine, SessionLocal
from app.core.config import settings
from app.core.security import hash_password
from app.core.secrets_check import db_password_from_url, enforce_production_secrets, is_insecure
from app.core.seed import ensure_default_groups_and_roles, migrate_operation_members_to_full_existing_data_access
from app import models
from app.routers import auth, admin

logger = logging.getLogger(__name__)

app = FastAPI(title="Auth Service", version="0.1.0")

app.include_router(auth.router)
app.include_router(admin.router)


def _secret_problems() -> list[str]:
    """Pure check, no I/O — kept separate from on_startup so it can be
    tested (and, more importantly, so it runs and can fail BEFORE any
    database/network I/O below it)."""
    problems = []
    if is_insecure(settings.jwt_secret, {"local_dev_jwt_secret_change_me"}):
        problems.append("JWT_SECRET")
    if is_insecure(settings.seed_admin_password, {"changeme", "admin"}):
        problems.append("SEED_ADMIN_PASSWORD")
    if is_insecure(db_password_from_url(settings.database_url), {"docmgmt", "postgres"}):
        problems.append("DATABASE_URL password")
    return problems


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())

    Base.metadata.create_all(bind=engine)

    # Seed the first user as a superuser, but only if the users table is
    # completely empty — someone needs superuser rights to bootstrap every
    # group/role/permission that follows. After this, manage users via the
    # API (POST /users for superuser, POST /admin/groups/{id}/users for
    # group admins), not this env var.
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            seed_user = models.User(
                username=settings.seed_admin_username,
                hashed_password=hash_password(settings.seed_admin_password),
                is_superuser=True,
                is_approved=True,
            )
            db.add(seed_user)
            db.commit()

        # User Control: the "Operation" group and its Editor/Viewer roles
        # (see app/core/seed.py) — always ensured, not just on an empty
        # database, and idempotent so re-running it never duplicates or
        # resets an admin's own later edits.
        ensure_default_groups_and_roles(db)

        # Existing-data migration for User Control: any Operation member
        # left over from a stale/pre-existing UserProjectAccess restriction
        # is restored to unrestricted ("ALL projects") visibility — see
        # migrate_operation_members_to_full_existing_data_access's
        # docstring for why this is the correct and only necessary
        # backfill. Idempotent, always run (not just on an empty database).
        migrate_operation_members_to_full_existing_data_access(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
