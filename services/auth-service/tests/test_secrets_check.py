"""
Security regression tests for Priority 2 — Remove Insecure Default Secrets
(auth-service). Covers both the pure validation helpers and the actual
app startup path (app.main.on_startup), which is what a real deployment
runs.
"""

import pytest

from app.core.secrets_check import db_password_from_url, enforce_production_secrets, is_insecure
from app.core.config import settings
import app.main as main


# --- pure helper tests -------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["", "  ", "CHANGE_ME", "changeme", "change_me", "GENERATE_A_SECURE_SECRET", "local_dev_jwt_secret_change_me"],
)
def test_known_insecure_values_are_flagged(value):
    assert is_insecure(value, {"local_dev_jwt_secret_change_me"}) is True


def test_a_real_looking_secret_is_not_flagged():
    assert is_insecure("kX9$mQ2#pL7vN4wR8tY1uI6oP3aS5dF0g") is False


def test_db_password_extraction():
    assert db_password_from_url("postgresql://docmgmt:docmgmt@postgres:5432/auth") == "docmgmt"
    assert db_password_from_url("postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/auth") == "S3cur3-Unique-Pw"


# --- actual startup path (app.main.on_startup) --------------------------

@pytest.fixture
def restore_settings():
    original = {
        "environment": settings.environment,
        "jwt_secret": settings.jwt_secret,
        "seed_admin_password": settings.seed_admin_password,
        "database_url": settings.database_url,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


def test_missing_jwt_secret_fails_startup_in_production(restore_settings):
    settings.environment = "production"
    settings.jwt_secret = ""
    settings.seed_admin_password = "a-real-unique-admin-password-1"
    settings.database_url = "postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/auth"
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_default_jwt_secret_fails_startup_in_production(restore_settings):
    settings.environment = "production"
    settings.jwt_secret = "local_dev_jwt_secret_change_me"
    settings.seed_admin_password = "a-real-unique-admin-password-1"
    settings.database_url = "postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/auth"
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_missing_database_credentials_fail_startup_in_production(restore_settings):
    settings.environment = "production"
    settings.jwt_secret = "a-real-unique-jwt-secret-abc123"
    settings.seed_admin_password = "a-real-unique-admin-password-1"
    settings.database_url = "postgresql://docmgmt:docmgmt@postgres:5432/auth"
    with pytest.raises(RuntimeError, match="DATABASE_URL password"):
        main.on_startup()


def test_default_admin_password_fails_startup_in_production(restore_settings):
    settings.environment = "production"
    settings.jwt_secret = "a-real-unique-jwt-secret-abc123"
    settings.seed_admin_password = "changeme"
    settings.database_url = "postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/auth"
    with pytest.raises(RuntimeError, match="SEED_ADMIN_PASSWORD"):
        main.on_startup()


def test_valid_production_configuration_starts_cleanly(restore_settings):
    settings.environment = "production"
    settings.jwt_secret = "a-real-unique-jwt-secret-abc123"
    settings.seed_admin_password = "a-real-unique-admin-password-1"
    settings.database_url = "postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/auth"
    main.on_startup()  # must not raise


def test_insecure_defaults_only_warn_in_development(restore_settings, caplog):
    settings.environment = "development"
    settings.jwt_secret = "local_dev_jwt_secret_change_me"
    settings.seed_admin_password = "changeme"
    settings.database_url = "postgresql://docmgmt:docmgmt@postgres:5432/auth"
    main.on_startup()  # must not raise — development is convenience mode
    assert any("SECURITY" in record.message for record in caplog.records)
