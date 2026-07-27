#!/usr/bin/env python3
"""
Seeds realistic fake data into the running platform via its own API, so you
can exercise sorting, filtering, search, project linking, and exports all at
once. Uses only the Python standard library (urllib) — nothing to pip install.

Run directly on gpu-server (or anywhere that can reach the gateway):
    python3 seed_data.py

Reads BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD from the environment if
set (e.g. `ADMIN_PASSWORD=... python3 seed_data.py`), otherwise falls back
to the local-dev defaults below — don't rely on those defaults against a
shared/staging environment.
"""

import json
import os
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080/api")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")

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


def seed_company():
    print("Creating company profile...")
    company = call("POST", "/companies", {
        "name": "Trustwell International Co., Ltd.",
        "position": "Director",
        "address": "No. 45, Kabar Aye Pagoda Road, Yangon, Myanmar",
        "contact_no": "+95 9 123 456 789",
        "support_email": "support@trustwell.example.com",
        "support_phone": "+95 9 987 654 321",
        "is_primary": True,
    })
    if company:
        print(f"  created: {company['name']}")
    return company


def seed_customers():
    print("Creating customers...")
    customers_data = [
        {"name": "Jimmy Trading Co.", "email": "jimmy@jimmytrading.example.com", "phone": "+95 9 111 222 333",
         "billing_address": {"address": "Yangon, Myanmar"}},
        {"name": "Golden Sky Enterprise", "email": "info@goldensky.example.com", "phone": "+95 9 444 555 666",
         "billing_address": {"address": "Mandalay, Myanmar"}},
        {"name": "Silver Star Logistics", "email": "contact@silverstar.example.com", "phone": "+95 9 777 888 999",
         "billing_address": {"address": "Naypyidaw, Myanmar"}},
        {"name": "Ayeyarwady Tech Solutions", "email": "sales@ayeyarwadytech.example.com", "phone": "+95 9 222 333 444",
         "billing_address": {"address": "Bago, Myanmar"}},
    ]
    created = []
    for c in customers_data:
        result = call("POST", "/customers", c)
        if result:
            created.append(result)
            print(f"  created: {result['name']}")
    return created


def seed_products():
    print("Creating products...")
    products_data = [
        {"sku": "SRV-DELL-R740", "name": "Dell PowerEdge R740 Server", "category": "Server",
         "unit_price": 4500.00, "description": "2U rack server, dual Xeon, 128GB RAM"},
        {"sku": "SRV-HPE-DL380", "name": "HPE ProLiant DL380 Gen10", "category": "Server",
         "unit_price": 5200.00, "description": "2U rack server, redundant PSU"},
        {"sku": "LPT-DELL-5420", "name": "Dell Latitude 5420", "category": "Laptop",
         "unit_price": 950.00, "description": "14-inch business laptop, i7, 16GB RAM"},
        {"sku": "LPT-HP-840", "name": "HP EliteBook 840 G8", "category": "Laptop",
         "unit_price": 1100.00, "description": "14-inch business laptop, i7, 16GB RAM"},
        {"sku": "CCTV-HIK-4MP", "name": "Hikvision 4MP Dome Camera", "category": "CCTV",
         "unit_price": 85.00, "description": "4MP IR dome camera, IP66"},
        {"sku": "CCTV-DAHUA-8CH", "name": "Dahua 8-Channel NVR", "category": "CCTV",
         "unit_price": 320.00, "description": "8-channel NVR, 4K output"},
        {"sku": "NET-UBNT-ES24", "name": "Ubiquiti EdgeSwitch 24", "category": "Network Devices",
         "unit_price": 399.00, "description": "24-port managed PoE switch"},
        {"sku": "NET-CISCO-2960", "name": "Cisco Catalyst 2960-X", "category": "Network Devices",
         "unit_price": 650.00, "description": "24-port Gigabit switch"},
        {"sku": "CMP-DESK-I7", "name": "Business Desktop i7", "category": "Computer",
         "unit_price": 780.00, "description": "i7, 16GB RAM, 512GB SSD"},
        {"sku": "CMP-DESK-I5", "name": "Business Desktop i5", "category": "Computer",
         "unit_price": 580.00, "description": "i5, 8GB RAM, 256GB SSD"},
    ]
    created = []
    for p in products_data:
        result = call("POST", "/products", p)
        if result:
            created.append(result)
            print(f"  created: {result['sku']} — {result['name']}")
    return created


