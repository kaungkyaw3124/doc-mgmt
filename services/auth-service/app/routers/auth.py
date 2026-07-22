import uuid

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.jwt_utils import create_access_token, decode_access_token
from app.core.security import hash_password, verify_password
from app.core.deps import require_superuser
from app.core.authz import user_has_service_access, get_user_allowed_project_ids, get_user_access_level
from app import models

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    username: str
    password: str
    requested_group_id: uuid.UUID | None = None


class RegisterResponse(BaseModel):
    username: str
    message: str = "Registration submitted. An administrator needs to approve your account before you can log in."


class PublicGroupOut(BaseModel):
    id: uuid.UUID
    name: str


class UserCreate(BaseModel):
    username: str
    password: str
    is_superuser: bool = False


class UserOut(BaseModel):
    username: str
    is_superuser: bool


@router.get("/groups-public", response_model=list[PublicGroupOut])
def list_groups_public(db: Session = Depends(get_db)):
    """
    Public (no auth) — just group names, so the registration form can offer
    "which group are you requesting to join?" without requiring a login.
    """
    groups = db.query(models.Group).order_by(models.Group.name).all()
    return [PublicGroupOut(id=g.id, name=g.name) for g in groups]


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """
    Public self-registration. Creates the account but leaves it unapproved —
    login is blocked until an admin approves it. If requested_group_id is
    given, that group's own admin(s) can approve it (not just a superuser) —
    see /admin/users/pending and /admin/users/{id}/approve.
    """
    existing = db.query(models.User).filter_by(username=payload.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="username already exists")

    if payload.requested_group_id:
        group = db.query(models.Group).filter_by(id=payload.requested_group_id).first()
        if not group:
            raise HTTPException(status_code=400, detail="requested group not found")

    user = models.User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_superuser=False,
        is_approved=False,
        requested_group_id=payload.requested_group_id,
    )
    db.add(user)
    db.commit()
    return RegisterResponse(username=user.username)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(username=payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="invalid username or password")

    if not user.is_approved:
        raise HTTPException(
            status_code=403,
            detail="Your account is pending administrator approval. Please check back later.",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account has been disabled. Contact an administrator.")

    token = create_access_token(subject=user.username)
    return TokenResponse(access_token=token)


@router.get("/verify")
def verify(
    response: Response,
    authorization: str | None = Header(default=None),
    x_service: str | None = Header(default=None, alias="X-Service"),
    db: Session = Depends(get_db),
):
    """
    Used internally by Nginx's auth_request directive — not meant to be
    called directly by clients. Nginx forwards the original Authorization
    header plus an X-Service header (set per-location in nginx.conf) naming
    which service is being accessed. Returns 200 if the token is valid AND
    the user has access to that service; 401 if the token itself is bad,
    403 if the token is valid but the user lacks access to this service.

    Also sets an X-Allowed-Projects response header ("ALL" or a comma-
    separated list of project IDs) which Nginx captures and forwards on to
    document-service, so it can filter document lists/lookups accordingly.
    """
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

    if not user.is_approved:
        raise HTTPException(status_code=403, detail="account pending approval")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")

    if x_service and not user_has_service_access(db, user, x_service):
        raise HTTPException(status_code=403, detail=f"user does not have access to '{x_service}'")

    allowed_projects = get_user_allowed_project_ids(db, user)
    response.headers["X-Allowed-Projects"] = (
        "ALL" if allowed_projects == "ALL" else ",".join(allowed_projects) if allowed_projects else "NONE"
    )
    response.headers["X-Username"] = user.username
    if x_service:
        response.headers["X-Access-Level"] = get_user_access_level(db, user, x_service)
    response.headers["X-Has-Audit-Log"] = str(user_has_service_access(db, user, "audit-log")).lower()
    response.headers["X-Has-Category-Access"] = str(user_has_service_access(db, user, "categories")).lower()

    return {"status": "ok"}


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),
):
    """
    Create a bare, pre-approved user account with no group membership yet
    (superuser only). To create a user AND add them to a group in one step,
    group admins should use POST /groups/{group_id}/users instead.
    """
    existing = db.query(models.User).filter_by(username=payload.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="username already exists")

    user = models.User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_superuser=payload.is_superuser,
        is_approved=True,  # created directly by a superuser, so no approval wait needed
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(username=user.username, is_superuser=user.is_superuser)
