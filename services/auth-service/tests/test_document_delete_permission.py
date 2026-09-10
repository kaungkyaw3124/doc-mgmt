"""
Tests for the "Delete permission" feature: a separate, orthogonal
permission from documents' own view/edit access_level — see
docs/SECURITY_HARDENING_LOG.md's "Delete permission" entry. Granting a
role "documents-delete" (via the same RoleAccess/service_name mechanism
already used for "audit-log"/"categories") must not change its
X-Access-Level for "documents", and vice versa. Real Postgres, real
login + /verify flow — same pattern as test_user_control.py.
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


def test_verify_reports_document_delete_permission_correctly(client, make_user, db):
    group, editor, viewer = _operation_group_and_roles(db)

    editor_user = make_user(username="editor_nodelete", password="correct horse battery staple")
    db.add(models.UserGroup(user_id=editor_user.id, group_id=group.id))
    db.add(models.UserRole(user_id=editor_user.id, role_id=editor.id))
    db.commit()

    token = _login(client, "editor_nodelete", "correct horse battery staple").json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200
    assert resp.headers["X-Access-Level"] == "edit"
    assert resp.headers["X-Has-Document-Delete"] == "false", "Editor role has no documents-delete grant yet — must default to false, not true"


def test_verify_reports_true_once_delete_is_explicitly_granted(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)

    editor_user = make_user(username="editor_withdelete", password="correct horse battery staple")
    db.add(models.UserGroup(user_id=editor_user.id, group_id=group.id))
    db.add(models.UserRole(user_id=editor_user.id, role_id=editor.id))
    db.commit()

    # Grant documents-delete on the SAME role — must not touch the
    # existing "documents" access_level grant at all.
    db.add(models.RoleAccess(role_id=editor.id, service_name="documents-delete", access_level="edit"))
    db.commit()

    token = _login(client, "editor_withdelete", "correct horse battery staple").json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200
    assert resp.headers["X-Access-Level"] == "edit", "granting documents-delete must not change the separate documents access_level"
    assert resp.headers["X-Has-Document-Delete"] == "true"


def test_viewer_without_delete_grant_gets_false(client, make_user, db):
    group, _editor, viewer = _operation_group_and_roles(db)

    viewer_user = make_user(username="viewer_nodelete", password="correct horse battery staple")
    db.add(models.UserGroup(user_id=viewer_user.id, group_id=group.id))
    db.add(models.UserRole(user_id=viewer_user.id, role_id=viewer.id))
    db.commit()

    token = _login(client, "viewer_nodelete", "correct horse battery staple").json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200
    assert resp.headers["X-Access-Level"] == "view"
    assert resp.headers["X-Has-Document-Delete"] == "false"


def test_viewer_can_be_granted_delete_independently_of_edit(client, make_user, db):
    """
    The permission model must allow Viewer + Delete (view-only otherwise,
    but can delete) as a valid combination, per the task's explicit
    Editor/Viewer x Delete matrix — granting documents-delete to the
    Viewer role must not upgrade its documents access_level to edit.
    """
    group, _editor, viewer = _operation_group_and_roles(db)
    db.add(models.RoleAccess(role_id=viewer.id, service_name="documents-delete", access_level="edit"))
    db.commit()

    viewer_user = make_user(username="viewer_withdelete", password="correct horse battery staple")
    db.add(models.UserGroup(user_id=viewer_user.id, group_id=group.id))
    db.add(models.UserRole(user_id=viewer_user.id, role_id=viewer.id))
    db.commit()

    token = _login(client, "viewer_withdelete", "correct horse battery staple").json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200
    assert resp.headers["X-Access-Level"] == "view", "granting Delete must not silently upgrade Viewer to edit access"
    assert resp.headers["X-Has-Document-Delete"] == "true"


def test_superuser_gets_document_delete_true_unconditionally(client, make_user, db):
    make_user(username="root_delete_test", password="correct horse battery staple", is_superuser=True)
    token = _login(client, "root_delete_test", "correct horse battery staple").json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200
    assert resp.headers["X-Has-Document-Delete"] == "true"


def test_admin_can_grant_documents_delete_via_role_access_api(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    make_user(username="root_api_test", password="correct horse battery staple", is_superuser=True)
    admin_token = _login(client, "root_api_test", "correct horse battery staple").json()["access_token"]

    resp = client.post(
        f"/admin/roles/{editor.id}/access",
        json={"service_name": "documents-delete"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/admin/roles/{editor.id}", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert "documents-delete" in detail["services"]
    assert "documents" in detail["services"], "granting delete must not remove the role's existing documents access"


def test_admin_can_revoke_documents_delete_via_role_access_api(client, make_user, db):
    group, editor, _viewer = _operation_group_and_roles(db)
    db.add(models.RoleAccess(role_id=editor.id, service_name="documents-delete", access_level="edit"))
    db.commit()

    make_user(username="root_revoke_test", password="correct horse battery staple", is_superuser=True)
    admin_token = _login(client, "root_revoke_test", "correct horse battery staple").json()["access_token"]

    resp = client.delete(
        f"/admin/roles/{editor.id}/access/documents-delete",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 204

    detail = client.get(f"/admin/roles/{editor.id}", headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert "documents-delete" not in detail["services"]
    assert "documents" in detail["services"], "revoking delete must not remove the role's existing documents access"
