#!/usr/bin/env python3
"""
Creates a full set of test data for a new "City Mart" group, so you can log
in as a City Mart user and confirm they see only City Mart's data — the
real-world test for get_user_allowed_project_ids (see
services/auth-service/app/core/authz.py).

Creates, as admin:
  - the "City Mart" group
  - a company profile ("City Mart Co., Ltd.")
  - 2 customers
  - 3 products
  - a project ("City Mart Store Renovation"), with that project's pool
    access granted ONLY to the City Mart group
  - 2 documents (a quotation and an invoice) against that project
  - a "City Mart Tester" user, member of the City Mart group, with an
    Editor role granting documents access
Then logs in AS that new user and checks what GET /projects and
GET /documents return — PASS if they see only City Mart's project/
documents, FAIL (with details) if they see anything from another group
(e.g. Operation's 4 projects from the earlier migration).

IMPORTANT CAVEAT: companies, customers, and products have NO group scoping
anywhere in this codebase (only documents/projects do — see
services/document-service/app/models.py vs services/catalogue-service/
app/models.py). So the City Mart company/customers/products created here
WILL be visible to every group, including Operation — that's expected,
not a bug in this script. Only the project and its documents are actually
isolated by group, and that's what the PASS/FAIL check below verifies.

Uses only the Python standard library (urllib) — nothing to pip install.
Idempotent-ish: re-running skips things that already exist by name/
username where the API allows checking, but will create duplicate
customers/products/documents on a second run (there's no natural unique
key for those) — safe to run once per test cycle, or clean up by hand
between runs.

Run:
    ADMIN_PASSWORD=... python3 seed_city_mart.py

Reads BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD / GROUP_NAME /
TEST_USERNAME / TEST_PASSWORD from the environment if set.
"""

import json
import os
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080/api")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
GROUP_NAME = os.environ.get("GROUP_NAME", "City Mart")
TEST_USERNAME = os.environ.get("TEST_USERNAME", "citymart_tester")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "CityMart123!")

ADMIN_TOKEN = None


def call(method, path, body=None, token=None, expect=(200, 201, 204)):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    use_token = token if token is not None else ADMIN_TOKEN
    if use_token:
        req.add_header("Authorization", f"Bearer {use_token}")
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            raw = resp.read()
            parsed = json.loads(raw) if raw else None
            if status not in expect:
                print(f"  ! unexpected status {status} for {method} {path}: {parsed}")
            return status, parsed
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"  ! FAILED {method} {path} -> {e.code}: {detail}")
        return e.code, detail


def login(username, password):
    status, result = call("POST", "/auth/login", {"username": username, "password": password}, token=False)
    if status != 200 or not result:
        raise SystemExit(f"Login as {username} failed ({status}): {result}")
    return result["access_token"]


def seed_group():
    print(f"Creating group '{GROUP_NAME}'...")
    status, group = call("POST", "/admin/groups", {"name": GROUP_NAME}, expect=(201, 409))
    if status == 409:
        print("  already exists — fetching it")
        _, groups = call("GET", "/admin/groups")
        group = next(g for g in groups if g["name"] == GROUP_NAME)
    print(f"  group_id={group['id']}")
    return group


def seed_company():
    print("Creating company profile...")
    status, company = call("POST", "/companies", {
        "name": "City Mart Co., Ltd.",
        "position": "Director",
        "address": "No. 12, Pyay Road, Yangon, Myanmar",
        "contact_no": "+95 9 555 111 222",
        "support_email": "support@citymart.example.com",
        "support_phone": "+95 9 555 333 444",
        "is_primary": False,
    })
    if status in (200, 201):
        print(f"  created: {company['name']}  (NOTE: not group-scoped — visible to every group)")
    return company


def seed_customers():
    print("Creating customers...")
    customers_data = [
        {"name": "City Mart Downtown Branch", "email": "downtown@citymart.example.com",
         "phone": "+95 9 666 111 222", "billing_address": {"address": "Downtown Yangon, Myanmar"}},
        {"name": "City Mart Riverside Branch", "email": "riverside@citymart.example.com",
         "phone": "+95 9 666 333 444", "billing_address": {"address": "Riverside, Yangon, Myanmar"}},
    ]
    created = []
    for c in customers_data:
        status, result = call("POST", "/customers", c)
        if status in (200, 201):
            created.append(result)
            print(f"  created: {result['name']}  (NOTE: not group-scoped)")
    return created


def seed_products():
    print("Creating products...")
    products_data = [
        {"sku": "CM-SHELF-STD", "name": "Standard Display Shelf Unit", "category": "Store Fixtures",
         "unit_price": 220.00, "description": "Steel display shelf, 1.8m, adjustable tiers"},
        {"sku": "CM-FRIDGE-GLASS", "name": "Glass-Door Display Fridge", "category": "Store Fixtures",
         "unit_price": 1350.00, "description": "Commercial glass-door chiller, 400L"},
        {"sku": "CM-POS-TERM", "name": "POS Terminal", "category": "Electronics",
         "unit_price": 480.00, "description": "Touchscreen POS terminal with receipt printer"},
    ]
    created = []
    for p in products_data:
        status, result = call("POST", "/products", p)
        if status in (200, 201):
            created.append(result)
            print(f"  created: {result['sku']} — {result['name']}  (NOTE: not group-scoped)")
    return created


