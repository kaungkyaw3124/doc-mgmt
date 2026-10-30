"""
Tests for the existing-data migration required by the User Control
follow-up task: Operation group members must see all pre-existing
Documents/Projects/Products/Customers/Companies per their role, without a
second permission system.

Schema inspection (see migrate_operation_members_to_full_existing_data_access's
own docstring in app/core/seed.py) found no per-record group/owner scoping
for any of the five resources — only Documents/Projects have any
visibility restriction at all (UserProjectAccess: no rows for a user means
"ALL projects", an opt-in restriction), and Products/Customers/Companies
have none. So a brand-new Operation member already sees every existing
record for free; the only real migration need is clearing a *leftover*
UserProjectAccess restriction that predates a user joining Operation,
which is what these tests exercise directly against a real Postgres DB
(same pattern as test_seed.py/test_user_control.py).
"""
import uuid

from app.core.seed import (
    ensure_default_groups_and_roles,
    migrate_operation_members_to_full_existing_data_access,
    OPERATION_GROUP_NAME,
)
from app.core.authz import get_user_allowed_project_ids
from app import models


def _make_operation_member(db, username):
    ensure_default_groups_and_roles(db)
    operation = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    user = models.User(username=username, hashed_password="x", is_approved=True, is_active=True)
    db.add(user)
    db.flush()
    db.add(models.UserGroup(user_id=user.id, group_id=operation.id))
    db.commit()
    return user, operation


def test_migration_is_a_noop_before_the_operation_group_exists(db):
    removed = migrate_operation_members_to_full_existing_data_access(db)
    assert removed == 0


def test_migration_is_a_noop_when_operation_has_no_members(db):
    ensure_default_groups_and_roles(db)
    removed = migrate_operation_members_to_full_existing_data_access(db)
    assert removed == 0


def test_migration_clears_a_leftover_restriction_for_an_operation_member(db):
    user, _operation = _make_operation_member(db, "legacy_editor")

    # Simulate a restriction that predates this user joining Operation —
    # e.g. set under a different group/role before the User Control
    # feature existed.
    stray_project_id = uuid.uuid4()
    db.add(models.UserProjectAccess(user_id=user.id, project_id=stray_project_id))
    db.commit()

    assert get_user_allowed_project_ids(db, user) == [str(stray_project_id)]

    removed = migrate_operation_members_to_full_existing_data_access(db)
    assert removed == 1

    db.refresh(user)
    assert get_user_allowed_project_ids(db, user) == "ALL"


def test_migration_leaves_non_operation_members_restrictions_untouched(db):
    sales = models.Group(name="Sales")
    db.add(sales)
    db.flush()
    outsider = models.User(username="sales_person", hashed_password="x", is_approved=True, is_active=True)
    db.add(outsider)
    db.flush()
    db.add(models.UserGroup(user_id=outsider.id, group_id=sales.id))
    stray_project_id = uuid.uuid4()
    db.add(models.UserProjectAccess(user_id=outsider.id, project_id=stray_project_id))
    db.commit()

    removed = migrate_operation_members_to_full_existing_data_access(db)
    assert removed == 0

    db.refresh(outsider)
    assert get_user_allowed_project_ids(db, outsider) == [str(stray_project_id)]


def test_migration_is_idempotent(db):
    user, _operation = _make_operation_member(db, "legacy_viewer")
    db.add(models.UserProjectAccess(user_id=user.id, project_id=uuid.uuid4()))
    db.commit()

    first_run = migrate_operation_members_to_full_existing_data_access(db)
    assert first_run == 1

    second_run = migrate_operation_members_to_full_existing_data_access(db)
    assert second_run == 0

    third_run = migrate_operation_members_to_full_existing_data_access(db)
    assert third_run == 0


def test_migration_never_touches_operational_data_tables(db):
    """
    Structural guard: this migration must only ever delete
    UserProjectAccess rows in auth-service's own database — it has no
    business touching Documents/Products/Customers/Projects/Companies at
    all (those live in different services' databases entirely, and
    auth-service has no models for them), so there is nothing for this
    migration to accidentally modify or delete there by construction.
    """
    import inspect
    from app.core import seed

    source = inspect.getsource(seed.migrate_operation_members_to_full_existing_data_access)
    for forbidden in ("models.Document", "models.Product", "models.Customer", "models.Company"):
        assert forbidden not in source
