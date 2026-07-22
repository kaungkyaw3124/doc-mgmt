import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.jwt_utils import decode_access_token
from app import models


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

    user = db.query(models.User).filter_by(username=payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=401, detail="user no longer exists")
    return user


def require_superuser(current_user: models.User = Depends(get_current_user)) -> models.User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="superuser privileges required")
    return current_user
