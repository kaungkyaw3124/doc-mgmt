import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app import models


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    """Normalizes a datetime to timezone-aware UTC before it's ever
    compared against another datetime in this module. `expires_at`/
    `revoked_at` are stored in a `DateTime(timezone=True)` column and the
    DB driver is expected to hand back timezone-aware values for it — but
    relying on that alone is exactly what caused this task's known CI
    failure (`TypeError: can't compare offset-naive and offset-aware
    datetimes`, comparing a driver-returned value against a naive
    `datetime.utcnow()`). Every comparison now goes through this instead
    of assuming either naive or aware: a naive value found here IS
    treated as UTC (matching how this module always wrote it), never
    silently misinterpreted as local time."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def issue_refresh_token(db: Session, user: models.User) -> str:
    """Creates a new refresh token row and returns the PLAINTEXT value —
    the only time it ever exists outside the cookie; only its hash is
    stored."""
    plaintext = secrets.token_urlsafe(32)
    row = models.RefreshToken(
        user_id=user.id,
        token_hash=_hash(plaintext),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(row)
    db.commit()
    return plaintext


def rotate_refresh_token(db: Session, plaintext: str) -> tuple[models.User, str] | None:
    """
    Validates a presented refresh token and, if valid, rotates it: the
    presented one is revoked and a new one is issued. Returns
    (user, new_plaintext) on success, None if the token is missing,
    expired, already revoked, or doesn't belong to a still-active/
    approved user.

    Presenting an already-revoked (but not expired) token is a replay of
    a token that was already rotated away — treated as a possible theft
    signal, so ALL of that user's other outstanding refresh tokens are
    revoked too, forcing every session to re-authenticate rather than
    silently trusting the replayed one.
    """
    if not plaintext:
        return None

    token_hash = _hash(plaintext)
    row = db.query(models.RefreshToken).filter_by(token_hash=token_hash).first()
    if not row:
        return None

    now = datetime.now(timezone.utc)
    if row.revoked_at is not None:
        revoke_all_user_tokens(db, row.user_id)
        return None
    if _aware(row.expires_at) < now:
        return None

    user = db.query(models.User).filter_by(id=row.user_id).first()
    if not user or not user.is_active or not user.is_approved:
        return None

    row.revoked_at = now
    db.commit()

    new_plaintext = issue_refresh_token(db, user)
    return user, new_plaintext


def revoke_refresh_token(db: Session, plaintext: str) -> None:
    """Used by /logout — revokes one specific token. No-op if it doesn't
    exist or is already revoked/expired (logout always succeeds from the
    client's point of view either way)."""
    if not plaintext:
        return
    row = db.query(models.RefreshToken).filter_by(token_hash=_hash(plaintext)).first()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_user_tokens(db, user_id) -> None:
    """Revokes every outstanding (not already revoked) refresh token for a
    user — called on account disable and on password change, so an
    already-issued refresh token can't be used to mint fresh access
    tokens after either event. Accepts a bare user_id (not a User object)
    since the replay-detection path above only has the id at that point."""
    now = datetime.now(timezone.utc)
    db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.revoked_at.is_(None),
    ).update({models.RefreshToken.revoked_at: now}, synchronize_session=False)
    db.commit()
