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
