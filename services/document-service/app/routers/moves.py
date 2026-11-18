"""
Superuser-only: move documents, customers and projects to another group, and
count what is still unassigned (loop iteration 20). The rules live in
app/core/group_move.py; this file only wires the routes.

Included in main.py BEFORE the entity routers, so GET /documents/unassigned-counts
is matched before documents.py's GET /documents/{document_id}.
"""
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.audit import log_action
from app.core.db import get_db
from app.core.group_move import ENTITIES, move
from app.core.group_scope import ALL, Scope, get_scope, require_superuser, scoped
from app.core.search_client import index_document

router = APIRouter(tags=["group moves"])


def _run(entity, ids, payload: schemas.MoveToGroup, db: Session, scope: Scope, username: str | None):
    require_superuser(scope)
    if payload.group_id not in scope.group_ids:  # a superuser's scope = every ACTIVE group
        raise HTTPException(status_code=422, detail="That group does not exist or is disabled.")
    counts, moved_docs = move(db, entity, ids, payload.group_id, payload.with_links, payload.dry_run, username)
    for doc in moved_docs:
        index_document(doc)  # the old group stops finding it in search, the new one does at once
        log_action(db, doc.id, username, "moved_group")
    return {"moved": counts, "dry_run": payload.dry_run}


def _add_routes(entity):
    @router.post(f"/{entity}/move")
    def bulk_move(
        payload: schemas.BulkMoveToGroup,
        db: Session = Depends(get_db),
        scope: Scope = Depends(get_scope),
        x_username: str | None = Header(default=None, alias="X-Username"),
    ):
        return _run(entity, payload.ids, payload, db, scope, x_username)

    @router.post(f"/{entity}/{{row_id}}/move")
    def move_one(
        row_id: uuid.UUID,
        payload: schemas.MoveToGroup,
        db: Session = Depends(get_db),
        scope: Scope = Depends(get_scope),
        x_username: str | None = Header(default=None, alias="X-Username"),
    ):
        return _run(entity, [row_id], payload, db, scope, x_username)


for _entity in ENTITIES:
    _add_routes(_entity)


@router.get("/documents/unassigned-counts")
def unassigned_counts(db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    """Admin → Groups "Unassigned data" card: rows with no group yet (recycle
    bin included — assign-unassigned assigns those too)."""
    require_superuser(scope)
    return {e: scoped(db, m, ALL).filter(m.group_id == None).count() for e, m in ENTITIES.items()}  # noqa: E711