def seed_projects():
    print("Creating projects...")
    projects_data = [
        {"name": "Network Upgrade Phase 2", "budget_year": "2025-2026",
         "description": "Core switch and CCTV upgrade across branch offices"},
        {"name": "Data Center Refresh", "budget_year": "2025-2026",
         "description": "Server and storage refresh for main data center"},
        {"name": "Branch Office Rollout", "budget_year": "2024-2025",
         "description": "New branch office IT equipment rollout"},
    ]
    created = []
    for p in projects_data:
        result = call("POST", "/projects", p)
        if result:
            created.append(result)
            print(f"  created: {result['name']} ({result['budget_year']})")
    return created


def seed_documents(customers, products, projects):
    print("Creating documents...")
    if not customers or not products:
        print("  skipping — need at least one customer and one product")
        return []

    by_sku = {p["sku"]: p for p in products}

    docs_data = [
        {
            "doc_type": "quotation", "doc_number": "QUO-2026-1001",
            "customer_id": customers[0]["id"], "project_id": projects[0]["id"] if projects else None,
            "currency": "USD",
            "items": [
                {"product_id": by_sku["NET-UBNT-ES24"]["id"], "quantity": 4, "tax_rate": 5},
                {"product_id": by_sku["CCTV-HIK-4MP"]["id"], "quantity": 10, "tax_rate": 5},
                {"product_id": by_sku["CCTV-DAHUA-8CH"]["id"], "quantity": 2, "tax_rate": 5},
            ],
            "status": "sent",
        },
        {
            "doc_type": "invoice", "doc_number": "INV-2026-2001",
            "customer_id": customers[1]["id"], "project_id": projects[1]["id"] if len(projects) > 1 else None,
            "currency": "USD",
            "items": [
                {"product_id": by_sku["SRV-DELL-R740"]["id"], "quantity": 2, "tax_rate": 5},
                {"product_id": by_sku["SRV-HPE-DL380"]["id"], "quantity": 1, "tax_rate": 5},
            ],
            "status": "paid",
        },
        {
            "doc_type": "proposal", "doc_number": "PRO-2026-3001",
            "customer_id": customers[2]["id"], "project_id": projects[2]["id"] if len(projects) > 2 else None,
            "currency": "MMK",
            "items": [
                {"product_id": by_sku["LPT-DELL-5420"]["id"], "quantity": 15, "tax_rate": 0},
                {"product_id": by_sku["LPT-HP-840"]["id"], "quantity": 5, "tax_rate": 0},
            ],
            "status": "draft",
        },
        {
            "doc_type": "invoice", "doc_number": "INV-2026-2002",
            "customer_id": customers[0]["id"], "project_id": projects[0]["id"] if projects else None,
            "currency": "USD",
            "items": [
                {"product_id": by_sku["CMP-DESK-I7"]["id"], "quantity": 8, "tax_rate": 5},
                {"product_id": by_sku["CMP-DESK-I5"]["id"], "quantity": 12, "tax_rate": 5},
            ],
            "status": "sent",
        },
        {
            "doc_type": "quotation", "doc_number": "QUO-2026-1002",
            "customer_id": customers[3]["id"], "project_id": None,
            "currency": "USD",
            "items": [
                {"description": "On-site installation & configuration service", "quantity": 1,
                 "unit": "Job", "unit_price": 500.00, "tax_rate": 0},
            ],
            "status": "draft",
        },
        {
            "doc_type": "invoice", "doc_number": "INV-2026-2003",
            "customer_id": customers[1]["id"], "project_id": projects[1]["id"] if len(projects) > 1 else None,
            "currency": "USD",
            "items": [
                {"product_id": by_sku["NET-CISCO-2960"]["id"], "quantity": 6, "tax_rate": 5},
            ],
            "status": "void",
        },
        {
            "doc_type": "quotation", "doc_number": "QUO-2026-1003",
            "customer_id": customers[2]["id"], "project_id": projects[0]["id"] if projects else None,
            "currency": "MMK",
            "items": [
                {"product_id": by_sku["CCTV-HIK-4MP"]["id"], "quantity": 20, "tax_rate": 5},
            ],
            "status": "expired",
        },
    ]

    created = []
    for d in docs_data:
        status = d.pop("status", None)
        result = call("POST", "/documents", d)
        if result:
            created.append(result)
            print(f"  created: {result['doc_number']} ({result['doc_type']}, total {result['total']} {result['currency']})")
            if status and status != "draft":
                call("PATCH", f"/documents/{result['id']}/status?new_status={status}", None, expect=(200,))
    return created


def main():
    login()
    print()
    seed_company()
    print()
    customers = seed_customers()
    print()
    products = seed_products()
    print()
    projects = seed_projects()
    print()
    seed_documents(customers, products, projects)
    print()
    print("Done. Refresh the app in your browser to see everything.")


if __name__ == "__main__":
    main()
