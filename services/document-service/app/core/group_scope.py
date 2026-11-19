"""
Department (group) data separation — the ONE place this service decides
which documents/customers/projects a caller may see or touch.

Nginx forwards two headers resolved by auth-service's /verify (and always
overwrites any client-sent copy; the X-Internal-Secret check in
gateway_auth.py proves the request came through Nginx):
  X-Group-Ids     comma-separated ids of the caller's ACTIVE groups
                  (a superuser gets every active group, so they can pick any)
  X-Is-Superuser  "1" or "0"

Rules: a superuser sees every row (including group_id NULL = "unassigned");
everyone else only rows whose group_id is one of their groups. Missing
headers mean "no groups" — fail closed, never "everything". Rows outside the
scope are reported as 404 so their existence is not revealed.

Every router query on Document/Customer/Project goes through `scoped()` /
`get_or_404()`; code that must see every row on purpose (global document
numbering, search re-index, internal product-id lookups) passes ALL.
"""
import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException
from sqlalchemy import false


@dataclass(frozen=True)
class Scope:
    superuser: bool
    group_ids: frozenset  # of uuid.UUID


ALL = Scope(superuser=True, group_ids=frozenset())  # internal use only, never from a request


def _parse_ids(value: str | None) -> frozenset:
    ids = set()
    for part in (value or "").split(","):
        try:
            ids.add(uuid.UUID(part.strip()))
        except ValueError:
            continue  # blank or malformed -> ignored (fails closed)
    return frozenset(ids)


def get_scope(
    x_group_ids: str | None = Header(default=None, alias="X-Group-Ids"),
    x_is_superuser: str | None = Header(default=None, alias="X-Is-Superuser"),
) -> Scope:
    """FastAPI dependency: the caller's scope from the gateway headers."""
    return Scope(superuser=x_is_superuser == "1", group_ids=_parse_ids(x_group_ids))


def filter_query(query, model, scope: Scope):
    if scope.superuser:
        return query
    if not scope.group_ids:
        return query.filter(false())
    return query.filter(model.group_id.in_(scope.group_ids))


def scoped(db, model, scope: Scope):
    """db.query(model) limited to the caller's groups."""
    return filter_query(db.query(model), model, scope)


def get_or_404(db, model, row_id, scope: Scope, detail: str):
    row = scoped(db, model, scope).filter(model.id == row_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=detail)
    return row


def group_for_create(scope: Scope, requested: uuid.UUID | None) -> uuid.UUID:
    """The group a new record is owned by: the creator's only group, or the
    one they picked (which must be one of theirs)."""
    if not scope.group_ids:
        raise HTTPException(status_code=403, detail="You are not in any group — ask an admin")
    if requested is None:
        if not scope.superuser and len(scope.group_ids) == 1:
            return next(iter(scope.group_ids))
        raise HTTPException(status_code=422, detail="Choose a group for this record (group_id).")
    if requested not in scope.group_ids:
        if scope.superuser:
            raise HTTPException(status_code=422, detail="That group does not exist or is disabled.")
        raise HTTPException(status_code=403, detail="You are not a member of that group.")
    return requested


def require_superuser(scope: Scope):
    if not scope.superuser:
        raise HTTPException(status_code=403, detail="superuser privileges required")
