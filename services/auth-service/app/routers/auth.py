import uuid
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.jwt_utils import create_access_token, decode_access_token
from app.core.security import hash_password, verify_password
from app.core.deps import get_current_user, require_live_session, require_superuser
from app.core.authz import user_has_service_access, get_user_allowed_project_ids, get_user_access_level
from app.core.rate_limit import RateLimitExceeded, check_login_rate_limit, get_client_ip, record_failed_attempt
from app.core.refresh_tokens import (
    active_sessions, issue_refresh_token, revoke_all_user_tokens, revoke_refresh_token, rotate_refresh_token,
    session_end, token_row,
)
from app import models

router = APIRouter(tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
# Root-scoped. This USED to be "/api/auth" on the theory that a browser
# matches Cookie Path against the URL it actually requested — true, but
# that URL is always /api/auth/* only via Nginx's rewrite (the frontend
# only ever calls API_BASE + '/auth/...', see web/index.html); this
# service's OWN routes are mounted at bare paths ("/login", "/refresh",
# "/logout" — no "/api/auth" prefix inside the container, see main.py).
# A caller that talks to those bare paths directly — this task's own
# real end-to-end test suite, TestClient, being one — would never get
# the cookie attached, since Path is a browser/HTTP-client-side
# same-request-shape filter with no server-side enforcement at all
# (this fix is what re-verifying Task 6 end-to-end, not just trusting
# the design comment, actually caught). Scoping to "/" costs nothing
# meaningful here: HttpOnly is what actually keeps this cookie away
# from JavaScript, and every other endpoint in this single-origin app
# simply never reads a cookie it doesn't look for.
REFRESH_COOKIE_PATH = "/"


def _set_refresh_cookie(response: Response, plaintext: str, row: models.RefreshToken) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=plaintext,
        # Same lifetime as the token itself (idle timeout or session end,
        # whichever is sooner), so the browser drops it when it's useless.
        max_age=max(0, int((row.expires_at - datetime.now(timezone.utc)).total_seconds())),
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        # Secure requires HTTPS — only turned on in production, where TLS
        # termination is assumed (see docs/deployment.md). Forcing it on
        # in dev (plain http://localhost:8080) would make the browser
        # silently refuse to ever send the cookie back.
        secure=(settings.environment == "production"),
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionInfo(BaseModel):
    idle_timeout_s: int  # the UI logs out after this long without user activity
    warning_s: int       # ...showing its warning this long before
    expires_at: datetime  # the session's hard end (login + session_max_hours)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    session: SessionInfo | None = None


def _session_tokens(response: Response, user: models.User, plaintext: str, row: models.RefreshToken) -> TokenResponse:
    """Sets the refresh cookie and mints the access token for a session's current token."""
    _set_refresh_cookie(response, plaintext, row)
    end = session_end(row)
    return TokenResponse(
        access_token=create_access_token(subject=str(user.id), session_id=row.session_id, session_end=end),
        session=SessionInfo(
            idle_timeout_s=int(settings.session_idle_timeout_minutes * 60),
            warning_s=settings.session_idle_warning_seconds,
            expires_at=end,
        ),
    )


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
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    client_ip = get_client_ip(request)

    try:
        check_login_rate_limit(db, client_ip, payload.username)
    except RateLimitExceeded as exc:
        # Same 429 + generic message regardless of whether the account
        # exists, and regardless of which of the three limits tripped —
        # nothing here should let a caller distinguish "real account,
        # rate-limited" from "no such account, rate-limited".
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    user = db.query(models.User).filter_by(username=payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        record_failed_attempt(db, client_ip, payload.username)
        raise HTTPException(status_code=401, detail="invalid username or password")

    if not user.is_approved:
        raise HTTPException(
            status_code=403,
            detail="Your account is pending administrator approval. Please check back later.",
        )

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account has been disabled. Contact an administrator.")

    refresh_plaintext = issue_refresh_token(
        db, user, user_agent=request.headers.get("user-agent"), ip=client_ip,
    )
    return _session_tokens(response, user, refresh_plaintext, token_row(db, refresh_plaintext))


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Mints a new access token from the HttpOnly refresh cookie, rotating
    the refresh token in the process (the presented one is revoked, a new
    one takes its place in the cookie) — called by the frontend on page
    load (silent re-auth) and transparently when an API call gets a 401
    for an expired access token. 401 if the cookie is missing, expired,
    already-used (replayed), or belongs to a since-disabled/un-approved
    user.
    """
    presented = request.cookies.get(REFRESH_COOKIE_NAME)
    result = rotate_refresh_token(db, presented, ip=get_client_ip(request))
    if result is None:
        # Returned, not raised: an HTTPException builds a fresh response,
        # which would drop the cookie deletion set on `response`.
        rejected = JSONResponse(status_code=401, content={"detail": "invalid or expired session"})
        _clear_refresh_cookie(rejected)
        return rejected

    user, new_plaintext, row = result
    return _session_tokens(response, user, new_plaintext, row)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Revokes the current refresh token server-side (not just a client-
    side "forget the token" — the cookie's token can no longer be used to
    mint a new access token even if it leaks) and clears the cookie.
    Always succeeds, even with no/an already-invalid cookie."""
    presented = request.cookies.get(REFRESH_COOKIE_NAME)
    revoke_refresh_token(db, presented)
    _clear_refresh_cookie(response)


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

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=401, detail="invalid token")

    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="user no longer exists")

    if not user.is_approved:
        raise HTTPException(status_code=403, detail="account pending approval")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="account disabled")

    require_live_session(db, user_id, payload)

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
    response.headers["X-Has-Document-Delete"] = str(user_has_service_access(db, user, "documents-delete")).lower()

    return {"status": "ok"}


def session_out(row: models.RefreshToken, current_sid=None) -> dict:
    return {
        "id": str(row.session_id),
        "started_at": row.session_started_at,
        "last_activity_at": row.last_activity_at,
        "user_agent": row.user_agent,
        "ip": row.ip,
        "current": current_sid is not None and str(row.session_id) == str(current_sid),
    }


@router.get("/sessions")
def list_my_sessions(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The caller's own live sessions; `current` marks the one this request is from."""
    sid = decode_access_token(authorization.removeprefix("Bearer ").strip()).get("sid")
    return [session_out(row, sid) for row in active_sessions(db, current_user.id)]


@router.post("/logout-all", status_code=204)
def logout_all(
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Ends every session of the caller, this one included ("log out all devices")."""
    revoke_all_user_tokens(db, current_user.id)
    _clear_refresh_cookie(response)


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
