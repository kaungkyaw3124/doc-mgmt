import uuid

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.jwt_utils import decode_access_token
from app.core.refresh_tokens import session_is_active
from app import models


def require_live_session(db: Session, user_id, payload: dict) -> None:
    """401 unless the token's session (its `sid` claim) is still live —
    not logged out, revoked by an admin, idle-expired or past its hard end.
    Shared by get_current_user and /verify, so a revoked session's access
    token stops working everywhere at once, not after its 15 minutes."""
    try:
        sid = uuid.UUID(payload["sid"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="invalid token")
    if not session_is_active(db, user_id, sid):
        raise HTTPException(status_code=401, detail="session ended")


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="invalid token")

    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="user no longer exists")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="account pending approval")
    require_live_session(db, user_id, payload)
    return user


def require_superuser(current_user: models.User = Depends(get_current_user)) -> models.User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="superuser privileges required")
    return current_user
