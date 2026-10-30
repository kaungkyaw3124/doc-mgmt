from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app import models


class RateLimitExceeded(Exception):
    """Raised when a login attempt should be rejected with 429."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limit exceeded, retry after {retry_after_seconds}s")


def get_client_ip(request: Request) -> str:
    """
    Best-effort client IP. Nginx (the only intended entry point) sets
    X-Real-IP for /api/auth/login — see infra/nginx/nginx.conf. Falls back
    to X-Forwarded-For's first hop, then the raw socket peer for direct/
    local access (e.g. tests, or auth-service hit without the gateway).
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _count_since(db: Session, cutoff: datetime, **filters) -> int:
    query = db.query(func.count(models.LoginAttempt.id)).filter(models.LoginAttempt.created_at >= cutoff)
    for column, value in filters.items():
        query = query.filter(getattr(models.LoginAttempt, column) == value)
    return query.scalar() or 0


def check_login_rate_limit(db: Session, ip: str, username: str) -> None:
    """
    Raises RateLimitExceeded if this login attempt should be blocked.
    Must be called BEFORE the attempt is recorded (so the current attempt
    doesn't count against itself), and before any password verification
    (so the timing/response is identical whether or not the account
    exists — no account-enumeration signal via rate limiting).
    """
    now = datetime.utcnow()
    username_key = username.strip().lower()

    ip_cutoff = now - timedelta(minutes=settings.rate_limit_ip_window_minutes)
    if _count_since(db, ip_cutoff, ip=ip) >= settings.rate_limit_ip_max_attempts:
        raise RateLimitExceeded(retry_after_seconds=settings.rate_limit_ip_window_minutes * 60)

    pair_cutoff = now - timedelta(minutes=settings.rate_limit_pair_window_minutes)
    if _count_since(db, pair_cutoff, ip=ip, username=username_key) >= settings.rate_limit_pair_max_attempts:
        raise RateLimitExceeded(retry_after_seconds=settings.rate_limit_pair_window_minutes * 60)

    username_cutoff = now - timedelta(minutes=settings.rate_limit_username_window_minutes)
    if _count_since(db, username_cutoff, username=username_key) >= settings.rate_limit_username_max_attempts:
        raise RateLimitExceeded(retry_after_seconds=settings.rate_limit_username_window_minutes * 60)


def record_failed_attempt(db: Session, ip: str, username: str) -> None:
    username_key = username.strip().lower()

    # Opportunistic housekeeping: drop rows older than every window we
    # check, so the table doesn't grow unbounded under sustained abuse.
    max_window = max(
        settings.rate_limit_ip_window_minutes,
        settings.rate_limit_pair_window_minutes,
        settings.rate_limit_username_window_minutes,
    )
    prune_cutoff = datetime.utcnow() - timedelta(minutes=max_window)
    db.query(models.LoginAttempt).filter(models.LoginAttempt.created_at < prune_cutoff).delete()

    db.add(models.LoginAttempt(ip=ip, username=username_key))
    db.commit()
