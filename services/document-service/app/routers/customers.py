import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app import models, schemas

router = APIRouter(prefix="/customers", tags=["customers"])


def _require_edit_access(x_access_level: str | None):
    """Mirrors routers/documents.py's helper — missing header defaults to
    "edit" (backward-compat, restrictions are opt-in)."""
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="your role has view-only access to documents")


@router.post("", response_model=schemas.CustomerOut, status_code=201)
def create_customer(
    payload: schemas.CustomerCreate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    customer = models.Customer(**payload.model_dump())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=list[schemas.CustomerOut])
def list_customers(q: str | None = None, db: Session = Depends(get_db)):
    query = db.query(models.Customer).filter(models.Customer.is_deleted == False)  # noqa: E712
    if q:
        query = query.filter(models.Customer.name.ilike(f"%{q}%"))
    return query.order_by(models.Customer.created_at.desc()).all()


@router.get("/trash", response_model=list[schemas.CustomerOut])
def list_trashed_customers(db: Session = Depends(get_db)):
    """The recycle bin — soft-deleted customers."""
    query = db.query(models.Customer).filter(models.Customer.is_deleted == True)  # noqa: E712
    return query.order_by(models.Customer.created_at.desc()).all()


@router.get("/{customer_id}", response_model=schemas.CustomerOut)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@router.patch("/{customer_id}", response_model=schemas.CustomerOut)
def update_customer(
    customer_id: uuid.UUID,
    payload: schemas.CustomerUpdate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)

    db.commit()
    db.refresh(customer)
    return customer


@router.patch("/{customer_id}/trash", response_model=schemas.CustomerOut)
def trash_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Soft delete — hides it from the main list, but keeps it recoverable
    via the recycle bin. Existing documents that reference this customer
    are unaffected. For a permanent purge, use DELETE /{customer_id}
    instead (only called from within the recycle bin)."""
    _require_edit_access(x_access_level)
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    customer.is_deleted = True
    db.commit()
    db.refresh(customer)
    return customer


@router.patch("/{customer_id}/restore", response_model=schemas.CustomerOut)
def restore_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    customer.is_deleted = False
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Permanent purge — only reachable from within the recycle bin.
    Blocked (with a clear message) if any document still references this
    customer, rather than failing with a raw database error."""
    _require_edit_access(x_access_level)
    customer = db.query(models.Customer).filter_by(id=customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    try:
        db.delete(customer)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Can't permanently delete — one or more documents still reference this customer. Reassign or delete those documents first.",
        )
