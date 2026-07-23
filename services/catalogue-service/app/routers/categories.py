import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app import models, schemas

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[schemas.CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    """Anyone with product access can list categories (needed to populate
    the product form's dropdown) — only *creating*/*editing* one is gated."""
    return db.query(models.Category).order_by(models.Category.name).all()


@router.post("", response_model=schemas.CategoryOut, status_code=201)
def create_category(
    payload: schemas.CategoryCreate,
    db: Session = Depends(get_db),
    x_has_category_access: str | None = Header(default=None, alias="X-Has-Category-Access"),
):
    if x_has_category_access != "true":
        raise HTTPException(status_code=403, detail="you don't have permission to create categories")

    existing = db.query(models.Category).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="category already exists")

    category = models.Category(name=payload.name, short_term=payload.short_term.upper(), description=payload.description)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=schemas.CategoryOut)
def update_category(
    category_id: uuid.UUID,
    payload: schemas.CategoryUpdate,
    db: Session = Depends(get_db),
    x_has_category_access: str | None = Header(default=None, alias="X-Has-Category-Access"),
):
    if x_has_category_access != "true":
        raise HTTPException(status_code=403, detail="you don't have permission to edit categories")

    category = db.query(models.Category).filter_by(id=category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="category not found")

    if payload.name and payload.name != category.name:
        existing = db.query(models.Category).filter_by(name=payload.name).first()
        if existing:
            raise HTTPException(status_code=409, detail="another category already has that name")
        category.name = payload.name

    if payload.short_term is not None:
        category.short_term = payload.short_term.upper()

    if payload.description is not None:
        category.description = payload.description

    db.commit()
    db.refresh(category)
    return category
