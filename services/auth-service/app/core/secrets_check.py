"""
Fail-fast startup validation for secrets, gated on ENVIRONMENT=production.

In development (the default), an insecure/default secret only logs a
warning — that's the whole point of a checked-in ".env.example" a
developer can run against immediately. In production, the same value
crashes the process before it can serve a single request: a silently
running service with a publicly-known secret is worse than a service
that refuses to start.
"""

import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Generic placeholder text that must never be treated as a real secret,
# regardless of which field it's in — covers both ".env.example"'s
# CHANGE_ME-style placeholders and someone copying that literal text into
# a real .env without filling it in.
GENERIC_PLACEHOLDERS = {
    "",
    "changeme",
    "change_me",
    "change-me",
    "generate_a_secure_secret",
    "generate-a-secure-secret",
    "todo",
    "secret",
    "password",
}


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def is_insecure(value: str | None, known_defaults: set[str] = frozenset()) -> bool:
    normalized = _normalize(value)
    if normalized in GENERIC_PLACEHOLDERS:
        return True
    return normalized in {d.lower() for d in known_defaults}


def db_password_from_url(database_url: str) -> str | None:
    try:
        return urlsplit(database_url).password
    except ValueError:
        return None


def enforce_production_secrets(environment: str, problems: list[str]) -> None:
    """
    `problems` is the list of human-readable names of every secret found
    insecure/missing (caller does the field-specific checks and passes
    the resulting list here). In production with any problems, raises —
    FastAPI's startup event propagates this and the process exits rather
    than serving traffic. In any other environment, only warns.
    """
    if not problems:
        return

    message = (
        "SECURITY: insecure or missing secret(s): "
        + ", ".join(problems)
        + ". These must be set to real, unique values before deploying."
    )

    if environment.strip().lower() == "production":
        raise RuntimeError(message + " Refusing to start in production.")

    logger.warning(message)
