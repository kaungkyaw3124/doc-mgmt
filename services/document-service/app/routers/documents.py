import io
import logging
import os
import re
import uuid
import zipfile
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.storage import upload_file, get_presigned_url, get_file_bytes
from app.core.upload_safety import safe_content_type, should_force_download, content_matches_extension
from app.core.catalogue_client import (
    get_product,
    get_product_sub_items,
    get_product_file_bytes,
    get_product_download_bundle_bytes,
    ProductNotFoundError,
    CatalogueServiceUnavailableError,
)
from app.core.search_client import index_document, remove_document_from_index
from app.core.export_quotation import generate_quotation_xlsx
from app.core.export_pdf import generate_quotation_pdf
from app.core.audit import log_action
from app import models, schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

VALID_CURRENCIES = {"USD", "MMK"}
VALID_STATUSES = {"draft", "sent", "paid", "void", "expired"}

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename(filename: str | None) -> str:
    """Strips any directory components (blocks path traversal via the
    object key) and collapses everything else to a safe character set."""
    base = os.path.basename(filename or "") or "file"
    return _SAFE_FILENAME_RE.sub("_", base)


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
    for index, item_in in enumerate(items_payload):
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
                remark=item_in.remark,
                unit=item_in.unit,
                quantity=item_in.quantity,
                unit_price=unit_price,
                tax_rate=item_in.tax_rate,
                line_total=line_total,
                sort_order=index,
            )
        )
    return line_items, subtotal, tax_total


def _generate_doc_number(db: Session, company_id: uuid.UUID | None = None) -> str:
    """
    e.g. SS-20260723/001 — {chosen company's short_name}-{YYYYMMDD}/{3-digit
    sequence for that company+day, starting at 001}. Falls back to "DOC" as
    the prefix if no company was chosen yet, or it has no short_name set.
    """
    company = db.query(models.Company).filter_by(id=company_id).first() if company_id else None
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

    doc_number = payload.doc_number or _generate_doc_number(db, payload.company_id)

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

    if payload.company_id:
        company = db.query(models.Company).filter_by(id=payload.company_id).first()
        if not company:
            raise HTTPException(status_code=400, detail=f"company {payload.company_id} not found")

    if payload.director_id:
        director = db.query(models.CompanyDirector).filter_by(id=payload.director_id).first()
        if not director:
            raise HTTPException(status_code=400, detail=f"director {payload.director_id} not found")
        if director.company_id != payload.company_id:
            raise HTTPException(status_code=400, detail="director does not belong to the selected company")

    doc = models.Document(
        doc_type=payload.doc_type,
        doc_number=doc_number,
        customer_id=payload.customer_id,
        project_id=payload.project_id,
        company_id=payload.company_id,
        director_id=payload.director_id,
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
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="doc_number already exists")
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


