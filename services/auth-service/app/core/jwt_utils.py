from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError (or a subclass) if the token is invalid or expired."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
