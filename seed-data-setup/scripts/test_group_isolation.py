#!/usr/bin/env python3
"""
End-to-end test for group-scoped project visibility (see
services/auth-service/app/core/authz.py:get_user_allowed_project_ids).

Creates a brand-new group ("City Mart" by default) with its own project and
its own user, then logs in as that user and checks they see ONLY their
group's project(s) — not Operation's or any other group's. Uses only the
Python standard library (urllib) — nothing to pip install.

Run:
    ADMIN_PASSWORD=... python3 test_group_isolation.py

Everything it creates is prefixed/suffixed so it's easy to find and clean up
by hand afterward (group "City Mart", user "citymart_tester", project
"City Mart Test Project") — this script does NOT delete anything itself.

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
PROJECT_NAME = os.environ.get("PROJECT_NAME", "City Mart Test Project")


def call(method, path, body=None, token=None, expect=(200, 201, 204)):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
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
        return e.code, detail


def login(username, password):
    status, result = call("POST", "/auth/login", {"username": username, "password": password})
    if status != 200 or not result:
        raise SystemExit(f"Login as {username} failed ({status}): {result}")
    return result["access_token"]


def main():
    print(f"Logging in as {ADMIN_USERNAME}...")
    admin_token = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    print("  OK")

    print(f"\nCreating group '{GROUP_NAME}'...")
    status, group = call("POST", "/admin/groups", {"name": GROUP_NAME}, token=admin_token, expect=(201, 409))
    if status == 409:
        print("  already exists — fetching it")
        _, groups = call("GET", "/admin/groups", token=admin_token)
        group = next(g for g in groups if g["name"] == GROUP_NAME)
    group_id = group["id"]
    print(f"  group_id={group_id}")

    print(f"\nCreating project '{PROJECT_NAME}'...")
    status, project = call(
        "POST", "/projects", {"name": PROJECT_NAME}, token=admin_token, expect=(201, 200)
    )
    if status not in (200, 201):
        raise SystemExit(f"Failed to create project: {project}")
    project_id = project["id"]
    print(f"  project_id={project_id}")

    print(f"\nGranting '{GROUP_NAME}' pool access to its own project...")
    call(
        "POST",
        f"/admin/groups/{group_id}/project-access",
        {"project_id": project_id},
        token=admin_token,
        expect=(201, 409),
    )
    print("  OK")

    print(f"\nCreating user '{TEST_USERNAME}' in group '{GROUP_NAME}'...")
    status, user = call(
        "POST",
        f"/admin/groups/{group_id}/users",
        {"username": TEST_USERNAME, "password": TEST_PASSWORD},
        token=admin_token,
        expect=(201, 409),
    )
    if status == 409:
        print("  user already exists — assuming already set up correctly, continuing")
    else:
        print("  OK")

    print(f"\nCreating 'Editor' role in '{GROUP_NAME}' with documents access...")
    status, role = call(
        "POST", f"/admin/groups/{group_id}/roles", {"name": "Editor"}, token=admin_token, expect=(201, 409)
    )
    if status == 409:
        _, roles = call("GET", f"/admin/groups/{group_id}/roles", token=admin_token)
        role = next(r for r in roles if r["name"] == "Editor")
    role_id = role["id"]
    call(
        "POST",
        f"/roles/{role_id}/access",
        {"service_name": "documents", "access_level": "edit"},
        token=admin_token,
    )
    print(f"  role_id={role_id}")

    print(f"\nAssigning Editor role to '{TEST_USERNAME}'...")
    call(
        "POST",
        f"/roles/{role_id}/assign",
        {"username": TEST_USERNAME},
        token=admin_token,
        expect=(201, 409),
    )
    print("  OK")

    print(f"\nLogging in as '{TEST_USERNAME}'...")
    test_token = login(TEST_USERNAME, TEST_PASSWORD)
    print("  OK")

    print(f"\nFetching projects visible to '{TEST_USERNAME}'...")
    status, visible_projects = call("GET", "/projects", token=test_token)
    if status != 200:
        raise SystemExit(f"Failed to list projects as test user: {visible_projects}")
    visible_ids = {p["id"] for p in visible_projects}
    visible_names = sorted(p["name"] for p in visible_projects)
    print(f"  sees {len(visible_projects)} project(s): {visible_names}")

    print("\n" + "=" * 60)
    if visible_ids == {project_id}:
        print(f"PASS: '{TEST_USERNAME}' sees ONLY '{PROJECT_NAME}' — group isolation is working.")
    elif project_id not in visible_ids:
        print(f"FAIL: '{TEST_USERNAME}' does NOT see their own group's project '{PROJECT_NAME}'.")
        print("      Check that the project-access grant above actually succeeded.")
    else:
        extra = visible_ids - {project_id}
        print(f"FAIL: '{TEST_USERNAME}' sees {len(extra)} project(s) outside '{GROUP_NAME}': {extra}")
        print("      Group isolation is NOT working — they should only see their own group's projects.")
    print("=" * 60)


if __name__ == "__main__":
    main()
