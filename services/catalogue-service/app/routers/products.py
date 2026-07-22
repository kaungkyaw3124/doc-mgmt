import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.search_client import index_product, remove_product_from_index
from app.core.storage import upload_file, get_presigned_url
from app.core.document_client import get_visible_product_ids
from app import models, schemas

router = APIRouter(prefix="/products", tags=["products"])


def _generate_sku(db: Session, category: str | None) -> str:
    """e.g. COM-0001 for a "Computer" category product. Falls back to
    "PRD-0001" if no category was given. Finds the next free number for
    that prefix, so it stays sequential per-category."""
    prefix = re.sub(r"[^A-Z0-9]", "", (category or "PRD").upper())[:3] or "PRD"

    existing_skus = [
        p.sku for p in db.query(models.Product.sku).filter(models.Product.sku.like(f"{prefix}-%")).all()
    ]
    max_n = 0
    for sku in existing_skus:
        match = re.match(rf"^{re.escape(prefix)}-(\d+)$", sku)
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"{prefix}-{max_n + 1:04d}"


@router.post("", response_model=schemas.ProductOut, status_code=201)
def create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    sku = payload.sku or _generate_sku(db, payload.category)

    existing = db.query(models.Product).filter_by(sku=sku).first()
    if existing:
        raise HTTPException(status_code=409, detail="sku already exists")

    data = payload.model_dump()
    data["sku"] = sku
    product = models.Product(**data)
    db.add(product)
    db.commit()
    db.refresh(product)
    index_product(product)
    return product


@router.get("", response_model=list[schemas.ProductOut])
def list_products(
    category: str | None = None,
    q: str | None = None,   # simple name search, e.g. ?q=widget
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    query = db.query(models.Product)
    if category:
        query = query.filter(models.Product.category == category)
    if q:
        query = query.filter(models.Product.name.ilike(f"%{q}%"))

    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None:
        query = query.filter(models.Product.id.in_(visible_ids))

    return query.order_by(models.Product.created_at.desc()).all()


@router.get("/{product_id}", response_model=schemas.ProductOut)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")

    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None and str(product.id) not in visible_ids:
        raise HTTPException(status_code=403, detail="you don't have access to this product")

    return product


@router.patch("/{product_id}", response_model=schemas.ProductOut)
def update_product(product_id: uuid.UUID, payload: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    index_product(product)
    return product


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")
    db.delete(product)
    db.commit()
    remove_product_from_index(product_id)


@router.post("/{product_id}/file")
def upload_product_file(product_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")

    object_key = f"{product.sku}/{file.filename}"
    upload_file(file.file, object_key, content_type=file.content_type or "application/octet-stream")

    product.image_object_key = object_key
    db.commit()
    return {"image_object_key": object_key}


@router.get("/{product_id}/file-url")
def get_product_file_url(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product or not product.image_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this product")
    return {"url": get_presigned_url(product.image_object_key)}
