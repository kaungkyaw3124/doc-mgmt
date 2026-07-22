import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app import models, schemas

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post("", response_model=schemas.CustomerOut, status_code=201)
def create_customer(payload: schemas.CustomerCreate, db: Session = Depends(get_db)):
    customer = models.Customer(**payload.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=list[schemas.CustomerOut])
def list_customers(q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.Customer)
    if q:
        query = query.filter(models.Customer.name.ilike(f"%{q}%"))
    return query.order_by(models.Customer.created_at.desc()).all()


@router.get("/{customer_id}", response_model=schemas.CustomerOut)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@router.patch("/{customer_id}", response_model=schemas.CustomerOut)
def update_customer(customer_id: uuid.UUID, payload: schemas.CustomerUpdate, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)

    db.commit()
    db.refresh(customer)
    return customer
