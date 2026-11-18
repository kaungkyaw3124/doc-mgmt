"""
Loop iteration 19: Nginx always overwrites X-Group-Ids / X-Is-Superuser with
what auth-service /verify decided. If a client's own copy got through, the
admin below would have demoted itself to a stranger group and seen nothing.
(The full two-department matrix is tests/test_group_isolation.py.)
"""
import uuid

SPOOF = {"X-Group-Ids": str(uuid.uuid4()), "X-Is-Superuser": "0"}


def test_spoofed_scope_headers_are_overwritten(admin, customer):
    for path in ("/customers", "/documents", "/projects", "/products"):
        assert admin.get(path, headers=SPOOF).status_code == 200
    ids = {c["id"] for c in admin.get("/customers", headers=SPOOF).json()}
    assert customer["id"] in ids
    assert admin.get(f"/customers/{customer['id']}", headers=SPOOF).status_code == 200
    assert admin.get("/search", params={"q": "x"}, headers=SPOOF).status_code == 200


def test_created_rows_carry_their_group(customer, group_id):
    assert customer["group_id"] == group_id
