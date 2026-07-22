import uuid

from sqlalchemy.orm import Session

from app import models


def is_group_admin_of(db: Session, user: models.User, group_id: uuid.UUID) -> bool:
    membership = (
        db.query(models.UserGroup)
        .filter_by(user_id=user.id, group_id=group_id, is_group_admin=True)
        .first()
    )
    return membership is not None


def is_member_of(db: Session, user: models.User, group_id: uuid.UUID) -> bool:
    membership = db.query(models.UserGroup).filter_by(user_id=user.id, group_id=group_id).first()
    return membership is not None


def can_manage_group(db: Session, user: models.User, group_id: uuid.UUID) -> bool:
    """Superuser can manage any group; a group admin can manage only their own group."""
    return user.is_superuser or is_group_admin_of(db, user, group_id)


def user_has_service_access(db: Session, user: models.User, service_name: str) -> bool:
    """Superuser always has access. Otherwise: true if any *active* role in
    an *active* group assigned to this user grants access to the given
    service. A disabled group behaves as if all its roles were disabled."""
    if user.is_superuser:
        return True

    grant = (
        db.query(models.RoleAccess)
        .join(models.UserRole, models.UserRole.role_id == models.RoleAccess.role_id)
        .join(models.Role, models.Role.id == models.RoleAccess.role_id)
        .join(models.Group, models.Group.id == models.Role.group_id)
        .filter(
            models.UserRole.user_id == user.id,
            models.RoleAccess.service_name == service_name,
            models.Role.is_active == True,  # noqa: E712
            models.Group.is_active == True,  # noqa: E712
        )
        .first()
    )
    return grant is not None


def get_user_access_level(db: Session, user: models.User, service_name: str) -> str:
    """
    Returns "edit" or "view" for the given service. Superuser always gets
    "edit". If ANY of the user's *active* roles in an *active* group grant
    "edit" for this service, they get "edit" overall (most-permissive-wins
    across multiple roles); otherwise "view" if they have any access at
    all, defaulting to "edit" if there's no grant to check (backward-compat
    — service access itself is already gated separately by
    user_has_service_access).
    """
    if user.is_superuser:
        return "edit"

    grants = (
        db.query(models.RoleAccess)
        .join(models.UserRole, models.UserRole.role_id == models.RoleAccess.role_id)
        .join(models.Role, models.Role.id == models.RoleAccess.role_id)
        .join(models.Group, models.Group.id == models.Role.group_id)
        .filter(
            models.UserRole.user_id == user.id,
            models.RoleAccess.service_name == service_name,
            models.Role.is_active == True,  # noqa: E712
            models.Group.is_active == True,  # noqa: E712
        )
        .all()
    )
    if not grants:
        return "edit"
    if any(g.access_level == "edit" for g in grants):
        return "edit"
    return "view"


def get_user_allowed_project_ids(db: Session, user: models.User):
    """
    Returns "ALL" (no restriction) or a list of project_id strings.
    Restrictions are opt-in: if the user has no UserProjectAccess rows at
    all, they see every project's documents (same as before this feature
    existed). Project access is granted directly to the user (see
    UserProjectAccess) — not via their roles, which now only carry service
    permissions.
    """
    if user.is_superuser:
        return "ALL"

    grants = db.query(models.UserProjectAccess).filter_by(user_id=user.id).all()
    if not grants:
        return "ALL"
    return [str(g.project_id) for g in grants]
