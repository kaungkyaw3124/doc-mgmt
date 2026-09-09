import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_superuser
from app.core.security import hash_password
from app.core.authz import can_manage_group, is_member_of, user_has_service_access
from app.core.refresh_tokens import revoke_all_user_tokens
from app import models

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_SERVICES = {"documents", "products", "search", "audit-log", "categories"}


# ---------- Pending user approval ----------

def _admin_group_ids(db: Session, user: models.User):
    """Group IDs this user is a group-admin of (empty for superuser — they
    don't need this, they can see/approve everything regardless)."""
    memberships = db.query(models.UserGroup).filter_by(user_id=user.id, is_group_admin=True).all()
    return [m.group_id for m in memberships]


@router.get("/users/pending")
def list_pending_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Superuser sees every pending registration. A group admin sees only
    pending registrations that requested to join a group they administer
    (registrations with no requested group, or a different group, are
    invisible to them — only a superuser can act on those).
    """
    query = db.query(models.User).filter_by(is_approved=False)
    if not current_user.is_superuser:
        admin_group_ids = _admin_group_ids(db, current_user)
        if not admin_group_ids:
            return []
        query = query.filter(models.User.requested_group_id.in_(admin_group_ids))
    users = query.order_by(models.User.created_at).all()
    return [
        {
            "id": str(u.id),
            "username": u.username,
            "created_at": u.created_at,
            "requested_group_id": str(u.requested_group_id) if u.requested_group_id else None,
        }
        for u in users
    ]


@router.post("/users/{user_id}/approve")
def approve_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Superuser can approve anyone. A group admin can approve only users who
    requested to join a group they administer — approving automatically
    also adds them to that group as a regular member (not group admin).
    """
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if not current_user.is_superuser:
        if not user.requested_group_id or not can_manage_group(db, current_user, user.requested_group_id):
            raise HTTPException(
                status_code=403,
                detail="you can only approve users who requested to join a group you administer",
            )

    user.is_approved = True

    added_to_group = False
    if user.requested_group_id:
        existing_membership = (
            db.query(models.UserGroup)
            .filter_by(user_id=user.id, group_id=user.requested_group_id)
            .first()
        )
        if not existing_membership:
            db.add(models.UserGroup(user_id=user.id, group_id=user.requested_group_id, is_group_admin=False))
            added_to_group = True

    db.commit()
    return {"username": user.username, "is_approved": True, "added_to_group": added_to_group}


@router.post("/users/{user_id}/deny", status_code=204)
def deny_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Reject a pending registration outright — deletes the account entirely
    (nothing else is tied to it yet, since it was never approved). Same
    scoping as approve: superuser can deny anyone, a group admin only
    requests aimed at a group they administer.
    """
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if user.is_approved:
        raise HTTPException(status_code=400, detail="this user is already approved — use remove instead")

    if not current_user.is_superuser:
        if not user.requested_group_id or not can_manage_group(db, current_user, user.requested_group_id):
            raise HTTPException(
                status_code=403,
                detail="you can only deny users who requested to join a group you administer",
            )

    db.delete(user)
    db.commit()


@router.post("/users/{user_id}/reject", status_code=204)
def reject_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Deny a pending registration — deletes the account outright (it was
    never approved, so it never had any access to lose). Same permission
    scoping as approve: superuser can reject anyone; a group admin can
    only reject users who requested to join a group they administer.
    If they still want access, they can just register again.
    """
    user = db.query(models.User).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    if user.is_approved:
        raise HTTPException(status_code=400, detail="this user is already approved — use group removal instead")

    if not current_user.is_superuser:
        if not user.requested_group_id or not can_manage_group(db, current_user, user.requested_group_id):
            raise HTTPException(
                status_code=403,
                detail="you can only reject users who requested to join a group you administer",
            )

    db.delete(user)
    db.commit()


# ---------- User management (username/password/roles) ----------
# Only a superuser, or a group admin (for users in their own group), can
# manage user accounts here — no separate grantable permission for this,
# unlike documents/products/search/audit-log/categories.

def _can_manage_users(db: Session, user: models.User) -> bool:
    """Superuser, or a group admin of at least one group — no separate
    permission grant needed; being a group admin includes managing your
    own group's users directly."""
    if user.is_superuser:
        return True
    return bool(_admin_group_ids(db, user))