@router.get("/by-customer/{customer_id}")
def get_documents_by_customer(
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """
    Which documents belong to this customer, and which project each of
    those documents belongs to — used by the customer detail popup to
    show "used in" context.
    """
    query = db.query(models.Document).filter(models.Document.customer_id == customer_id)

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        query = query.filter(
            (models.Document.project_id == None)  # noqa: E711
            | (models.Document.project_id.in_(allowed))
        )

    docs = query.order_by(models.Document.created_at.desc()).all()
    if not docs:
        return []

    project_ids = {doc.project_id for doc in docs if doc.project_id}
    projects_by_id = {}
    if project_ids:
        for project in db.query(models.Project).filter(models.Project.id.in_(project_ids)).all():
            projects_by_id[project.id] = project.name

    return [
        {
            "document_id": doc.id,
            "doc_number": doc.doc_number,
            "doc_type": doc.doc_type,
            "project_name": projects_by_id.get(doc.project_id),
        }
        for doc in docs
    ]


@router.get("/by-product/{product_id}")
def get_documents_by_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """
    Which documents include this product as a line item, and which
    project each of those documents belongs to — used by the product
    detail popup to show "used in" context.
    """
    line_items = (
        db.query(models.LineItem)
        .filter(models.LineItem.product_id == product_id)
        .all()
    )
    doc_ids = {item.document_id for item in line_items}
    if not doc_ids:
        return []

    doc_query = db.query(models.Document).filter(models.Document.id.in_(doc_ids))
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None:
        doc_query = doc_query.filter(
            (models.Document.project_id == None)  # noqa: E711
            | (models.Document.project_id.in_(allowed))
        )
    docs = doc_query.all()
    project_ids = {doc.project_id for doc in docs if doc.project_id}
    projects_by_id = {}
    if project_ids:
        for project in db.query(models.Project).filter(models.Project.id.in_(project_ids)).all():
            projects_by_id[project.id] = project.name

    return [
        {
            "document_id": doc.id,
            "doc_number": doc.doc_number,
            "doc_type": doc.doc_type,
            "project_name": projects_by_id.get(doc.project_id),
        }
        for doc in sorted(docs, key=lambda d: d.created_at, reverse=True)
    ]


@router.get("/next-number")
def preview_next_doc_number(company_id: uuid.UUID | None = None, db: Session = Depends(get_db)):
    """
    Lets the New Document form show the real number it'll get, filled in
    up front, instead of a placeholder hint. Pass company_id to preview the
    number for that specific company's prefix (re-called whenever the
    Company dropdown changes) — falls back to a generic "DOC" prefix if
    none is chosen yet. Since this doesn't reserve the number, it's
    possible (rare) for it to be taken by the time the document is
    actually submitted if two people open the form at once — creation
    still re-generates and re-checks uniqueness at that point.
    """
    return {"doc_number": _generate_doc_number(db, company_id)}


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
    Full edit of an existing document: customer, project, company, currency,
    terms, and line items (line items are fully replaced, then totals
    recalculated — same validation/product-linking rules as creating a
    document). doc_type and doc_number are intentionally not editable here,
    since doc_number is the document's unique identity.
    """
    _require_edit_access(x_access_level)

    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")

    if payload.currency is not None and payload.currency not in VALID_CURRENCIES:
        raise HTTPException(status_code=422, detail=f"currency must be one of {sorted(VALID_CURRENCIES)}")

    if payload.customer_id:
        customer = db.query(models.Customer).filter_by(id=payload.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail=f"customer {payload.customer_id} not found")

    if payload.project_id:
        project = db.query(models.Project).filter_by(id=payload.project_id).first()
        if not project:
            raise HTTPException(status_code=400, detail=f"project {payload.project_id} not found")

    if payload.company_id:
        company = db.query(models.Company).filter_by(id=payload.company_id).first()
        if not company:
            raise HTTPException(status_code=400, detail=f"company {payload.company_id} not found")

    if payload.director_id:
        director = db.query(models.CompanyDirector).filter_by(id=payload.director_id).first()
        if not director:
            raise HTTPException(status_code=400, detail=f"director {payload.director_id} not found")
        if director.company_id != payload.company_id:
            raise HTTPException(status_code=400, detail="director does not belong to the selected company")

    line_items, subtotal, tax_total = _process_items(payload.items, db)

    doc.customer_id = payload.customer_id
    doc.project_id = payload.project_id
    doc.company_id = payload.company_id
    doc.director_id = payload.director_id
    if payload.currency is not None:
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

    if new_status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(VALID_STATUSES)}")

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

    sanitized_filename = _sanitize_filename(file.filename)

    # Read only the leading bytes to check the file's actual content
    # against what its extension claims — then rewind before streaming
    # the full upload to storage. See app/core/upload_safety.py: this is
    # a distinct check from Content-Type derivation below — it rejects
    # the upload outright rather than just relabeling it.
    head = file.file.read(4096)
    file.file.seek(0)
    if not content_matches_extension(sanitized_filename, head):
        raise HTTPException(
            status_code=400,
            detail="file content does not match its extension",
        )

    object_key = f"{doc.doc_type}/{doc.doc_number}/{sanitized_filename}"
    # Content-Type is derived from the filename server-side, NEVER trusted
    # from the client's upload — see app/core/upload_safety.py.
    upload_file(file.file, object_key, content_type=safe_content_type(sanitized_filename))

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
def get_document_file_url(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc or not doc.file_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this document")

    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and doc.project_id is not None and str(doc.project_id) not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this document's project")

    return {"url": get_presigned_url(doc.file_object_key, force_download=should_force_download(doc.file_object_key))}


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


_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}


def _guess_image_mime(object_key: str | None) -> str:
    """Guesses the MIME type from the uploaded file's extension — used to
    build a correct data URI. Defaults to png if we can't tell, which was
    the previous (buggy) hardcoded behavior for every format."""
    if not object_key or "." not in object_key.rsplit("/", 1)[-1]:
        return "image/png"
    ext = object_key.rsplit(".", 1)[-1].lower()
    return _IMAGE_MIME_TYPES.get(ext, "image/png")


def _check_project_access(doc, x_allowed_projects: str | None):
    allowed = _parse_allowed_projects(x_allowed_projects)
    if allowed is not None and doc.project_id is not None and str(doc.project_id) not in allowed:
        raise HTTPException(status_code=403, detail="you don't have access to this document's project")


def _gather_export_data(document_id: uuid.UUID, db: Session, x_allowed_projects: str | None = None):
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    _check_project_access(doc, x_allowed_projects)

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
        sub_items = []
        if item.product_id:
            try:
                product = get_product(item.product_id)
            except (ProductNotFoundError, CatalogueServiceUnavailableError):
                product = None
            sub_items = get_product_sub_items(item.product_id)
        items_with_product.append((item, product, sub_items))

    company = None
    logo_bytes = None
    logo_mime = "image/png"
    seal_bytes = None
    seal_mime = "image/png"
    company_row = db.query(models.Company).filter_by(id=doc.company_id).first() if doc.company_id else None
    if company_row:
        company = {
            "name": company_row.name,
        }
        if company_row.logo_object_key:
            try:
                logo_bytes = get_file_bytes(company_row.logo_object_key)
                logo_mime = _guess_image_mime(company_row.logo_object_key)
            except Exception:
                logo_bytes = None  # don't let a bad logo file block the export
        if company_row.seal_object_key:
            try:
                seal_bytes = get_file_bytes(company_row.seal_object_key)
                seal_mime = _guess_image_mime(company_row.seal_object_key)
            except Exception:
                seal_bytes = None  # don't let a bad seal file block the export

    director = None
    director_seal_bytes = None
    director_seal_mime = "image/png"
    if doc.director_id:
        director_row = db.query(models.CompanyDirector).filter_by(id=doc.director_id).first()
        if director_row:
            director = {
                "name": director_row.name,
                "address": director_row.address,
                "contact_no": director_row.contact_no,
                "email": director_row.email,
            }
            if director_row.seal_object_key:
                try:
                    director_seal_bytes = get_file_bytes(director_row.seal_object_key)
                    director_seal_mime = _guess_image_mime(director_row.seal_object_key)
                except Exception:
                    director_seal_bytes = None  # don't let a bad seal file block the export

    return doc, customer, items_with_product, company, logo_bytes, seal_bytes, logo_mime, seal_mime, director, director_seal_bytes, director_seal_mime


@router.get("/{document_id}/export/quotation")
def export_quotation_xlsx(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    doc, customer, items_with_product, company, logo_bytes, seal_bytes, logo_mime, seal_mime, director, director_seal_bytes, director_seal_mime = _gather_export_data(document_id, db, x_allowed_projects)
    buffer = generate_quotation_xlsx(doc, customer, items_with_product, company, logo_bytes, seal_bytes, logo_mime, seal_mime, director, director_seal_bytes, director_seal_mime)
    filename = f"Quotation-{doc.doc_number}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{document_id}/export/quotation-pdf")
def export_quotation_pdf(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    doc, customer, items_with_product, company, logo_bytes, seal_bytes, logo_mime, seal_mime, director, director_seal_bytes, director_seal_mime = _gather_export_data(document_id, db, x_allowed_projects)
    buffer = generate_quotation_pdf(doc, customer, items_with_product, company, logo_bytes, seal_bytes, logo_mime, seal_mime, director, director_seal_bytes, director_seal_mime)
    filename = f"Quotation-{doc.doc_number}.pdf"
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{document_id}/export/catalogue")
def export_document_catalogue(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
):
    """
    Bundles the catalogue files of every product in this document into one
    zip. Products are numbered by their position among the document's
    product-based line items (1, 2, 3… — free-text line items with no
    product are skipped, since there's no file to include for them).

    A product with no sub-items contributes a single top-level file named
    by its position (e.g. "2.pdf"). A product WITH sub-items instead gets
    its own folder named by that position (e.g. "3/"), containing each
    sub-item's file renamed "{position}.{sub-item's own sequence}"
    (e.g. "3/3.1.pdf", "3/3.2.png") — reusing the same zip that product's
    own "View file" button would produce, just renamed into this shape.
    """
    doc = db.query(models.Document).filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    _check_project_access(doc, x_allowed_projects)

    product_items = [item for item in doc.items if item.product_id]
    if not product_items:
        raise HTTPException(status_code=404, detail="this document has no product-based line items to bundle")

    zip_buffer = io.BytesIO()
    added_any = False
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for position, item in enumerate(product_items, start=1):
            sub_items = get_product_sub_items(item.product_id)

            if sub_items:
                bundle_bytes = get_product_download_bundle_bytes(item.product_id)
                if not bundle_bytes:
                    logger.warning(
                        "catalogue export: product %s (position %d) has sub-items but its bundle came back empty — skipping",
                        item.product_id, position,
                    )
                    continue
                try:
                    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as inner_zip:
                        for inner_name in inner_zip.namelist():
                            data = inner_zip.read(inner_name)
                            zf.writestr(f"{position}/{position}.{inner_name}", data)
                            added_any = True
                except zipfile.BadZipFile:
                    logger.warning(
                        "catalogue export: product %s (position %d) returned a bad zip for its sub-items bundle — skipping",
                        item.product_id, position,
                    )
                    continue
            else:
                file_bytes = get_product_file_bytes(item.product_id)
                if not file_bytes:
                    logger.warning(
                        "catalogue export: product %s (position %d) has no sub-items and no file to bundle — skipping",
                        item.product_id, position,
                    )
                    continue
                try:
                    product = get_product(item.product_id)
                except (ProductNotFoundError, CatalogueServiceUnavailableError) as exc:
                    logger.warning(
                        "catalogue export: couldn't look up product %s (position %d) for its file extension — using none (%s)",
                        item.product_id, position, exc,
                    )
                    product = None
                ext = ""
                key = (product or {}).get("image_object_key") or ""
                if "." in key.rsplit("/", 1)[-1]:
                    ext = "." + key.rsplit(".", 1)[-1]
                zf.writestr(f"{position}{ext}", file_bytes)
                added_any = True

    if not added_any:
        logger.warning(
            "catalogue export: nothing was bundled for document %s (doc_number=%s) — %d product-based line item(s) checked",
            document_id, doc.doc_number, len(product_items),
        )
        raise HTTPException(status_code=404, detail="none of this document's products have catalogue files")

    filename = f"{doc.doc_number}-catalogue.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
