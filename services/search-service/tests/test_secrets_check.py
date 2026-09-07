"""
Security regression tests for Priority 2 — Remove Insecure Default Secrets
(search-service).
"""

import pytest

from app.core.secrets_check import enforce_production_secrets, is_insecure
from app.core.config import settings
import app.main as main


@pytest.fixture
def restore_settings():
    original = {"environment": settings.environment, "meili_master_key": settings.meili_master_key}
    yield
    for key, value in original.items():
        setattr(settings, key, value)


def test_default_meili_key_fails_in_production(restore_settings):
    settings.environment = "production"
    settings.meili_master_key = "local_dev_master_key_change_me"
    with pytest.raises(RuntimeError, match="MEILI_MASTER_KEY"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_missing_meili_key_fails_in_production(restore_settings):
    settings.environment = "production"
    settings.meili_master_key = ""
    with pytest.raises(RuntimeError, match="MEILI_MASTER_KEY"):
        enforce_production_secrets(settings.environment, main._secret_problems())


def test_valid_production_configuration_has_no_problems(restore_settings):
    settings.environment = "production"
    settings.meili_master_key = "a-real-unique-meili-key-abc123"
    assert main._secret_problems() == []
    enforce_production_secrets(settings.environment, main._secret_problems())  # must not raise


def test_insecure_default_only_warns_outside_production(restore_settings, caplog):
    settings.environment = "development"
    settings.meili_master_key = "local_dev_master_key_change_me"
    enforce_production_secrets(settings.environment, main._secret_problems())  # must not raise
    assert any("SECURITY" in record.message for record in caplog.records)


def test_is_insecure_rejects_generic_placeholders():
    for placeholder in ["", "CHANGE_ME", "GENERATE_A_SECURE_SECRET"]:
        assert is_insecure(placeholder) is True
    assert is_insecure("a-real-random-looking-secret-9f8e7d") is False
