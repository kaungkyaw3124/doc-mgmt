from sqlalchemy.orm import Session

from app import models


def log_action(db: Session, document_id, username: str | None, action: str) -> None:
    """Records one audit log entry. Never raises — a logging hiccup should
    never block the actual document operation."""
    try:
        entry = models.AuditLogEntry(
            document_id=document_id,
            username=username or "unknown",
            action=action,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
