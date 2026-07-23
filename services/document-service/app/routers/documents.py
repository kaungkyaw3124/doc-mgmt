import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.storage import upload_file, get_presigned_url, get_file_bytes
from app.core.catalogue_client import (
    get_product,
    ProductNotFoundError,
    CatalogueServiceUnavailableError,
)
from app.core.search_client import index_document, remove_document_from_index
from app.core.export_quotation import generate_quotation_xlsx
from app.core.export_pdf import generate_quotation_pdf
from app.core.audit import log_action
from app import models, schemas

router = APIRouter(prefix="/documents", tags=["documents"])

VALID_CURRENCIES = {"USD", "MMK"}


def _require_edit_access(x_access_level: str | None):
    """
    x_access_level comes from Nginx (forwarded from auth-service's /verify).
    Missing header = treated as "edit" (backward-compat default, matches the
    same opt-in-restriction philosophy used for project access).
    """
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="your role has view-only access to documents")


def _process_items(items_payload, db: Session):
    """Validates/builds LineItem objects from a list of DocumentItemIn,
    returning (line_items, subtotal, tax_total). Shared between create and
    full-update so both paths handle product linking identically."""
    line_items = []
    subtotal = Decimal("0")
    tax_total = Decimal("0")
    for item_in in items_payload:
        description = item_in.description
        unit_price = item_in.unit_price

        if item_in.product_id:
            try:
                product = get_product(item_in.product_id)
            except ProductNotFoundError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except CatalogueServiceUnavailableError as exc:
                raise HTTPException(status_code=502, detail=str(exc))

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
        line_items.append(
            models.LineItem(
                product_id=item_in.product_id,
                description=description,
                unit=item_in.unit,
                quantity=item_in.quantity,
                unit_price=unit_price,
                tax_rate=item_in.tax_rate,
                line_total=line_total,
            )
        )
    return line_items, subtotal, tax_total


def _generate_doc_number(db: Session) -> str:
    """
    e.g. SS-20260723/001 — {primary company's short_name}-{YYYYMMDD}/{3-digit
    sequence for that company+day, starting at 001}. Falls back to "DOC" as
    the prefix if there's no primary company yet, or it has no short_name set.
    """
    company = db.query(models.Company).filter_by(is_primary=True).first()
    prefix = (company.short_name if company and company.short_name else "DOC").upper()

    today = date.today()
    date_part = f"{today.year}{today.month:02d}{today.day:02d}"
    base_prefix = f"{prefix}-{date_part}/"

    existing_count = (
        db.query(models.Document)
        .filter(models.Document.doc_number.like(f"{base_prefix}%"))
        .count()
    )

    seq = existing_count + 1
    while True:
        candidate = f"{base_prefix}{seq:03d}"
        if not db.query(models.Document).filter_by(doc_number=candidate).first():
            return candidate
        seq += 1


@router.post("", response_model=schemas.DocumentOut, status_code=201)
def create_document(
    payload: schemas.DocumentCreate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    x_username: str | None = Header(default=None, alias="X-Username"),
):
    _require_edit_access(x_access_level)

    doc_number = payload.doc_number or _generate_doc_number(db)

    existing = db.query(models.Document).filter_by(doc_number=doc_number).first()
    if existing:
        raise HTTPException(status_code=409, detail="doc_number already exists")

    if payload.currency not in VALID_CURRENCIES:
        raise HTTPException(status_code=422, detail=f"currency must be one of {sorted(VALID_CURRENCIES)}")

    if payload.customer_id:
        customer = db.query(models.Customer).filter_by(id=payload.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail=f"customer {payload.customer_id} not found")

    if payload.project_id:
        project = db.query(models.Project).filter_by(id=payload.project_id).first()
        if not project:
            raise HTTPException(status_code=400, detail=f"project {payload.project_id} not found")

    doc = models.Document(
        doc_type=payload.doc_type,
        doc_number=doc_number,
        customer_id=payload.customer_id,
        project_id=payload.project_id,
        currency=payload.currency,
        issue_date=payload.issue_date,
        due_date=payload.due_date,
        doc_metadata=payload.doc_metadata,
        terms_and_conditions=payload.terms_and_conditions,
    )

    line_items, subtotal, tax_total = _process_items(payload.items, db)
    doc.items = line_items
    doc.subtotal = subtotal
    doc.tax_total = tax_total
    doc.total = subtotal + tax_total
    doc.status = "draft"

    db.add(doc)
    db.commit()
    db.refresh(doc)
    index_document(doc)
    log_action(db, doc.id, x_username, "created")
    return doc


