"""
Tests for Tasks 3-6 of the User Control feature: admin-created users with
a role assigned at creation time, the "All users" listing exposing
group/role/status, no-deletion (only deactivation), and the pending
self-registration workflow continuing to work alongside admin-created
users. Real Postgres, real login flow — same pattern as
test_refresh_tokens.py.
"""
from app.core.seed import ensure_default_groups_and_roles, OPERATION_GROUP_NAME, EDITOR_ROLE_NAME, VIEWER_ROLE_NAME
from app import models


def _login(client, username, password):
    return client.post("/login", json={"username": username, "password": password})


def _operation_group_and_roles(db):
    ensure_default_groups_and_roles(db)
    group = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    editor = db.query(models.Role).filter_by(name=EDITOR_ROLE_NAME, group_id=group.id).first()
    viewer = db.query(models.Role).filter_by(name=VIEWER_ROLE_NAME, group_id=group.id).first()
    return group, editor, viewer


def _admin_token(client, make_user):
    make_user(username="root", password="correct horse battery staple", is_superuser=True)
    return _login(client, "root", "correct horse battery staple").json()["access_token"]


# ---------- Task 3: admin create user with role ----------

def test_admin_can_create_an_editor(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)

    resp = client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role_name"] == "Editor"

    created = db.query(models.User).filter_by(username="eddie").first()
    assert created.is_approved is True
    assert created.is_active is True
    assert created.is_superuser is False


def test_admin_can_create_a_viewer(client, make_user, db):
    group, _editor, viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)

    resp = client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "vera", "password": "correct horse battery staple", "role_id": str(viewer.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role_name"] == "Viewer"


def test_created_editor_can_log_in_with_edit_access_level(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    login_resp = _login(client, "eddie", "correct horse battery staple")
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    verify_resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert verify_resp.status_code == 200
    assert verify_resp.headers["X-Access-Level"] == "edit"


def test_created_viewer_can_log_in_with_view_access_level(client, make_user, db):
    group, _editor, viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "vera", "password": "correct horse battery staple", "role_id": str(viewer.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    login_resp = _login(client, "vera", "correct horse battery staple")
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    verify_resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert verify_resp.status_code == 200
    assert verify_resp.headers["X-Access-Level"] == "view"


def test_role_id_from_a_different_group_is_rejected(client, make_user, db):
    """A role_id must belong to the SAME group being posted to — prevents
    accidentally (or maliciously) assigning a role from an unrelated group."""
    group, editor, _viewer = _operation_group_and_roles(db)
    other_group = models.Group(name="Sales")
    db.add(other_group)
    db.commit()

    admin_token = _admin_token(client, make_user)
    resp = client.post(
        f"/admin/groups/{other_group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400


def test_create_user_endpoint_has_no_superuser_field():
    """Structural guard: CreateUserInGroupRequest must never grow an
    is_superuser field — promoting to superuser must stay unreachable
    through this admin-facing endpoint."""
    from app.routers.admin import CreateUserInGroupRequest
    assert "is_superuser" not in CreateUserInGroupRequest.model_fields


# ---------- Task 4: user listing shows group/role/status ----------

def test_all_users_listing_includes_group_and_role(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    eddie = next(u for u in resp.json() if u["username"] == "eddie")
    assert eddie["groups"] == [OPERATION_GROUP_NAME]
    assert eddie["roles"] == [EDITOR_ROLE_NAME]
    assert eddie["is_active"] is True
    assert eddie["is_approved"] is True


# ---------- Task 5: no user deletion ----------

def test_no_delete_user_endpoint_exists(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    create_resp = client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_id = db.query(models.User).filter_by(username="eddie").first().id

    resp = client.delete(f"/admin/users/{user_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code in (404, 405)  # route doesn't exist — FastAPI returns 405 for a matched path/wrong method, 404 if unmatched entirely

    # And the account is provably still there, untouched.
    still_there = db.query(models.User).filter_by(username="eddie").first()
    assert still_there is not None


def test_deactivated_user_cannot_log_in(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    eddie = db.query(models.User).filter_by(username="eddie").first()

    deactivate_resp = client.patch(
        f"/admin/users/{eddie.id}/active",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deactivate_resp.status_code == 200

    login_resp = _login(client, "eddie", "correct horse battery staple")
    assert login_resp.status_code == 403


def test_deactivated_user_refresh_token_is_revoked(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login_resp = _login(client, "eddie", "correct horse battery staple")
    assert login_resp.status_code == 200
    # The refresh cookie is now set on `client` (TestClient persists cookies).

    eddie = db.query(models.User).filter_by(username="eddie").first()
    client.patch(
        f"/admin/users/{eddie.id}/active",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    refresh_resp = client.post("/refresh")
    assert refresh_resp.status_code == 401


def test_deactivated_user_blocked_from_protected_apis(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    admin_token = _admin_token(client, make_user)
    client.post(
        f"/admin/groups/{group.id}/users",
        json={"username": "eddie", "password": "correct horse battery staple", "role_id": str(editor.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login_resp = _login(client, "eddie", "correct horse battery staple")
    token = login_resp.json()["access_token"]

    eddie = db.query(models.User).filter_by(username="eddie").first()
    client.patch(
        f"/admin/users/{eddie.id}/active",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # The already-issued access token is still cryptographically valid
    # (JWTs aren't individually revocable), but /verify re-checks is_active
    # against the DB on every call — this is what actually blocks the very
    # next protected request, not token expiry.
    verify_resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert verify_resp.status_code == 403


# ---------- Task 6: pending self-registration still works ----------

def test_self_registered_user_stays_pending_until_approved(client, db):
    resp = client.post("/register", json={"username": "newperson", "password": "correct horse battery staple"})
    assert resp.status_code == 201

    login_resp = _login(client, "newperson", "correct horse battery staple")
    assert login_resp.status_code == 403
    assert "pending" in login_resp.json()["detail"].lower()


def test_self_registered_user_can_log_in_after_approval_and_role_assignment(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    client.post(
        "/register",
        json={"username": "newperson", "password": "correct horse battery staple", "requested_group_id": str(group.id)},
    )
    newperson = db.query(models.User).filter_by(username="newperson").first()

    admin_token = _admin_token(client, make_user)
    approve_resp = client.post(
        f"/admin/users/{newperson.id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["added_to_group"] is True

    # Approval alone only adds group membership — a role still needs
    # assigning (same two-role model as an admin-created user).
    assign_resp = client.post(
        f"/admin/roles/{editor.id}/assign",
        json={"username": "newperson"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert assign_resp.status_code == 201

    login_resp = _login(client, "newperson", "correct horse battery staple")
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    verify_resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert verify_resp.status_code == 200
    assert verify_resp.headers["X-Access-Level"] == "edit"
