import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
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


def session_end(row: models.RefreshToken) -> datetime:
    """The session's hard end: login time + session_max_hours. Activity never extends it."""
    return _aware(row.session_started_at) + timedelta(hours=settings.session_max_hours)


def issue_refresh_token(db: Session, user: models.User, previous: models.RefreshToken | None = None,
                        user_agent: str | None = None, ip: str | None = None) -> str:
    """Creates a new refresh token row and returns the PLAINTEXT value —
    the only time it ever exists outside the cookie; only its hash is
    stored. Without `previous` this starts a new session (a login); with
    it, the new token continues that session (a rotation). Either way it
    expires at min(now + idle timeout, session hard end), and the commit
    also persists any pending change to `previous` (its revocation), so a
    rotation never leaves a moment with no live token for the session."""
    plaintext = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = models.RefreshToken(
        user_id=user.id,
        token_hash=_hash(plaintext),
        session_id=previous.session_id if previous else uuid.uuid4(),
        session_started_at=previous.session_started_at if previous else now,
        last_activity_at=now,
        user_agent=previous.user_agent if previous else (user_agent or "")[:255] or None,
        ip=(ip or (previous.ip if previous else None) or "")[:64] or None,
    )
    row.expires_at = min(now + timedelta(minutes=settings.session_idle_timeout_minutes), session_end(row))
    db.add(row)
    db.commit()
    return plaintext


def token_row(db: Session, plaintext: str) -> models.RefreshToken:
    return db.query(models.RefreshToken).filter_by(token_hash=_hash(plaintext)).one()


def rotate_refresh_token(db: Session, plaintext: str, ip: str | None = None) -> tuple[models.User, str, models.RefreshToken] | None:
    """
    Validates a presented refresh token and, if valid, rotates it: the
    presented one is revoked and a new one is issued for the same session.
    Returns (user, new_plaintext, new_row) on success, None if the token
    is missing, expired (idle too long, or past the session's hard end),
    already revoked, from before sessions existed, or doesn't belong to a
    still-active/approved user.

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
    if _aware(row.expires_at) <= now:  # <=: a token is dead from its expiry instant on (same as session_is_active)
        return None
    if row.session_started_at is None:
        return None  # issued before sessions existed (14-day cookie): log in once more

    user = db.query(models.User).filter_by(id=row.user_id).first()
    if not user or not user.is_active or not user.is_approved:
        return None

    row.revoked_at = now  # committed together with the new token, by issue_refresh_token
    new_plaintext = issue_refresh_token(db, user, previous=row, ip=ip)
    return user, new_plaintext, token_row(db, new_plaintext)


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


def session_is_active(db: Session, user_id, session_id) -> bool:
    """True while the session has a live (un-revoked, unexpired) token.
    Called by /verify on every gatewayed request: one indexed lookup."""
    return db.query(models.RefreshToken.id).filter(
        models.RefreshToken.session_id == session_id,
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.revoked_at.is_(None),
        models.RefreshToken.expires_at > datetime.now(timezone.utc),
    ).first() is not None


def active_sessions(db: Session, user_id) -> list[models.RefreshToken]:
    """One row per live session (its current token), most recently active first."""
    return db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.session_id.isnot(None),
        models.RefreshToken.revoked_at.is_(None),
        models.RefreshToken.expires_at > datetime.now(timezone.utc),
    ).order_by(models.RefreshToken.last_activity_at.desc()).all()


def revoke_session(db: Session, user_id, session_id) -> bool:
    """Ends one session (its live token). False if it wasn't live."""
    count = db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.session_id == session_id,
        models.RefreshToken.revoked_at.is_(None),
    ).update({models.RefreshToken.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    return count > 0


SESSION_COLUMNS = (
    "session_id UUID",
    "session_started_at TIMESTAMP WITH TIME ZONE",
    "last_activity_at TIMESTAMP WITH TIME ZONE",
    "user_agent VARCHAR(255)",
    "ip VARCHAR(64)",
)


def ensure_session_columns(engine) -> None:
    """create_all() never adds columns to an existing table, so databases
    from before sessions get them here. Idempotent (Postgres IF NOT EXISTS);
    run at every startup."""
    with engine.begin() as conn:
        for column in SESSION_COLUMNS:
            conn.execute(text(f"ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS {column}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_refresh_tokens_session_id ON refresh_tokens (session_id)"))


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
