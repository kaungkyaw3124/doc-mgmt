"""
Loop iteration 19 — department separation: /verify tells the services which
groups' data the caller may see (X-Group-Ids: active groups only; every
active group for a superuser) and whether they are a superuser.
"""
from app import models
from app.core.seed import ensure_default_groups_and_roles, OPERATION_GROUP_NAME, EDITOR_ROLE_NAME

PASSWORD = "correct horse battery staple"


def _verify(client, username):
    token = client.post("/login", json={"username": username, "password": PASSWORD}).json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}", "X-Service": "documents"})
    assert resp.status_code == 200, resp.text
    return resp.headers


def test_verify_reports_active_groups_and_superuser_flag(client, make_user, db):
    ensure_default_groups_and_roles(db)
    ops = db.query(models.Group).filter_by(name=OPERATION_GROUP_NAME).first()
    editor = db.query(models.Role).filter_by(name=EDITOR_ROLE_NAME, group_id=ops.id).first()
    city = models.Group(name="CityMart")
    off = models.Group(name="Closed Dept", is_active=False)
    db.add_all([city, off])
    db.commit()

    one = make_user(username="ops_only", password=PASSWORD)
    both = make_user(username="ops_and_city", password=PASSWORD)
    none = make_user(username="no_group", password=PASSWORD)
    boss = make_user(username="boss", password=PASSWORD, is_superuser=True)
    for user, groups in ((one, [ops]), (both, [ops, city, off])):
        for g in groups:
            db.add(models.UserGroup(user_id=user.id, group_id=g.id))
        db.add(models.UserRole(user_id=user.id, role_id=editor.id))
    db.commit()

    assert _verify(client, "ops_only")["X-Group-Ids"] == str(ops.id)
    assert _verify(client, "ops_only")["X-Is-Superuser"] == "0"
    # the disabled group is left out
    assert set(_verify(client, "ops_and_city")["X-Group-Ids"].split(",")) == {str(ops.id), str(city.id)}
    # a superuser gets every ACTIVE group (to pick one as a record's owner), and the flag
    h = _verify(client, "boss")
    assert set(h["X-Group-Ids"].split(",")) == {str(g.id) for g in db.query(models.Group).filter_by(is_active=True)}
    assert h["X-Is-Superuser"] == "1"
    # in no group (and no documents role) -> /verify refuses the service itself;
    # for a service-less check the header is present but empty
    token = client.post("/login", json={"username": "no_group", "password": PASSWORD}).json()["access_token"]
    resp = client.get("/verify", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.headers["X-Group-Ids"] == "" and resp.headers["X-Is-Superuser"] == "0"
