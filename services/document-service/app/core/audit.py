from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app import models


def log_action(db: Session, document_id, username: str | None, action: str) -> None:
    """
    Records one audit log entry. Never raises — a logging hiccup should
    never block the actual document operation.

    Uses its own independent session rather than the caller's `db` (kept in
    the signature for call-site compatibility, but intentionally unused) —
    committing/rolling back the caller's shared session here would also
    commit or discard whatever unrelated changes the caller still has
    pending on it.
    """
    log_db = SessionLocal()
    try:
        entry = models.AuditLogEntry(
            document_id=document_id,
            username=username or "unknown",
            action=action,
        )
        log_db.add(entry)
        log_db.commit()
    except Exception:
        log_db.rollback()
    finally:
        log_db.close()
