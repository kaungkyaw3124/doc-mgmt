"""Iteration 28: a superuser's X-Group-Ids lists every active group. With
~100+ groups it outgrew nginx's 4k proxy buffer and every API call 500'd
("upstream sent too big header"). 150 groups → still 200 everywhere."""
import uuid

from conftest import destructive


@destructive  # creates 150 groups on the stack it runs against
def test_superuser_with_150_groups_gets_200(admin):
    tag = uuid.uuid4().hex[:6]
    ids = []
    try:
        for i in range(150):
            r = admin.post("/admin/groups", json={"name": f"many-{tag}-{i:03d}"})
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        for path in ("/documents", "/customers", "/projects", "/products", "/companies", "/categories"):
            r = admin.get(path)
            assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
        assert admin.get("/search", params={"q": "x"}).status_code == 200
    finally:
        for gid in ids:
            admin.delete(f"/admin/groups/{gid}")