def _require_can_manage_users(db: Session, user: models.User):
    if not _can_manage_users(db, user):
        raise HTTPException(status_code=403, detail="you don't have permission to manage users")


@router.get("/users")
def list_all_users(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_users(db, current_user)

    if current_user.is_superuser:
        users = db.query(models.User).order_by(models.User.username).all()
    else:
        admin_group_ids = _admin_group_ids(db, current_user)
        user_ids = [
            m.user_id for m in
            db.query(models.UserGroup).filter(models.UserGroup.group_id.in_(admin_group_ids)).all()
        ]
        users = (
            db.query(models.User)
            .filter(models.User.id.in_(user_ids))
            .order_by(models.User.username)
            .all()
        )

    # Groups/roles inline per user (rather than making the frontend fetch
    # each user's roles separately — see GET /users/{id}/roles) so the
    # "All users" table can show Group/Role in one request.
    user_ids = [u.id for u in users]
    memberships_by_user: dict = {}
    if user_ids:
        for m in db.query(models.UserGroup).filter(models.UserGroup.user_id.in_(user_ids)).all():
            memberships_by_user.setdefault(m.user_id, []).append(m)

    role_assignments_by_user: dict = {}
    if user_ids:
        for a in db.query(models.UserRole).filter(models.UserRole.user_id.in_(user_ids)).all():
            role_assignments_by_user.setdefault(a.user_id, []).append(a)

    all_group_ids = {m.group_id for ms in memberships_by_user.values() for m in ms}
    groups_by_id = {g.id: g for g in db.query(models.Group).filter(models.Group.id.in_(all_group_ids)).all()} if all_group_ids else {}

    all_role_ids = {a.role_id for ras in role_assignments_by_user.values() for a in ras}
    roles_by_id = {r.id: r for r in db.query(models.Role).filter(models.Role.id.in_(all_role_ids)).all()} if all_role_ids else {}

    return [
        {
            "id": str(u.id),
            "username": u.username,
            "is_superuser": u.is_superuser,
            "is_approved": u.is_approved,
            "is_active": u.is_active,
            "groups": sorted({
                groups_by_id[m.group_id].name
                for m in memberships_by_user.get(u.id, [])
                if m.group_id in groups_by_id
            }),
            "roles": sorted({
                roles_by_id[a.role_id].name
                for a in role_assignments_by_user.get(u.id, [])
                if a.role_id in roles_by_id
            }),
        }
        for u in users
    ]


@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(status_code=403, detail="you can only view users who belong to a group you administer")

    assignments = db.query(models.UserRole).filter_by(user_id=user_id).all()
    result = []
    for a in assignments:
        role = db.query(models.Role).filter_by(id=a.role_id).first()
        if not role:
            continue
        group = db.query(models.Group).filter_by(id=role.group_id).first()
        result.append({
            "role_id": str(role.id),
            "role_name": role.name,
            "group_id": str(role.group_id),
            "group_name": group.name if group else "—",
        })
    return result


@router.get("/users/{user_id}/groups")
def get_user_groups(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Raw group memberships (unlike /roles, includes a group the user
    belongs to even if they have no role assigned in it yet) — used by the
    'Manage user' UI's group section."""
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(status_code=403, detail="you can only view users who belong to a group you administer")

    memberships = db.query(models.UserGroup).filter_by(user_id=user_id).all()
    result = []
    for m in memberships:
        group = db.query(models.Group).filter_by(id=m.group_id).first()
        if not group:
            continue
        result.append({
            "group_id": str(group.id),
            "group_name": group.name,
            "is_group_admin": m.is_group_admin,
        })
    return result


class UserEdit(BaseModel):
    username: str | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
def edit_user(
    user_id: uuid.UUID,
    payload: UserEdit,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Change a user's username and/or password. Does NOT touch is_superuser —
    that stays out of reach here entirely, promote/demote superusers only
    via direct database access. A group admin can only edit users who
    belong to a group they administer, and never a superuser's account.
    """
    _require_can_manage_users(db, current_user)

    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if target.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="only a superuser can edit a superuser's account")

    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(status_code=403, detail="you can only edit users who belong to a group you administer")

    if payload.username and payload.username != target.username:
        existing = db.query(models.User).filter_by(username=payload.username).first()
        if existing:
            raise HTTPException(status_code=409, detail="that username is already taken")
        target.username = payload.username

    password_changed = bool(payload.password)
    if payload.password:
        target.hashed_password = hash_password(payload.password)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="that username is already taken")

    if password_changed:
        # A password change invalidates any outstanding refresh token —
        # whoever set the new password is the only one who should be able
        # to mint fresh access tokens from here on (e.g. an admin
        # resetting a compromised account's password shouldn't leave the
        # old session silently refreshable).
        revoke_all_user_tokens(db, target.id)

    return {"id": str(target.id), "username": target.username}


def _target_is_in_an_administered_group(db: Session, current_user: models.User, target: models.User) -> bool:
    """True if the target user is a member of at least one group current_user administers."""
    admin_group_ids = _admin_group_ids(db, current_user)
    if not admin_group_ids:
        return False
    membership = (
        db.query(models.UserGroup)
        .filter(models.UserGroup.user_id == target.id, models.UserGroup.group_id.in_(admin_group_ids))
        .first()
    )
    return membership is not None


# NOTE: there is deliberately no DELETE /users/{user_id} endpoint. User
# Control requires that an approved account can never be deleted, only
# deactivated (see PATCH /users/{user_id}/active) — deactivating already
# blocks login, refresh, and every protected API call (see auth.py's
# /login, /verify, /refresh), and revokes any outstanding refresh tokens.
# A still-pending (never-approved) registration is different — it never
# had real access to lose — and can still be rejected via POST
# /users/{user_id}/deny or /reject above.


class UserActiveUpdate(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: uuid.UUID,
    payload: UserActiveUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Suspend/unsuspend a user without deleting or un-approving them — a
    disabled user is blocked from logging in (and any existing session is
    cut off on their very next request) until re-enabled. Same scoping as
    remove: superuser can toggle anyone; a group admin (or "users" grant
    holder) only users in a group they administer, and never a superuser.
    """
    _require_can_manage_users(db, current_user)

    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if target.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="only a superuser can suspend a superuser's account")

    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="you can't disable your own account")

    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(
            status_code=403,
            detail="you can only suspend users who belong to a group you administer",
        )

    target.is_active = payload.is_active
    db.commit()

    if not target.is_active:
        # /verify already blocks a disabled user's very next API request
        # (checked fresh from the DB every time), but their refresh
        # cookie — if they have one — could otherwise still mint new
        # access tokens. Revoke it too, so disabling truly cuts them off.
        revoke_all_user_tokens(db, target.id)

    return {"id": str(target.id), "is_active": target.is_active}


# ---------- Groups ----------

class GroupCreate(BaseModel):
    name: str


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool = True


@router.post("/groups", response_model=GroupOut, status_code=201)
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),  # only superuser creates groups
):
    existing = db.query(models.Group).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="group already exists")

    group = models.Group(name=payload.name)
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="group already exists")
    db.refresh(group)
    return GroupOut(id=group.id, name=group.name, is_active=group.is_active)


