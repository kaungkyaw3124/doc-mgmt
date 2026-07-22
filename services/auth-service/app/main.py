from fastapi import FastAPI

from app.core.db import Base, engine, SessionLocal
from app.core.config import settings
from app.core.security import hash_password
from app import models
from app.routers import auth, admin

app = FastAPI(title="Auth Service", version="0.1.0")

app.include_router(auth.router)
app.include_router(admin.router)


@app.on_event("startup")
def on_startup():
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
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
