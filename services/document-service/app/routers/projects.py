import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.concurrency import check_version, claim_idempotency_key, remember
from app.core.group_scope import Scope, _parse_ids, get_scope, scoped, get_or_404, group_for_create
from app import models, schemas

router = APIRouter(prefix="/projects", tags=["projects"])


def _parse_allowed_projects(x_allowed_projects: str | None):
    """
    Returns None (no restriction) or a set of allowed project ids (UUIDs —
    plain strings break `Project.id.in_()` on a non-native UUID column, and
    a malformed id is dropped instead of a 500, failing closed).
    """
    if not x_allowed_projects or x_allowed_projects == "ALL":
        return None
    return set(_parse_ids(x_allowed_projects))  # "NONE" parses to the empty set


def _require_edit_access(x_access_level: str | None):
    """Mirrors routers/documents.py's helper — missing header defaults to
    "edit" (backward-compat, restrictions are opt-in)."""
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="your role has view-only access to documents")


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(
    payload: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_edit_access(x_access_level)
    data = payload.model_dump()
    data["group_id"] = group_for_create(scope, payload.group_id)
    held, replay_id = claim_idempotency_key(db, idempotency_key, "POST /projects")
    if replay_id:
        return get_or_404(db, models.Project, replay_id, scope, "project not found")
    project = models.Project(id=uuid.uuid4(), **data)
    remember(held, project.id)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    query = scoped(db, models.Project, scope).filter(models.Project.is_deleted == False)  # noqa: E712
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        query = query.filter(models.Project.id.in_(allowed))
    return query.order_by(models.Project.created_at.desc()).all()


@router.get("/trash", response_model=list[schemas.ProjectOut])
def list_trashed_projects(
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """The recycle bin — soft-deleted projects, same project-visibility rule as the main list."""
    query = scoped(db, models.Project, scope).filter(models.Project.is_deleted == True)  # noqa: E712
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        query = query.filter(models.Project.id.in_(allowed))
    return query.order_by(models.Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    project = get_or_404(db, models.Project, project_id, scope, "project not found")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and project.id not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this project")

    return project


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: uuid.UUID,
    payload: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    project = get_or_404(db, models.Project, project_id, scope, "project not found")
    check_version(project, payload.version)

    for field, value in payload.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


@router.patch("/{project_id}/trash", response_model=schemas.ProjectOut)
def trash_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Soft delete — hides it from the main list, but keeps it recoverable
    via the recycle bin. Existing documents that reference this project
    are unaffected. For a permanent purge, use DELETE /{project_id}
    instead (only called from within the recycle bin)."""
    _require_edit_access(x_access_level)
    project = get_or_404(db, models.Project, project_id, scope, "project not found")
    project.is_deleted = True
    db.commit()
    db.refresh(project)
    return project


@router.patch("/{project_id}/restore", response_model=schemas.ProjectOut)
def restore_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    project = get_or_404(db, models.Project, project_id, scope, "project not found")
    project.is_deleted = False
    db.commit()
    db.refresh(project)
    return project


# DEV-ONLY: delete-all BEGIN — one-click wipe for dev testing (loop iteration 15)
@router.delete("")
def delete_all_projects(
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Permanently deletes every project in the caller's groups (recycle bin
    included; a superuser: all). Documents are kept; their project_id is
    cleared first so the FK can't block it."""
    from app.core.dev_delete_all import refuse_in_production_or_view_only

    refuse_in_production_or_view_only(x_access_level)
    ids = [row.id for row in scoped(db, models.Project, scope).with_entities(models.Project.id).all()]
    if not ids:
        return {"deleted": 0}
    scoped(db, models.Document, scope).filter(models.Document.project_id.in_(ids)).update(
        {models.Document.project_id: None}, synchronize_session=False
    )
    deleted = scoped(db, models.Project, scope).filter(models.Project.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}
# DEV-ONLY: delete-all END


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Permanent purge — only reachable from within the recycle bin.
    Blocked (with a clear message) if any document still references this
    project, rather than failing with a raw database error."""
    _require_edit_access(x_access_level)
    project = get_or_404(db, models.Project, project_id, scope, "project not found")
    try:
        db.delete(project)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Can't permanently delete — one or more documents still reference this project. Reassign or delete those documents first.",
        )


@router.get("/{project_id}/documents", response_model=list[schemas.DocumentOut])
def get_project_documents(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    scope: Scope = Depends(get_scope),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """What's been sent/created under this project — used when viewing a project."""
    project = get_or_404(db, models.Project, project_id, scope, "project not found")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and project.id not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this project")

    return (
        scoped(db, models.Document, scope)
        .filter_by(project_id=project_id)
        .order_by(models.Document.created_at.desc())
        .all()
    )