def _parse_allowed_projects(x_allowed_projects: str | None):
    """
    Returns None (no restriction) or a set of allowed project_id strings,
    based on the X-Allowed-Projects header Nginx forwards from auth-service's
    /verify response. "ALL" or a missing/empty header means unrestricted —
    matches the "restrictions are opt-in" design (see auth-service's
    get_user_allowed_project_ids for the full explanation).
    """
    if not x_allowed_projects or x_allowed_projects == "ALL":
        return None
    if x_allowed_projects == "NONE":
        return set()
    return set(x_allowed_projects.split(","))


@router.get("/visible-product-ids")
def get_visible_product_ids(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """
    Called internally by catalogue-service to figure out which products a
    project-restricted user is allowed to see — a product is visible if
    it's referenced by at least one line item on a document the caller can
    see (same project-visibility rule as the documents list itself).
    Returns {"all": true} when unrestricted, else {"all": false, "product_ids": [...]}.
    """
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is None:
        return {"all": True, "product_ids": []}

    doc_query = db.query(models.Document.id).filter(
        (models.Document.project_id == None) | (models.Document.project_id.in_(allowed))  # noqa: E711
    )
    doc_ids = [d.id for d in doc_query.all()]

    if not doc_ids:
        return {"all": False, "product_ids": []}

    product_ids = (
        db.query(models.LineItem.product_id)
        .filter(models.LineItem.document_id.in_(doc_ids), models.LineItem.product_id != None)  # noqa: E711
        .distinct()
        .all()
    )
    return {"all": False, "product_ids": [str(p.product_id) for p in product_ids]}


@router.get("", response_model=list[schemas.DocumentOut])
def list_documents(
    doc_type: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    query = db.query(models.Document).filter(models.Document.is_deleted == False)  # noqa: E712
    if doc_type:
        query = query.filter(models.Document.doc_type == doc_type)
    if status:
        query = query.filter(models.Document.status == status)

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        # Documents with no project at all stay visible to everyone —
        # project restrictions only apply to documents actually in a project.
        query = query.filter(
            (models.Document.project_id == None)  # noqa: E711
            | (models.Document.project_id.in_(allowed))
        )

    return query.order_by(models.Document.created_at.desc()).all()


@router.get("/next-number")
def preview_next_doc_number(db: Session = Depends(get_db)):
    """
    Lets the New Document form show the real number it'll get, filled in
    up front, instead of a placeholder hint. Since this doesn't reserve
    the number, it's possible (rare) for it to be taken by the time the
    document is actually submitted if two people open the form at once —
    creation still re-generates and re-checks uniqueness at that point.
    """
    return {"doc_number": _generate_doc_number(db)}


@router.get("/trash", response_model=list[schemas.DocumentOut])
def list_trashed_documents(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """The recycle bin — soft-deleted documents, same project-visibility
    rule as the main list, so you only see what you could delete."""
    query = db.query(models.Document).filter(models.Document.is_deleted == True)  # noqa: E712

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        query = query.filter(
            (models.Document.project_id == None)  # noqa: E711
            | (models.Document.project_id.in_(allowed))
        )

    return query.order_by(models.Document.updated_at.desc()).all()


@router.patch("/{document_id}/trash", response_model=schemas.DocumentOut)
def trash_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Soft delete — hides it from the main list and marks it recoverable.
    For a permanent purge, use DELETE /{document_id} instead (only ever
    called from within the recycle bin in the UI)."""
    _require_edit_access(x_access_level)
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    doc.is_deleted = True
    db.commit()
    db.refresh(doc)
    return doc


@router.patch("/{document_id}/restore", response_model=schemas.DocumentOut)
def restore_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    doc.is_deleted = False
    db.commit()
    db.refresh(doc)
    return doc


@router.get("/{document_id}", response_model=schemas.DocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_username: str | None = Header(default=None, alias="X-Username"),
):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and doc.project_id is not None and str(doc.project_id) not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this document's project")

    log_action(db, doc.id, x_username, "viewed")
    return doc


@router.patch("/{document_id}", response_model=schemas.DocumentOut)
def update_document(
    document_id: uuid.UUID,
    payload: schemas.DocumentUpdate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    x_username: str | None = Header(default=None, alias="X-Username"),
):
    """
    Full edit of an existing document: customer, project, currency, terms,
    and line items (line items are fully replaced, then totals recalculated —
    same validation/product-linking rules as creating a document).
    doc_type and doc_number are intentionally not editable here, since
    doc_number is the document's unique identity.
    """
    _require_edit_access(x_access_level)

    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    if payload.currency not in VALID_CURRENCIES:
        raise HTTPException(status_code=422, detail=f"currency must be one of {sorted(VALID_CURRENCIES)}")

    if payload.customer_id:
        customer = db.query(models.Customer).filter_by(id=payload.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail=f"customer {payload.customer_id} not found")

    if payload.project_id:
        project = db.query(models.Project).filter_by(id=payload.project_id).first()
        if not project:
            raise HTTPException(status_code=400, detail=f"project {payload.project_id} not found")

    line_items, subtotal, tax_total = _process_items(payload.items, db)

    doc.customer_id = payload.customer_id
    doc.project_id = payload.project_id
    doc.currency = payload.currency
    doc.terms_and_conditions = payload.terms_and_conditions
    doc.items = line_items  # SQLAlchemy replaces the collection (old ones deleted via cascade)
    doc.subtotal = subtotal
    doc.tax_total = tax_total
    doc.total = subtotal + tax_total

    db.commit()
    db.refresh(doc)
    index_document(doc)
    log_action(db, doc.id, x_username, "edited")
    return doc


@router.patch("/{document_id}/status", response_model=schemas.DocumentOut)
def update_status(
    document_id: uuid.UUID,
    new_status: str,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    x_username: str | None = Header(default=None, alias="X-Username"),
):
    _require_edit_access(x_access_level)

    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    doc.status = new_status
    db.commit()
    db.refresh(doc)
    log_action(db, doc.id, x_username, "status_changed")
    return doc


@router.post("/{document_id}/file")
def upload_document_file(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    x_username: str | None = Header(default=None, alias="X-Username"),
):
    _require_edit_access(x_access_level)

    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    object_key = f"{doc.doc_type}/{doc.doc_number}/{file.filename}"
    upload_file(file.file, object_key, content_type=file.content_type or "application/octet-stream")

    doc.file_object_key = object_key
    db.commit()
    db.refresh(doc)
    log_action(db, doc.id, x_username, "file_uploaded")
    return {"file_object_key": object_key}


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    db.delete(doc)
    db.commit()
    remove_document_from_index(document_id)


@router.get("/{document_id}/file-url")
def get_document_file_url(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc or not doc.file_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this document")
    return {"url": get_presigned_url(doc.file_object_key)}


@router.get("/{document_id}/audit-log")
def get_audit_log(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_has_audit_log: str | None = Header(default=None, alias="X-Has-Audit-Log"),
):
    """Who viewed/edited this document, and when. Only visible to roles with
    the 'audit-log' service grant (e.g. a 'Director' role) or a superuser."""
    if x_has_audit_log != "true":
        raise HTTPException(status_code=403, detail="you don't have access to the audit log")

    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    entries = (
        db.query(models.AuditLogEntry)
        .filter_by(document_id=document_id)
        .order_by(models.AuditLogEntry.created_at.desc())
        .all()
    )
    return [
        {"username": e.username, "action": e.action, "created_at": e.created_at}
        for e in entries
    ]


def _gather_export_data(document_id: uuid.UUID, db: Session):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    customer = None
    if doc.customer_id:
        customer_row = db.query(models.Customer).filter_by(id=doc.customer_id).first()
        if customer_row:
            customer = {
                "id": customer_row.id,
                "name": customer_row.name,
                "billing_address": customer_row.billing_address,
            }

    items_with_product = []
    for item in doc.items:
        product = None
        if item.product_id:
            try:
                product = get_product(item.product_id)
            except (ProductNotFoundError, CatalogueServiceUnavailableError):
                product = None
        items_with_product.append((item, product))

    company = None
    logo_bytes = None
    company_row = db.query(models.Company).filter_by(is_primary=True).first()
    if company_row:
        company = {
            "name": company_row.name,
            "position": company_row.position,
            "address": company_row.address,
            "contact_no": company_row.contact_no,
            "support_email": company_row.support_email,
            "support_phone": company_row.support_phone,
        }
        if company_row.logo_object_key:
            try:
                logo_bytes = get_file_bytes(company_row.logo_object_key)
            except Exception:
                logo_bytes = None  # don't let a bad logo file block the export

    return doc, customer, items_with_product, company, logo_bytes


@router.get("/{document_id}/export/quotation")
def export_quotation_xlsx(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc, customer, items_with_product, company, logo_bytes = _gather_export_data(document_id, db)
    buffer = generate_quotation_xlsx(doc, customer, items_with_product, company, logo_bytes)
    filename = f"Quotation-{doc.doc_number}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{document_id}/export/quotation-pdf")
def export_quotation_pdf(document_id: uuid.UUID, db: Session = Depends(get_db)):
    doc, customer, items_with_product, company, logo_bytes = _gather_export_data(document_id, db)
    buffer = generate_quotation_pdf(doc, customer, items_with_product, company, logo_bytes)
    filename = f"Quotation-{doc.doc_number}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
