import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    # jti: a random per-token nonce, not a PII/identity claim. Without it,
    # two tokens minted for the same user within the same wall-clock
    # second (exp only has 1-second resolution) would be byte-for-byte
    # identical, since a JWT is a deterministic function of its payload —
    # e.g. a refresh that happens to land in the same second as the login
    # it followed. Not itself a security hole (both tokens are equally
    # valid for the same short window either way), but it defeats the
    # expectation that a refresh actually mints a materially new
    # credential, and made this exact scenario flaky in this task's own
    # end-to-end tests. A random jti makes every issued token unique
    # regardless of timing.
    payload = {"sub": subject, "exp": expire, "jti": secrets.token_urlsafe(8)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError (or a subclass) if the token is invalid or expired."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