@router.get("/groups", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if current_user.is_superuser:
        groups = db.query(models.Group).all()
    else:
        groups = (
            db.query(models.Group)
            .join(models.UserGroup, models.UserGroup.group_id == models.Group.id)
            .filter(models.UserGroup.user_id == current_user.id)
            .all()
        )
    return [GroupOut(id=g.id, name=g.name, is_active=g.is_active) for g in groups]


class GroupActiveUpdate(BaseModel):
    is_active: bool


@router.patch("/groups/{group_id}/active")
def set_group_active(
    group_id: uuid.UUID,
    payload: GroupActiveUpdate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),
):
    """
    Enable/disable a group without deleting it. A disabled group stops
    granting any access via ALL of its roles to everyone in it — as if
    every role in the group were disabled — but everything (members,
    roles, grants) is preserved for when it's re-enabled.
    """
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="group not found")
    group.is_active = payload.is_active
    db.commit()
    return {"id": str(group.id), "is_active": group.is_active}


@router.delete("/groups/{group_id}", status_code=204)
def delete_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),
):
    """Permanently deletes the group, along with its members, roles, and
    those roles' access/project grants (cascade). This can't be undone —
    disable the group instead (PATCH /groups/{id}/active) if you might
    want it back later."""
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="group not found")

    # Strip any direct project grants members only held via this group's
    # pool — otherwise deleting the group leaves orphaned UserProjectAccess
    # rows behind, same cleanup revoke_group_project_access does per-project.
    # Skip a (user, project) pair if the user still has that project via a
    # DIFFERENT group they also belong to — deleting this group shouldn't
    # silently take away access that's still legitimately granted elsewhere.
    member_user_ids = [
        m.user_id for m in db.query(models.UserGroup).filter_by(group_id=group_id).all()
    ]
    pool_project_ids = [
        g.project_id for g in db.query(models.GroupProjectAccess).filter_by(group_id=group_id).all()
    ]
    for project_id in pool_project_ids:
        if not member_user_ids:
            break
        other_groups_with_project = [
            g.group_id
            for g in db.query(models.GroupProjectAccess)
            .filter(
                models.GroupProjectAccess.project_id == project_id,
                models.GroupProjectAccess.group_id != group_id,
            )
            .all()
        ]
        still_covered_user_ids = set()
        if other_groups_with_project:
            still_covered_user_ids = {
                m.user_id
                for m in db.query(models.UserGroup)
                .filter(
                    models.UserGroup.group_id.in_(other_groups_with_project),
                    models.UserGroup.user_id.in_(member_user_ids),
                )
                .all()
            }
        user_ids_to_strip = [uid for uid in member_user_ids if uid not in still_covered_user_ids]
        if user_ids_to_strip:
            db.query(models.UserProjectAccess).filter(
                models.UserProjectAccess.user_id.in_(user_ids_to_strip),
                models.UserProjectAccess.project_id == project_id,
            ).delete(synchronize_session=False)

    db.delete(group)
    db.commit()


