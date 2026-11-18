"""
Safe simultaneous use (loop iteration 21): locked number counters, the
version check on edit, and Idempotency-Key replay. The same file lives in
catalogue-service (one copy per service, like group_scope.py) — keep them
identical.

Everything here runs inside the caller's own transaction, on purpose:
- a counter bumped by an insert that later rolls back is rolled back too,
  so numbers stay gapless;
- an idempotency key is only ever committed together with the record it
  created, so a stored key always points at a real row.
"""
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models

CONFLICT_DETAIL = "Someone else changed this. Reload to see their changes."
IDEMPOTENCY_TTL = timedelta(hours=24)


def next_number(db: Session, name: str, current_max, reseed: bool = False) -> int:
    """
    The next number for counter `name`, taken under the counter row's lock
    (`UPDATE … RETURNING` — two callers can never get the same value; the
    second waits until the first commits or rolls back). `current_max()`
    returns the highest number already used and is only called to seed a
    counter that doesn't exist yet, or with `reseed` to jump past a number
    taken some other way (a manually typed SKU/number).
    """
    exists = db.execute(text("SELECT 1 FROM counters WHERE name = :n"), {"n": name}).first()
    if not exists:
        # Two first-callers may both get here: DO NOTHING lets one seed win and
        # both then increment the same row, one after the other.
        db.execute(text("INSERT INTO counters (name, value) VALUES (:n, :v) ON CONFLICT (name) DO NOTHING"),
                   {"n": name, "v": current_max()})
    elif reseed:
        db.execute(text("UPDATE counters SET value = :v WHERE name = :n AND value < :v"), {"n": name, "v": current_max()})
    return db.execute(text("UPDATE counters SET value = value + 1 WHERE name = :n RETURNING value"), {"n": name}).scalar_one()


def peek_number(db: Session, name: str, current_max) -> int:
    """What next_number would return right now, without taking it (form previews)."""
    value = db.execute(text("SELECT value FROM counters WHERE name = :n"), {"n": name}).scalar()
    return (current_max() if value is None else value) + 1


def check_version(row, version: int | None) -> None:
    """409 when the client edited an older copy. No version sent = no check
    (scripts, bulk import); the ORM's version_id_col still refuses a write
    that lost a race between this check and the commit."""
    if version is not None and version != row.version:
        raise HTTPException(status_code=409, detail=CONFLICT_DETAIL)


def claim_idempotency_key(db: Session, key: str | None, endpoint: str):
    """
    Returns (held, replay_id). With no key: (None, None). For a key seen in
    the last IDEMPOTENCY_TTL: (None, id of the record the first request
    made) — the caller returns that record instead of creating another.
    Otherwise the key is now held in this transaction: the caller sets
    held.resource_id and commits it together with the new record. A
    parallel request with the same key blocks on the unique key until this
    one commits, then sees it — so ten parallel repeats create one record.
    Must be called before anything else is written in the transaction (a
    replay rolls the session back).
    """
    if not key:
        return None, None
    full = f"{endpoint}:{key.strip()[:200]}"
    db.query(models.IdempotencyKey).filter(
        models.IdempotencyKey.key == full,
        models.IdempotencyKey.created_at < datetime.utcnow() - IDEMPOTENCY_TTL,
    ).delete(synchronize_session=False)  # an expired key counts as new
    held = models.IdempotencyKey(key=full, created_at=datetime.utcnow())
    db.add(held)
    try:
        db.flush()
        return held, None
    except IntegrityError:
        db.rollback()
    first = db.get(models.IdempotencyKey, full)
    if not first or not first.resource_id:
        raise HTTPException(status_code=409, detail="This request is already being processed. Reload to check the result.")
    return None, first.resource_id


def remember(held, resource_id: uuid.UUID) -> None:
    if held is not None:
        held.resource_id = resource_id
