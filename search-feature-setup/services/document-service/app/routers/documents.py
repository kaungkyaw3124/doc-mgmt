import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.storage import upload_file, get_presigned_url
from app.core.catalogue_client import (
    get_product,
    ProductNotFoundError,
    CatalogueServiceUnavailableError,
)
from app.core.search_client import index_document
from app import models, schemas

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=schemas.DocumentOut, status_code=201)
def create_document(payload: schemas.DocumentCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Document).filter_by(doc_number=payload.doc_number).first()
    if existing:
        raise HTTPException(status_code=409, detail="doc_number already exists")

    if payload.customer_id:
        customer = db.query(models.Customer).filter_by(id=payload.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail=f"customer {payload.customer_id} not found")

    doc = models.Document(
        doc_type=payload.doc_type,
        doc_number=payload.doc_number,
        customer_id=payload.customer_id,
        currency=payload.currency,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        doc_metadata=payload.doc_metadata,
    )

    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for item_in in payload.items:
        description = item_in.description
        unit_price = item_in.unit_price

        if item_in.product_id:
            try:
                product = get_product(item_in.product_id)
            except ProductNotFoundError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except CatalogueServiceUnavailableError as exc:
                raise HTTPException(status_code=502, detail=str(exc))

            # Fill in from the catalogue only where the caller didn't specify —
            # lets you override price/description per-document (e.g. a discount)
            # while still validating the product actually exists.
            if description is None:
                description = product["name"]
            if unit_price is None:
                unit_price = Decimal(str(product["unit_price"]))

        if description is None or unit_price is None:
            raise HTTPException(
                status_code=422,
                detail="each item needs either a product_id, or both description and unit_price",
            )

        line_total = item_in.quantity * unit_price
        tax_amount = line_total * (item_in.tax_rate / Decimal("100"))
        subtotal += line_total
        tax_total += tax_amount
        doc.items.append(
            models.LineItem(
                product_id=item_in.product_id,
                description=description,
                quantity=item_in.quantity,
                unit_price=unit_price,
                tax_rate=item_in.tax_rate,
                line_total=line_total,
            )
        )

    doc.subtotal = subtotal
    doc.tax_total = tax_total
    doc.total = subtotal + tax_total
    doc.status = "draft"

    db.add(doc)
    db.commit()
    db.refresh(doc)
    index_document(doc)
    return doc


@router.get("", response_model=list[schemas.DocumentOut])
def list_documents(
    doc_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(models.Document)
    if doc_type:
        query = query.filter(models.Document.doc_type == doc_type)
    if status:
        query = query.filter(models.Document.status == status)
    return query.order_by(models.Document.created_at.desc()).all()


@router.get("/{document_id}", response_model=schemas.DocumentOut)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.patch("/{document_id}/status", response_model=schemas.DocumentOut)
def update_status(document_id: uuid.UUID, new_status: str, db: Session = Depends(get_db)):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    doc.status = new_status
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/{document_id}/file")
def upload_document_file(document_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    object_key = f"{doc.doc_type}/{doc.doc_number}/{file.filename}"
    upload_file(file.file, object_key, content_type=file.content_type or "application/octet-stream")

    doc.file_object_key = object_key
    db.commit()
    db.refresh(doc)
    return {"file_object_key": object_key}


@router.get("/{document_id}/file-url")
def get_document_file_url(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc or not doc.file_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this document")
    return {"url": get_presigned_url(doc.file_object_key)}
