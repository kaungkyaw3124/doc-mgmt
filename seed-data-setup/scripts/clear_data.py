#!/usr/bin/env python3
"""
Permanently deletes ALL documents, customers, products, projects, and
companies via the running platform's own API — the counterpart to
seed_data.py, for wiping test/demo data back to a clean slate.

Does NOT touch user accounts, groups, or roles.

This is irreversible: these are hard deletes, not recycle-bin trashes.
Back up anything you care about before running this.

Run directly on gpu-server (or anywhere that can reach the gateway):
    python3 clear_data.py

Reads BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD from the environment if
set (e.g. `ADMIN_PASSWORD=... python3 clear_data.py`), otherwise falls
back to the local-dev defaults below — same convention as seed_data.py.
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


def delete_all(list_path, delete_path_fmt, label, key="id", name_key="name"):
    """Fetches every row at list_path and permanently deletes each one.
    Deleting documents first matters — customers/products/projects/companies
    can't be deleted while a document still references them."""
    print(f"Deleting all {label}...")
    items = call("GET", list_path) or []
    if not items:
        print(f"  none found")
        return
    for item in items:
        label_text = item.get(name_key) or item.get("doc_number") or item[key]
        result = call("DELETE", delete_path_fmt.format(item[key]), expect=(204, 400))
        print(f"  deleted: {label_text}")


def main():
    login()
    print()
    delete_all("/documents", "/documents/{}", "documents", name_key="doc_number")
    print()
    delete_all("/customers", "/customers/{}", "customers")
    print()
    delete_all("/products", "/products/{}", "products", name_key="name")
    print()
    delete_all("/projects", "/projects/{}", "projects")
    print()
    delete_all("/companies", "/companies/{}", "companies")
    print()
    print("Done. All business data cleared — accounts, groups, and roles are untouched.")


if __name__ == "__main__":
    main()
