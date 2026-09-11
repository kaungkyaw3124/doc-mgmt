#!/usr/bin/env python3
"""
One-off migration: grants a named group's project pool (GroupProjectAccess)
access to every existing project in document-service, via the platform's own
API. Uses only the Python standard library (urllib) — nothing to pip install.

Why this is needed: get_user_allowed_project_ids (auth-service/app/core/
authz.py) used to default a user with no explicit grants to "ALL" projects.
That default is now scoped instead — a non-superuser with no
UserProjectAccess rows falls back to the union of their group(s)'
GroupProjectAccess pool, and sees nothing if that pool is empty. Any group
whose pool was never explicitly provisioned (true for every group before
this change, since it never mattered) will suddenly see zero projects once
that fix ships, unless its pool is backfilled first.

Run this once per group that should keep seeing today's existing projects,
BEFORE deploying the authz fix (or immediately after, before anyone notices
data "disappear"). Idempotent — a project already in the group's pool is
skipped (the API returns 409, treated as success), so re-running is safe.

Usage:
    GROUP_NAME=Operation ADMIN_PASSWORD=... python3 migrate_projects_to_group.py

Reads BASE_URL / ADMIN_USERNAME / ADMIN_PASSWORD / GROUP_NAME from the
environment if set, otherwise falls back to the local-dev defaults below —
don't rely on those defaults against a shared/staging environment.
"""

import json
import os
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080/api")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
GROUP_NAME = os.environ.get("GROUP_NAME", "Operation")

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
            return status, parsed
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return e.code, detail


def login():
    global TOKEN
    print(f"Logging in as {ADMIN_USERNAME}...")
    status, result = call("POST", "/auth/login", {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    if status not in (200, 201) or not result:
        raise SystemExit(f"Login failed ({status}): {result} — check ADMIN_USERNAME/ADMIN_PASSWORD.")
    TOKEN = result["access_token"]
    print("  logged in OK")


def get_or_create_group(name: str) -> str:
    print(f"Looking up group '{name}'...")
    status, groups = call("GET", "/admin/groups")
    if status != 200:
        raise SystemExit(f"Failed to list groups ({status}): {groups}")
    for g in groups:
        if g["name"] == name:
            print(f"  found group {g['id']}")
            return g["id"]

    print(f"  group '{name}' does not exist yet — creating it")
    status, group = call("POST", "/admin/groups", {"name": name})
    if status != 201:
        raise SystemExit(f"Failed to create group '{name}' ({status}): {group}")
    print(f"  created group {group['id']}")
    return group["id"]


def list_all_projects() -> list[dict]:
    print("Fetching existing projects...")
    status, projects = call("GET", "/projects")
    if status != 200:
        raise SystemExit(f"Failed to list projects ({status}): {projects}")
    print(f"  found {len(projects)} project(s)")
    return projects


def grant_project_to_group(group_id: str, project_id: str) -> None:
    status, result = call(
        "POST",
        f"/admin/groups/{group_id}/project-access",
        {"project_id": project_id},
        expect=(201, 409),
    )
    if status == 201:
        print(f"  + granted project {project_id}")
    elif status == 409:
        print(f"  = already granted: {project_id}")
    else:
        print(f"  ! failed to grant {project_id} ({status}): {result}")


def main():
    login()
    group_id = get_or_create_group(GROUP_NAME)
    projects = list_all_projects()
    if not projects:
        print("No existing projects to migrate — nothing to do.")
        return
    print(f"Granting group '{GROUP_NAME}' ({group_id}) access to all {len(projects)} project(s)...")
    for project in projects:
        grant_project_to_group(group_id, project["id"])
    print("Done.")


if __name__ == "__main__":
    main()
