import uuid

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.group_scope import Scope, get_scope, scoped, get_or_404, group_for_create
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
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    data = payload.model_dump()
    data["group_id"] = group_for_create(scope, payload.group_id)
    customer = models.Customer(**data)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=list[schemas.CustomerOut])
def list_customers(q: str | None = None, db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    query = scoped(db, models.Customer, scope).filter(models.Customer.is_deleted == False)  # noqa: E712
    if q:
        query = query.filter(models.Customer.name.ilike(f"%{q}%"))
    return query.order_by(models.Customer.created_at.desc()).all()


@router.get("/trash", response_model=list[schemas.CustomerOut])
def list_trashed_customers(db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    """The recycle bin — soft-deleted customers."""
    query = scoped(db, models.Customer, scope).filter(models.Customer.is_deleted == True)  # noqa: E712
    return query.order_by(models.Customer.created_at.desc()).all()


@router.get("/{customer_id}", response_model=schemas.CustomerOut)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    return get_or_404(db, models.Customer, customer_id, scope, "customer not found")


@router.patch("/{customer_id}", response_model=schemas.CustomerOut)
def update_customer(
    customer_id: uuid.UUID,
    payload: schemas.CustomerUpdate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    customer = get_or_404(db, models.Customer, customer_id, scope, "customer not found")

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
    scope: Scope = Depends(get_scope),
):
    """Soft delete — hides it from the main list, but keeps it recoverable
    via the recycle bin. Existing documents that reference this customer
    are unaffected. For a permanent purge, use DELETE /{customer_id}
    instead (only called from within the recycle bin)."""
    _require_edit_access(x_access_level)
    customer = get_or_404(db, models.Customer, customer_id, scope, "customer not found")
    customer.is_deleted = True
    db.commit()
    db.refresh(customer)
    return customer


@router.patch("/{customer_id}/restore", response_model=schemas.CustomerOut)
def restore_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    customer = get_or_404(db, models.Customer, customer_id, scope, "customer not found")
    customer.is_deleted = False
    db.commit()
    db.refresh(customer)
    return customer


# DEV-ONLY: delete-all BEGIN — one-click wipe for dev testing (loop iteration 15)
@router.delete("")
def delete_all_customers(
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """Permanently deletes every customer in the caller's groups (recycle bin
    included; a superuser: all). Documents are kept; their customer_id is
    cleared first so the FK can't block it."""
    from app.core.dev_delete_all import refuse_in_production_or_view_only

    refuse_in_production_or_view_only(x_access_level)
    ids = [row.id for row in scoped(db, models.Customer, scope).with_entities(models.Customer.id).all()]
    if not ids:
        return {"deleted": 0}
    scoped(db, models.Document, scope).filter(models.Document.customer_id.in_(ids)).update(
        {models.Document.customer_id: None}, synchronize_session=False
    )
    deleted = scoped(db, models.Customer, scope).filter(models.Customer.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": deleted}
# DEV-ONLY: delete-all END


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """Permanent purge — only reachable from within the recycle bin.
    Blocked (with a clear message) if any document still references this
    customer, rather than failing with a raw database error."""
    _require_edit_access(x_access_level)
    customer = get_or_404(db, models.Customer, customer_id, scope, "customer not found")
    try:
        db.delete(customer)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Can't permanently delete — one or more documents still reference this customer. Reassign or delete those documents first.",
        )
