"""
Security regression tests for Priority 2 — Remove Insecure Default Secrets
(catalogue-service). Only exercises the pure secret-validation path
(app.main._secret_problems / app.core.secrets_check) — NOT the rest of
on_startup, which needs real Postgres/MinIO/Meilisearch and is out of
scope for this check.
"""

import pytest

from app.core.secrets_check import enforce_production_secrets, is_insecure
from app.core.config import settings
import app.main as main


@pytest.fixture
def restore_settings():
    original = {
        "environment": settings.environment,
        "meili_master_key": settings.meili_master_key,
        "minio_access_key": settings.minio_access_key,
        "minio_secret_key": settings.minio_secret_key,
        "database_url": settings.database_url,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


def _valid_production_settings():
    settings.environment = "production"
    settings.meili_master_key = "a-real-unique-meili-key-abc123"
    settings.minio_access_key = "a-real-unique-minio-access-key"
    settings.minio_secret_key = "a-real-unique-minio-secret-key"
    settings.database_url = "postgresql://docmgmt:S3cur3-Unique-Pw@postgres:5432/catalogue"


def test_default_meili_key_fails_in_production(restore_settings):
    _valid_production_settings()
    settings.meili_master_key = "local_dev_master_key_change_me"
    with pytest.raises(RuntimeError, match="MEILI_MASTER_KEY"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_default_minio_credentials_fail_in_production(restore_settings):
    _valid_production_settings()
    settings.minio_access_key = "minioadmin"
    settings.minio_secret_key = "minioadmin"
    problems = main._secret_problems()
    assert "MINIO_ACCESS_KEY" in problems
    assert "MINIO_SECRET_KEY" in problems
    with pytest.raises(RuntimeError):
        enforce_production_secrets(settings.environment, problems)


def test_default_database_password_fails_in_production(restore_settings):
    _valid_production_settings()
    settings.database_url = "postgresql://docmgmt:docmgmt@postgres:5432/catalogue"
    with pytest.raises(RuntimeError, match="DATABASE_URL password"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_valid_production_configuration_has_no_problems(restore_settings):
    _valid_production_settings()
    assert main._secret_problems() == []
    enforce_production_secrets(settings.environment, main._secret_problems())  # must not raise


def test_insecure_defaults_only_warn_outside_production(restore_settings, caplog):
    settings.environment = "development"
    settings.meili_master_key = "local_dev_master_key_change_me"
    settings.minio_access_key = "minioadmin"
    settings.minio_secret_key = "minioadmin"
    settings.database_url = "postgresql://docmgmt:docmgmt@postgres:5432/catalogue"
    enforce_production_secrets(settings.environment, main._secret_problems())  # must not raise
    assert any("SECURITY" in record.message for record in caplog.records)


def test_is_insecure_rejects_generic_placeholders():
    for placeholder in ["", "CHANGE_ME", "GENERATE_A_SECURE_SECRET"]:
        assert is_insecure(placeholder) is True
    assert is_insecure("a-real-random-looking-secret-9f8e7d") is False