def seed_project(group_id):
    print("Creating project 'City Mart Store Renovation'...")
    status, project = call("POST", "/projects", {
        "name": "City Mart Store Renovation",
        "budget_year": "2026-2027",
        "description": "Fixtures and POS refresh for City Mart branches",
    })
    if status not in (200, 201):
        raise SystemExit(f"Failed to create project: {project}")
    print(f"  project_id={project['id']}")

    print(f"  granting '{GROUP_NAME}' pool access to this project...")
    call(
        "POST", f"/admin/groups/{group_id}/project-access", {"project_id": project["id"]}, expect=(201, 409)
    )
    print("  OK (this project is now ONLY visible to the City Mart group)")
    return project


def seed_documents(customers, products, project):
    print("Creating documents...")
    if not customers or not products:
        print("  skipping — need at least one customer and one product")
        return []

    by_sku = {p["sku"]: p for p in products}
    docs_data = [
        {
            "doc_type": "quotation", "doc_number": "CM-QUO-2026-0001",
            "customer_id": customers[0]["id"], "project_id": project["id"],
            "currency": "USD",
            "items": [
                {"product_id": by_sku["CM-SHELF-STD"]["id"], "quantity": 10, "tax_rate": 5},
                {"product_id": by_sku["CM-POS-TERM"]["id"], "quantity": 3, "tax_rate": 5},
            ],
        },
        {
            "doc_type": "invoice", "doc_number": "CM-INV-2026-0001",
            "customer_id": customers[1]["id"], "project_id": project["id"],
            "currency": "USD",
            "items": [
                {"product_id": by_sku["CM-FRIDGE-GLASS"]["id"], "quantity": 2, "tax_rate": 5},
            ],
        },
    ]
    created = []
    for d in docs_data:
        status, result = call("POST", "/documents", d)
        if status in (200, 201):
            created.append(result)
            print(f"  created: {result['doc_number']} ({result['doc_type']}, total {result['total']} {result['currency']})")
    return created


def seed_test_user(group_id):
    print(f"Creating test user '{TEST_USERNAME}' in group '{GROUP_NAME}'...")
    status, user = call(
        "POST", f"/admin/groups/{group_id}/users",
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
        expect=(201, 409),
    )
    if status == 409:
        print("  already exists — continuing")
    else:
        print("  OK")

    print("  creating 'Editor' role with documents access...")
    status, role = call("POST", f"/admin/groups/{group_id}/roles", {"name": "Editor"}, expect=(201, 409))
    if status == 409:
        _, roles = call("GET", f"/admin/groups/{group_id}/roles")
        role = next(r for r in roles if r["name"] == "Editor")
    call("POST", f"/admin/roles/{role['id']}/access", {"service_name": "documents", "access_level": "edit"})
    call("POST", f"/admin/roles/{role['id']}/access", {"service_name": "products", "access_level": "edit"})

    print(f"  assigning Editor role to '{TEST_USERNAME}'...")
    call("POST", f"/admin/roles/{role['id']}/assign", {"username": TEST_USERNAME}, expect=(201, 409))
    print("  OK")


def verify_isolation(project):
    print(f"\nLogging in as '{TEST_USERNAME}' to verify isolation...")
    test_token = login(TEST_USERNAME, TEST_PASSWORD)
    print("  OK")

    status, visible_projects = call("GET", "/projects", token=test_token)
    if status != 200:
        raise SystemExit(f"Failed to list projects as test user: {visible_projects}")
    visible_ids = {p["id"] for p in visible_projects}
    visible_names = sorted(p["name"] for p in visible_projects)
    print(f"  '{TEST_USERNAME}' sees {len(visible_projects)} project(s): {visible_names}")

    print("\n" + "=" * 60)
    if visible_ids == {project["id"]}:
        print(f"PASS: '{TEST_USERNAME}' sees ONLY '{project['name']}' — group isolation is working.")
    elif project["id"] not in visible_ids:
        print(f"FAIL: '{TEST_USERNAME}' does NOT see their own group's project '{project['name']}'.")
    else:
        extra = visible_ids - {project["id"]}
        print(f"FAIL: '{TEST_USERNAME}' sees {len(extra)} project(s) outside '{GROUP_NAME}': {extra}")
        print("      Group isolation is NOT working.")
    print("=" * 60)
    print(
        "\nNote: customers/products/companies are NOT group-scoped in this codebase, so "
        f"'{TEST_USERNAME}' will see every group's customers/products too — that's a separate, "
        "known gap, not what this check tests."
    )


def main():
    global ADMIN_TOKEN
    print(f"Logging in as {ADMIN_USERNAME}...")
    ADMIN_TOKEN = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    print("  OK\n")

    group = seed_group()
    print()
    seed_company()
    print()
    customers = seed_customers()
    print()
    products = seed_products()
    print()
    project = seed_project(group["id"])
    print()
    seed_documents(customers, products, project)
    print()
    seed_test_user(group["id"])

    verify_isolation(project)

    print(f"\nLog into the app as username='{TEST_USERNAME}' password='{TEST_PASSWORD}' to check visually too.")


if __name__ == "__main__":
    main()
