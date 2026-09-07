import os
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql://docmgmt:docmgmt@localhost:5432/auth_test"),
)
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-a-default")
os.environ.setdefault("SEED_ADMIN_USERNAME", "admin")
os.environ.setdefault("SEED_ADMIN_PASSWORD", "test-only-admin-password-not-a-default")
# Small, fast-to-hit limits so tests don't need to fire hundreds of requests.
os.environ.setdefault("RATE_LIMIT_IP_MAX_ATTEMPTS", "6")
os.environ.setdefault("RATE_LIMIT_IP_WINDOW_MINUTES", "15")
os.environ.setdefault("RATE_LIMIT_PAIR_MAX_ATTEMPTS", "3")
os.environ.setdefault("RATE_LIMIT_PAIR_WINDOW_MINUTES", "15")
os.environ.setdefault("RATE_LIMIT_USERNAME_MAX_ATTEMPTS", "5")
os.environ.setdefault("RATE_LIMIT_USERNAME_WINDOW_MINUTES", "15")

import pytest
from fastapi.testclient import TestClient

from app.core.db import Base, engine, SessionLocal
from app.core.security import hash_password
from app import models
from app.main import app


@pytest.fixture(autouse=True)
def _clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_user(db):
    def _make_user(username="alice", password="correct horse battery staple", is_approved=True, is_active=True, is_superuser=False):
        user = models.User(
            id=uuid.uuid4(),
            username=username,
            hashed_password=hash_password(password),
            is_superuser=is_superuser,
            is_approved=is_approved,
            is_active=is_active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make_user
