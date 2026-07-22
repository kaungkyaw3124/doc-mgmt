#!/usr/bin/env python3
"""
Clears ALL existing documents and products, then seeds fresh test data
specifically laid out to test project-based visibility:

  - Uses your EXISTING projects (fetched live, not recreated)
  - Creates a fresh, clearly-named set of products
  - Creates documents so that each project's documents reference a
    DIFFERENT subset of products — so a project-restricted user should
    only ever see the products tied to projects they can access.

Does NOT touch customers, companies, categories, or projects — only
documents and products are wiped and reseeded, as requested.

Run directly on gpu-server:
    python3 reset_and_reseed.py
"""

import json
import urllib.request
import urllib.error

BASE_URL = "http://localhost:8080/api"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "changeme"

TOKEN = None


def call(method, path, body=None, expect=(200, 201, 204)):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            raw = resp.read()
            parsed = json.loads(raw) if raw else None
            if status not in expect:
                print(f"  ! unexpected status {status} for {method} {path}: {parsed}")
            return parsed
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"  ! FAILED {method} {path} -> {e.code}: {detail}")
        return None


def login():
    global TOKEN
    print(f"Logging in as {ADMIN_USERNAME}...")
    result = call("POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    if not result:
        raise SystemExit("Login failed — check ADMIN_USERNAME/ADMIN_PASSWORD at the top of this script.")
    TOKEN = result["access_token"]
    print("  logged in OK")


def clear_documents():
    print("Clearing existing documents...")
    docs = call("GET", "/documents") or []
    for d in docs:
        call("DELETE", f"/documents/{d['id']}", expect=(204,))
    print(f"  removed {len(docs)} document(s)")


def clear_products():
    print("Clearing existing products...")
    products = call("GET", "/products") or []
    for p in products:
        call("DELETE", f"/products/{p['id']}", expect=(204,))
    print(f"  removed {len(products)} product(s)")


def get_existing_projects():
    projects = call("GET", "/projects") or []
    print(f"Found {len(projects)} existing project(s): {[p['name'] for p in projects]}")
    return projects


def get_existing_customers():
    customers = call("GET", "/customers") or []
    if not customers:
        print("  ! no customers found — documents need at least one, run seed_data.py first if this is empty")
    return customers


def seed_products():
    print("Creating fresh test products...")
    products_data = [
        {"sku": "TEST-CAM-01", "name": "Test Dome Camera", "category": "CCTV", "unit_price": 90.00},
        {"sku": "TEST-NVR-01", "name": "Test NVR Recorder", "category": "CCTV", "unit_price": 340.00},
        {"sku": "TEST-SRV-01", "name": "Test Rack Server", "category": "Server", "unit_price": 4800.00},
        {"sku": "TEST-SRV-02", "name": "Test Storage Array", "category": "Server", "unit_price": 3200.00},
        {"sku": "TEST-SW-01", "name": "Test Core Switch", "category": "Network Devices", "unit_price": 610.00},
        {"sku": "TEST-SW-02", "name": "Test Access Point", "category": "Network Devices", "unit_price": 145.00},
        {"sku": "TEST-LAP-01", "name": "Test Business Laptop", "category": "Laptop", "unit_price": 980.00},
    ]
    created = {}
    for p in products_data:
        result = call("POST", "/products", p)
        if result:
            created[result["sku"]] = result
            print(f"  created: {result['sku']} — {result['name']}")
    return created


def seed_documents(projects, customers, products):
    print("Creating documents, deliberately split by project...")
    if not customers:
        print("  skipping — no customers available")
        return

    by_sku = products
    customer_id = customers[0]["id"]

    # Map: project name -> which products its documents will reference.
    # Adjust these names if your project list differs.
    project_product_plan = {
        "Branch Office Rollout": ["TEST-CAM-01", "TEST-NVR-01"],
        "AI-1": ["TEST-SRV-01", "TEST-SRV-02"],
        "Data Center Refresh": ["TEST-SW-01", "TEST-SW-02"],
        "Network Upgrade Phase 2": ["TEST-LAP-01", "TEST-CAM-01"],
    }

    doc_counter = 1
    for project in projects:
        plan_skus = project_product_plan.get(project["name"])
        if not plan_skus:
            continue
        items = []
        for sku in plan_skus:
            if sku in by_sku:
                items.append({"product_id": by_sku[sku]["id"], "quantity": 2, "tax_rate": 5})
        if not items:
            continue

        doc_number = f"TEST-{doc_counter:03d}"
        doc_counter += 1
        result = call("POST", "/documents", {
            "doc_type": "quotation",
            "doc_number": doc_number,
            "customer_id": customer_id,
            "project_id": project["id"],
            "currency": "USD",
            "items": items,
        })
        if result:
            print(f"  created: {result['doc_number']} for project '{project['name']}' -> {plan_skus}")


def main():
    login()
    print()
    clear_documents()
    print()
    clear_products()
    print()
    projects = get_existing_projects()
    customers = get_existing_customers()
    print()
    products = seed_products()
    print()
    seed_documents(projects, customers, products)
    print()
    print("Done. Refresh the app in your browser to see everything.")
    print("Test tip: restrict a user to just one project (e.g. 'AI-1') and confirm")
    print("the Products tab only shows that project's products (TEST-SRV-01/02).")


if __name__ == "__main__":
    main()
