"""Asks document-service which live projects the given groups own — the pool
a user may be granted (project access is per user, limited to their groups).

Fails CLOSED: if document-service can't answer, the caller gets 503 and no
grant is made — a hiccup must never let a user be granted another group's project.
"""
import json
import urllib.error
import urllib.request

from fastapi import HTTPException

from app.core.config import settings


def project_ids_in_groups(group_ids: list[str]) -> set[str]:
    if not group_ids:
        return set()
    # The same scope headers nginx forwards for a non-superuser in these groups,
    # so document-service's own group filter (group_scope.py) decides.
    req = urllib.request.Request(f"{settings.document_service_url}/projects", headers={
        "X-Internal-Secret": settings.internal_shared_secret,
        "X-Group-Ids": ",".join(group_ids),
        "X-Is-Superuser": "0",
        "X-Allowed-Projects": "ALL",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {p["id"] for p in json.load(resp)}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=503, detail="could not check which projects belong to the user's groups — try again")
