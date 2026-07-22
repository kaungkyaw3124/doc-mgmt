import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app import models, schemas

router = APIRouter(prefix="/projects", tags=["projects"])


def _parse_allowed_projects(x_allowed_projects: str | None):
    """
    Returns None (no restriction) or a set of allowed project_id strings.
    Mirrors the same helper in routers/documents.py — kept local here to
    avoid a cross-router import for one small function.
    """
    if not x_allowed_projects or x_allowed_projects == "ALL":
        return None
    if x_allowed_projects == "NONE":
        return set()
    return set(x_allowed_projects.split(","))


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = models.Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    query = db.query(models.Project)
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        query = query.filter(models.Project.id.in_(allowed))
    return query.order_by(models.Project.created_at.desc()).all()


@router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    project = db.query(models.Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and str(project.id) not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this project")

    return project


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: uuid.UUID, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = db.query(models.Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}/documents", response_model=list[schemas.DocumentOut])
def get_project_documents(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """What's been sent/created under this project — used when viewing a project."""
    project = db.query(models.Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and str(project.id) not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this project")

    return (
        db.query(models.Document)
        .filter_by(project_id=project_id)
        .order_by(models.Document.created_at.desc())
        .all()
    )
