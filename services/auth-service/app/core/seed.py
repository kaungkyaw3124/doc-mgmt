"""
Idempotent startup seeding for the built-in User Control defaults: the
"Operation" group (where normal operational users belong — Documents,
Products, Customers, Projects, Companies) and its two roles, Editor and
Viewer. Reuses the existing Group/Role/RoleAccess model (see app/models.py,
app/core/authz.py) rather than a second permission system — Editor/Viewer
are just ordinary roles with RoleAccess grants like any admin could create
by hand, pre-provisioned so the app is usable out of the box.

Safe to call on every startup: each piece is a get-or-create, so re-running
it never creates duplicates and never resets an admin's own edits (e.g. if
an admin later changes what Viewer grants, this won't put it back).
"""
from sqlalchemy.orm import Session

from app import models

OPERATION_GROUP_NAME = "Operation"
EDITOR_ROLE_NAME = "Editor"
VIEWER_ROLE_NAME = "Viewer"

# The five operational resources (see docs/SECURITY_HARDENING_LOG.md's User
# Control entry). Nginx maps customers/companies/projects to the same
# "documents" service_name as documents itself (see infra/nginx/
# nginx.conf.template) — document-service's own routers enforce
# X-Access-Level per-request — so one RoleAccess grant on "documents"
# already covers all four; "products" is the fifth, separately gated.
_OPERATION_SERVICES = ("documents", "products")


def _get_or_create_group(db: Session, name: str) -> models.Group:
    group = db.query(models.Group).filter_by(name=name).first()
    if group:
        return group
    group = models.Group(name=name)
    db.add(group)
    db.flush()
    return group


def _get_or_create_role(db: Session, group: models.Group, name: str) -> models.Role:
    role = db.query(models.Role).filter_by(name=name, group_id=group.id).first()
    if role:
        return role
    role = models.Role(name=name, group_id=group.id)
    db.add(role)
    db.flush()
    return role


def _ensure_access(db: Session, role: models.Role, service_name: str, access_level: str) -> None:
    grant = (
        db.query(models.RoleAccess)
        .filter_by(role_id=role.id, service_name=service_name)
        .first()
    )
    if grant:
        return  # already provisioned (possibly hand-edited by an admin since) — leave it alone
    db.add(models.RoleAccess(role_id=role.id, service_name=service_name, access_level=access_level))


def ensure_default_groups_and_roles(db: Session) -> None:
    operation = _get_or_create_group(db, OPERATION_GROUP_NAME)

    editor = _get_or_create_role(db, operation, EDITOR_ROLE_NAME)
    for service in _OPERATION_SERVICES:
        _ensure_access(db, editor, service, "edit")

    viewer = _get_or_create_role(db, operation, VIEWER_ROLE_NAME)
    for service in _OPERATION_SERVICES:
        _ensure_access(db, viewer, service, "view")

    db.commit()


def migrate_operation_members_to_full_existing_data_access(db: Session) -> int:
    """
    Existing-data migration for the User Control feature.

    Inspection of the schema (see app/models.py, app/core/authz.py) found
    NO per-record group/owner scoping for any of the five operational
    resources — Documents/Projects are visibility-gated only by
    UserProjectAccess ("no rows for this user" == "ALL projects visible",
    an opt-in restriction, not a default lockdown — see
    get_user_allowed_project_ids); Products/Customers/Companies have no
    project or group scoping at all, only the service-level Editor/Viewer
    access_level (already enforced server-side — see the User Control
    entry in docs/SECURITY_HARDENING_LOG.md).

    That means a brand-new Operation member already sees every existing
    record for free, by construction, with zero rows to add — restrictions
    are opt-in and none exist for a user who's never had one. The only way
    an Operation member could fail to see existing data is a **leftover**
    UserProjectAccess restriction predating their membership (e.g. an
    admin scoped them down to specific projects under a different
    group/role before this feature existed, or before they joined
    Operation) — that row would still narrow them to an allow-list instead
    of "ALL", in direct conflict with this feature's requirement that
    Operation users see existing data unconditionally by role.

    This migration corrects exactly that conflict: for every current
    Operation group member, it removes any UserProjectAccess rows they
    have, restoring them to the unrestricted "ALL projects" default so
    every pre-existing (and future) Document/Project is visible per their
    Editor/Viewer access_level. It does NOT touch any other group's
    members, any RoleProjectAccess/GroupProjectAccess provisioning data
    (those are pools/bookkeeping for admin-driven grants, not read at
    authorization time), or any operational data itself (Documents,
    Products, Customers, Projects, Companies are untouched — this only
    ever deletes auth-service's own UserProjectAccess rows).

    Idempotent: the first run removes any leftover restrictions; every
    run after that finds nothing left to remove and is a no-op. Returns
    the number of rows removed, for logging/tests.

    Deliberately NOT rolled into ensure_default_groups_and_roles above:
    that function only ever ADDS default provisioning and never touches
    per-user state; this one actively removes rows tied to specific
    users, which deserves to stay a clearly separate, clearly named step.
    """
    operation = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    if not operation:
        return 0  # ensure_default_groups_and_roles hasn't run yet — nothing to migrate

    member_ids = [
        row.user_id
        for row in db.query(models.UserGroup.user_id).filter_by(group_id=operation.id).all()
    ]
    if not member_ids:
        return 0

    removed = (
        db.query(models.UserProjectAccess)
        .filter(models.UserProjectAccess.user_id.in_(member_ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return removed