# ---------- Group membership ----------

class AddMemberRequest(BaseModel):
    username: str
    is_group_admin: bool = False


class CreateUserInGroupRequest(BaseModel):
    username: str
    password: str
    is_group_admin: bool = False
    role_id: uuid.UUID | None = None  # must be a role belonging to this same group, e.g. Editor/Viewer


def _require_can_manage_group(db: Session, current_user: models.User, group_id: uuid.UUID):
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="group not found")
    if not can_manage_group(db, current_user, group_id):
        raise HTTPException(status_code=403, detail="not authorized to manage this group")
    return group


@router.post("/groups/{group_id}/users", status_code=201)
def create_user_in_group(
    group_id: uuid.UUID,
    payload: CreateUserInGroupRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Create a brand-new user and add them to this group in one step —
    approved and active immediately (no self-registration wait). Callable
    by superuser (any group) or that group's own admin(s). Never accepts
    is_superuser — there is no field for it here at all; promoting to
    superuser stays out of reach of this UI/endpoint entirely. If role_id
    is given, it must be a role belonging to this same group (e.g. this
    group's Editor or Viewer) — assigned in the same transaction so
    permissions are correct from the very first login."""
    group = _require_can_manage_group(db, current_user, group_id)

    existing = db.query(models.User).filter_by(username=payload.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="username already exists")

    role = None
    if payload.role_id is not None:
        role = db.query(models.Role).filter_by(id=payload.role_id, group_id=group.id).first()
        if not role:
            raise HTTPException(status_code=400, detail="role_id must be a role belonging to this group")

    user = models.User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        is_approved=True,  # created directly by an authorized admin, so no approval wait needed
        is_active=True,
    )
    db.add(user)
    db.flush()  # get user.id without committing yet

    membership = models.UserGroup(
        user_id=user.id, group_id=group_id, is_group_admin=payload.is_group_admin
    )
    db.add(membership)
    if role is not None:
        db.add(models.UserRole(user_id=user.id, role_id=role.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="username already exists")
    return {
        "username": user.username,
        "group_id": str(group_id),
        "is_group_admin": payload.is_group_admin,
        "role_id": str(role.id) if role else None,
        "role_name": role.name if role else None,
    }


@router.post("/groups/{group_id}/members", status_code=201)
def add_existing_member(
    group_id: uuid.UUID,
    payload: AddMemberRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Add an existing user to this group. Superuser: any group. Group admin: own group only."""
    _require_can_manage_group(db, current_user, group_id)

    target_user = db.query(models.User).filter_by(username=payload.username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="user not found")

    existing_membership = (
        db.query(models.UserGroup).filter_by(user_id=target_user.id, group_id=group_id).first()
    )
    if existing_membership:
        raise HTTPException(status_code=409, detail="user is already a member of this group")

    membership = models.UserGroup(
        user_id=target_user.id, group_id=group_id, is_group_admin=payload.is_group_admin
    )
    db.add(membership)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="user is already a member of this group")
    return {"username": target_user.username, "group_id": str(group_id)}


@router.delete("/groups/{group_id}/members/{username}", status_code=204)
def remove_member(
    group_id: uuid.UUID,
    username: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Remove a user from this group (revokes any roles/access they had via
    this group). This is the group-admin equivalent of 'delete user' — it
    does not delete the user's account itself, since they may belong to
    other groups. Superuser: any group. Group admin: own group only."""
    _require_can_manage_group(db, current_user, group_id)

    target_user = db.query(models.User).filter_by(username=username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="user not found")

    membership = (
        db.query(models.UserGroup).filter_by(user_id=target_user.id, group_id=group_id).first()
    )
    if not membership:
        raise HTTPException(status_code=404, detail="user is not a member of this group")

    # Also drop any role assignments the user has via roles belonging to this group
    role_ids_in_group = [r.id for r in db.query(models.Role).filter_by(group_id=group_id).all()]
    if role_ids_in_group:
        db.query(models.UserRole).filter(
            models.UserRole.user_id == target_user.id,
            models.UserRole.role_id.in_(role_ids_in_group),
        ).delete(synchronize_session=False)

    db.delete(membership)
    db.commit()


# ---------- Roles ----------

class RoleCreate(BaseModel):
    name: str


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    group_id: uuid.UUID
    services: list[str] = []
    is_active: bool = True


@router.post("/groups/{group_id}/roles", response_model=RoleOut, status_code=201)
def create_role(
    group_id: uuid.UUID,
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_group(db, current_user, group_id)

    existing = db.query(models.Role).filter_by(name=payload.name, group_id=group_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="role already exists in this group")

    role = models.Role(name=payload.name, group_id=group_id)
    db.add(role)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="role already exists in this group")
    db.refresh(role)
    return RoleOut(id=role.id, name=role.name, group_id=role.group_id, services=[], is_active=role.is_active)


@router.get("/groups/{group_id}/roles", response_model=list[RoleOut])
def list_roles_in_group(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not (current_user.is_superuser or is_member_of(db, current_user, group_id)):
        raise HTTPException(status_code=403, detail="not authorized to view this group")
    roles = db.query(models.Role).filter_by(group_id=group_id).all()
    return [
        RoleOut(
            id=r.id, name=r.name, group_id=r.group_id,
            services=[g.service_name for g in r.access_grants],
            is_active=r.is_active,
        )
        for r in roles
    ]


@router.get("/groups/{group_id}/members")
def list_group_members(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if not (current_user.is_superuser or is_member_of(db, current_user, group_id)):
        raise HTTPException(status_code=403, detail="not authorized to view this group")
    memberships = db.query(models.UserGroup).filter_by(group_id=group_id).all()
    result = []
    for m in memberships:
        user = db.query(models.User).filter_by(id=m.user_id).first()
        if user:
            result.append({"username": user.username, "is_group_admin": m.is_group_admin})
    return result


def _require_can_manage_role(db: Session, current_user: models.User, role_id: uuid.UUID) -> models.Role:
    role = db.query(models.Role).filter_by(id=role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    if not can_manage_group(db, current_user, role.group_id):
        raise HTTPException(status_code=403, detail="not authorized to manage this role")
    return role


# ---------- Role access (which services a role grants) ----------

class AccessGrant(BaseModel):
    service_name: str
    access_level: str = "edit"  # "view" | "edit"


@router.post("/roles/{role_id}/access", status_code=201)
def grant_access(
    role_id: uuid.UUID,
    payload: AccessGrant,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    role = _require_can_manage_role(db, current_user, role_id)

    if payload.service_name not in VALID_SERVICES:
        raise HTTPException(
            status_code=422, detail=f"service_name must be one of {sorted(VALID_SERVICES)}"
        )
    if payload.access_level not in {"view", "edit"}:
        raise HTTPException(status_code=422, detail="access_level must be 'view' or 'edit'")

    existing = (
        db.query(models.RoleAccess)
        .filter_by(role_id=role_id, service_name=payload.service_name)
        .first()
    )
    if existing:
        existing.access_level = payload.access_level
        db.commit()
        return {"role_id": str(role_id), "service_name": payload.service_name, "access_level": payload.access_level}

    grant = models.RoleAccess(role_id=role_id, service_name=payload.service_name, access_level=payload.access_level)
    db.add(grant)
    try:
        db.commit()
    except IntegrityError:
        # lost a race with a concurrent grant for the same role+service —
        # harmless, the other request already created the row we wanted
        db.rollback()
    return {"role_id": str(role_id), "service_name": payload.service_name, "access_level": payload.access_level}


@router.delete("/roles/{role_id}/access/{service_name}", status_code=204)
def revoke_access(
    role_id: uuid.UUID,
    service_name: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_role(db, current_user, role_id)

    grant = (
        db.query(models.RoleAccess).filter_by(role_id=role_id, service_name=service_name).first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="role does not have this access grant")

    db.delete(grant)
    db.commit()


@router.get("/roles/{role_id}")
def get_role_detail(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    role = db.query(models.Role).filter_by(id=role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    if not (current_user.is_superuser or is_member_of(db, current_user, role.group_id)):
        raise HTTPException(status_code=403, detail="not authorized to view this role")

    assigned_usernames = []
    for ur in role.user_assignments:
        user = db.query(models.User).filter_by(id=ur.user_id).first()
        if user:
            assigned_usernames.append(user.username)

    return {
        "id": str(role.id),
        "name": role.name,
        "group_id": str(role.group_id),
        "is_active": role.is_active,
        "services": [g.service_name for g in role.access_grants],
        "service_levels": {g.service_name: g.access_level for g in role.access_grants},
        "assigned_users": assigned_usernames,
    }


class RoleActiveUpdate(BaseModel):
    is_active: bool


class RoleRename(BaseModel):
    name: str


@router.patch("/roles/{role_id}/rename")
def rename_role(
    role_id: uuid.UUID,
    payload: RoleRename,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    role = _require_can_manage_role(db, current_user, role_id)
    existing = (
        db.query(models.Role)
        .filter(models.Role.group_id == role.group_id, models.Role.name == payload.name, models.Role.id != role_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="another role in this group already has that name")
    role.name = payload.name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="another role in this group already has that name")
    return {"id": str(role.id), "name": role.name}


@router.patch("/roles/{role_id}/active")
def set_role_active(
    role_id: uuid.UUID,
    payload: RoleActiveUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Enable/disable a role without deleting it. A disabled role stops
    granting any access (service, level, or project) to everyone it's
    assigned to — as if they were unassigned — but the role definition,
    its grants, and its assignments are all preserved for when it's
    re-enabled.
    """
    role = _require_can_manage_role(db, current_user, role_id)
    role.is_active = payload.is_active
    db.commit()
    return {"id": str(role.id), "is_active": role.is_active}


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Permanently deletes the role, along with its service/project grants
    and user assignments (cascade). This can't be undone — disable the role
    instead (PATCH /roles/{id}/active) if you might want it back later."""
    role = _require_can_manage_role(db, current_user, role_id)
    db.delete(role)
    db.commit()


# ---------- Project access (group pool + per-user grants) ----------
# Roles carry only service permissions now (documents/products/search/etc).
# Project visibility is granted directly to a user (UserProjectAccess),
# drawn from their group's pool (GroupProjectAccess) unless the granter
# is a superuser.

class GroupProjectAccessGrant(BaseModel):
    project_id: uuid.UUID


@router.post("/groups/{group_id}/project-access", status_code=201)
def grant_group_project_access(
    group_id: uuid.UUID,
    payload: GroupProjectAccessGrant,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),
):
    """
    Superuser only: adds a project to this group's pool. The group's own
    admin(s) can then grant individual users access to any project already
    in this pool (but nothing outside it) via POST /users/{id}/project-access.
    """
    group = db.query(models.Group).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="group not found")

    existing = (
        db.query(models.GroupProjectAccess)
        .filter_by(group_id=group_id, project_id=payload.project_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="group already has access to this project")

    db.add(models.GroupProjectAccess(group_id=group_id, project_id=payload.project_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="group already has access to this project")
    return {"group_id": str(group_id), "project_id": str(payload.project_id)}


@router.delete("/groups/{group_id}/project-access/{project_id}", status_code=204)
def revoke_group_project_access(
    group_id: uuid.UUID,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(require_superuser),
):
    """
    Superuser only. Also strips that project from every member of this
    group who currently has it granted directly — otherwise someone could
    keep a grant the group itself no longer has, a confusing orphaned
    permission.
    """
    grant = (
        db.query(models.GroupProjectAccess).filter_by(group_id=group_id, project_id=project_id).first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="group does not have access to this project")

    member_user_ids = [
        m.user_id for m in db.query(models.UserGroup).filter_by(group_id=group_id).all()
    ]
    if member_user_ids:
        # Don't strip access from a member who still has this project via a
        # DIFFERENT group's pool they also belong to — otherwise revoking it
        # from this group silently takes away access that's still
        # legitimately granted through another membership.
        other_groups_with_project = [
            g.group_id
            for g in db.query(models.GroupProjectAccess)
            .filter(
                models.GroupProjectAccess.project_id == project_id,
                models.GroupProjectAccess.group_id != group_id,
            )
            .all()
        ]
        still_covered_user_ids = set()
        if other_groups_with_project:
            still_covered_user_ids = {
                m.user_id
                for m in db.query(models.UserGroup)
                .filter(
                    models.UserGroup.group_id.in_(other_groups_with_project),
                    models.UserGroup.user_id.in_(member_user_ids),
                )
                .all()
            }
        user_ids_to_strip = [uid for uid in member_user_ids if uid not in still_covered_user_ids]
        if user_ids_to_strip:
            db.query(models.UserProjectAccess).filter(
                models.UserProjectAccess.user_id.in_(user_ids_to_strip),
                models.UserProjectAccess.project_id == project_id,
            ).delete(synchronize_session=False)

    db.delete(grant)
    db.commit()


@router.get("/groups/{group_id}/project-access")
def list_group_project_access(
    group_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Anyone who can see this group (superuser or a member) can see its
    project pool — needed so a group admin knows what they're able to grant."""
    if not (current_user.is_superuser or is_member_of(db, current_user, group_id)):
        raise HTTPException(status_code=403, detail="not authorized to view this group")
    grants = db.query(models.GroupProjectAccess).filter_by(group_id=group_id).all()
    return [str(g.project_id) for g in grants]


class UserProjectAccessGrant(BaseModel):
    project_id: uuid.UUID


def _shared_administered_group_ids(db: Session, current_user: models.User, target: models.User):
    """Group IDs where current_user is a group admin AND target is a member."""
    admin_group_ids = set(_admin_group_ids(db, current_user))
    target_group_ids = {
        m.group_id for m in db.query(models.UserGroup).filter_by(user_id=target.id).all()
    }
    return admin_group_ids & target_group_ids


@router.get("/users/{user_id}/available-projects")
def get_available_projects_for_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Which projects the CALLER is allowed to grant THIS target user.
    Superuser: every project (frontend already has the full list, but this
    endpoint stays consistent for callers that want to check). Group admin:
    the union of pools from groups they administer that the target belongs to.
    """
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if current_user.is_superuser:
        return {"all": True, "project_ids": []}

    shared_group_ids = _shared_administered_group_ids(db, current_user, target)
    if not shared_group_ids:
        return {"all": False, "project_ids": []}

    grants = (
        db.query(models.GroupProjectAccess)
        .filter(models.GroupProjectAccess.group_id.in_(shared_group_ids))
        .all()
    )
    return {"all": False, "project_ids": sorted({str(g.project_id) for g in grants})}


@router.get("/users/{user_id}/project-access")
def list_user_project_access(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(status_code=403, detail="you can only view users who belong to a group you administer")

    grants = db.query(models.UserProjectAccess).filter_by(user_id=user_id).all()
    return [str(g.project_id) for g in grants]


@router.post("/users/{user_id}/project-access", status_code=201)
def grant_user_project_access(
    user_id: uuid.UUID,
    payload: UserProjectAccessGrant,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Grant a specific user visibility into one project's documents. A
    superuser can grant any project to anyone. A group admin can only
    grant a project that's in the pool of a group they administer AND
    that the target user actually belongs to.
    """
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")

    if not current_user.is_superuser:
        shared_group_ids = _shared_administered_group_ids(db, current_user, target)
        if not shared_group_ids:
            raise HTTPException(status_code=403, detail="you can only manage users who belong to a group you administer")

        in_pool = (
            db.query(models.GroupProjectAccess)
            .filter(
                models.GroupProjectAccess.group_id.in_(shared_group_ids),
                models.GroupProjectAccess.project_id == payload.project_id,
            )
            .first()
        )
        if not in_pool:
            raise HTTPException(
                status_code=403,
                detail="your group doesn't have access to this project — ask a superuser to grant it to your group first",
            )

    existing = (
        db.query(models.UserProjectAccess)
        .filter_by(user_id=user_id, project_id=payload.project_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="user already has access to this project")

    db.add(models.UserProjectAccess(user_id=user_id, project_id=payload.project_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="user already has access to this project")
    return {"user_id": str(user_id), "project_id": str(payload.project_id)}


@router.delete("/users/{user_id}/project-access/{project_id}", status_code=204)
def revoke_user_project_access(
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Mirrors grant_user_project_access's scoping: a superuser can revoke
    anything, but a group admin can only revoke a grant that falls within a
    group they actually administer — not just any project on a user who
    happens to share some administered group with them (the user could
    have this project via a *different* group's pool).
    """
    _require_can_manage_users(db, current_user)
    target = db.query(models.User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="user not found")
    if not current_user.is_superuser and not _target_is_in_an_administered_group(db, current_user, target):
        raise HTTPException(status_code=403, detail="you can only manage users who belong to a group you administer")

    if not current_user.is_superuser:
        shared_group_ids = _shared_administered_group_ids(db, current_user, target)
        in_pool = (
            db.query(models.GroupProjectAccess)
            .filter(
                models.GroupProjectAccess.group_id.in_(shared_group_ids),
                models.GroupProjectAccess.project_id == project_id,
            )
            .first()
            if shared_group_ids
            else None
        )
        if not in_pool:
            raise HTTPException(
                status_code=403,
                detail="this project isn't in the pool of a group you administer for this user",
            )

    grant = (
        db.query(models.UserProjectAccess).filter_by(user_id=user_id, project_id=project_id).first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="user does not have access to this project")

    db.delete(grant)
    db.commit()


# ---------- Assigning users to roles ----------

class RoleAssignRequest(BaseModel):
    username: str


@router.post("/roles/{role_id}/assign", status_code=201)
def assign_role(
    role_id: uuid.UUID,
    payload: RoleAssignRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    role = _require_can_manage_role(db, current_user, role_id)

    target_user = db.query(models.User).filter_by(username=payload.username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="user not found")

    if not is_member_of(db, target_user, role.group_id):
        raise HTTPException(
            status_code=400,
            detail="user must be a member of this role's group before being assigned the role",
        )

    existing = db.query(models.UserRole).filter_by(user_id=target_user.id, role_id=role_id).first()
    if existing:
        raise HTTPException(status_code=409, detail="user already has this role")

    assignment = models.UserRole(user_id=target_user.id, role_id=role_id)
    db.add(assignment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="user already has this role")
    return {"username": target_user.username, "role_id": str(role_id)}


@router.delete("/roles/{role_id}/assign/{username}", status_code=204)
def unassign_role(
    role_id: uuid.UUID,
    username: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _require_can_manage_role(db, current_user, role_id)

    target_user = db.query(models.User).filter_by(username=username).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="user not found")

    assignment = db.query(models.UserRole).filter_by(user_id=target_user.id, role_id=role_id).first()
    if not assignment:
        raise HTTPException(status_code=404, detail="user does not have this role")

    db.delete(assignment)
    db.commit()


# ---------- "Who am I" convenience endpoint ----------

@router.get("/me")
def whoami(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    memberships = db.query(models.UserGroup).filter_by(user_id=current_user.id).all()
    role_assignments = db.query(models.UserRole).filter_by(user_id=current_user.id).all()
    roles = [db.query(models.Role).filter_by(id=ur.role_id).first() for ur in role_assignments]
    services = set()
    for role in roles:
        if not role or not role.is_active or not role.group or not role.group.is_active:
            continue  # same "active role in an active group" rule as authz.user_has_service_access
        for grant in role.access_grants:
            services.add(grant.service_name)

    return {
        "username": current_user.username,
        "is_superuser": current_user.is_superuser,
        "groups": [
            {"group_id": str(m.group_id), "is_group_admin": m.is_group_admin} for m in memberships
        ],
        "roles": [{"role_id": str(r.id), "name": r.name, "group_id": str(r.group_id)} for r in roles],
        "service_access": sorted(services) if not current_user.is_superuser else sorted(VALID_SERVICES),
    }
