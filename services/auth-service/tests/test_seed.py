"""
Tests for Task 1 (Operation group) and Task 2 (exactly two roles: Editor,
Viewer) of the User Control feature. Real Postgres, same pattern as the
other auth-service tests (see conftest.py) — this exercises actual DB
state, not mocks.
"""
import uuid

from app.core.seed import ensure_default_groups_and_roles, OPERATION_GROUP_NAME, EDITOR_ROLE_NAME, VIEWER_ROLE_NAME
from app import models


def test_operation_group_is_created(db):
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    assert group is not None
    assert group.is_active is True


def test_exactly_two_roles_in_operation_group(db):
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    roles = db.query(models.Role).filter_by(group_id=group.id).all()
    names = sorted(r.name for r in roles)
    assert names == [EDITOR_ROLE_NAME, VIEWER_ROLE_NAME]


def test_editor_grants_edit_on_documents_and_products(db):
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    editor = db.query(models.Role).filter_by(name=EDITOR_ROLE_NAME, group_id=group.id).first()
    levels = {g.service_name: g.access_level for g in editor.access_grants}
    assert levels == {"documents": "edit", "products": "edit"}


def test_viewer_grants_view_on_documents_and_products(db):
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    viewer = db.query(models.Role).filter_by(name=VIEWER_ROLE_NAME, group_id=group.id).first()
    levels = {g.service_name: g.access_level for g in viewer.access_grants}
    assert levels == {"documents": "view", "products": "view"}


def test_seeding_is_idempotent(db):
    ensure_default_groups_and_roles(db)
    ensure_default_groups_and_roles(db)
    ensure_default_groups_and_roles(db)

    groups = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).all()
    assert len(groups) == 1

    roles = db.query(models.Role).filter_by(group_id=groups[0].id).all()
    assert len(roles) == 2

    for role in roles:
        assert len(role.access_grants) == 2  # documents + products, not duplicated


def test_seeding_does_not_reset_an_admin_edit(db):
    """If an admin later changes what a grant means (e.g. narrows Viewer's
    products access), re-running the seed on the next startup must not
    silently put the original default back."""
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    viewer = db.query(models.Role).filter_by(name=VIEWER_ROLE_NAME, group_id=group.id).first()
    products_grant = db.query(models.RoleAccess).filter_by(role_id=viewer.id, service_name="products").first()
    products_grant.access_level = "edit"  # admin manually promoted Viewer's products access
    db.commit()

    ensure_default_groups_and_roles(db)

    products_grant = db.query(models.RoleAccess).filter_by(role_id=viewer.id, service_name="products").first()
    assert products_grant.access_level == "edit"  # not reset back to "view"


def test_superuser_is_not_auto_added_to_operation(db, make_user):
    ensure_default_groups_and_roles(db)
    superuser = make_user(username="root", is_superuser=True)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    membership = db.query(models.UserGroup).filter_by(user_id=superuser.id, group_id=group.id).first()
    assert membership is None


def test_normal_user_can_belong_to_operation(db, make_user):
    ensure_default_groups_and_roles(db)
    user = make_user(username="opuser")
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    db.add(models.UserGroup(user_id=user.id, group_id=group.id, is_group_admin=False))
    db.commit()

    membership = db.query(models.UserGroup).filter_by(user_id=user.id, group_id=group.id).first()
    assert membership is not None
