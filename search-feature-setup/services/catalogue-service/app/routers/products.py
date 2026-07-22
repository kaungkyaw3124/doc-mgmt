import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.search_client import index_product
from app import models, schemas

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=schemas.ProductOut, status_code=201)
def create_product(payload: schemas.ProductCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Product).filter_by(sku=payload.sku).first()
    if existing:
        raise HTTPException(status_code=409, detail="sku already exists")

    product = models.Product(**payload.model_dump())
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
):
    query = db.query(models.Product)
    if category:
        query = query.filter(models.Product.category == category)
    if q:
        query = query.filter(models.Product.name.ilike(f"%{q}%"))
    return query.order_by(models.Product.created_at.desc()).all()


@router.get("/{product_id}", response_model=schemas.ProductOut)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = db.query(models.Product).filter_by(id=product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="product not found")
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
