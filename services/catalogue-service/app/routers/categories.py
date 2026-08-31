import io
import uuid

import openpyxl
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.category_utils import generate_short_term
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

    category = models.Category(name=payload.name, short_term=payload.short_term.strip(), description=payload.description)
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="a category with that name or short_term already exists")
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

    old_name = category.name
    if payload.name and payload.name != category.name:
        existing = db.query(models.Category).filter_by(name=payload.name).first()
        if existing:
            raise HTTPException(status_code=409, detail="another category already has that name")
        category.name = payload.name

    if payload.short_term is not None:
        category.short_term = payload.short_term.strip()

    if payload.description is not None:
        category.description = payload.description

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="another category already has that name or short_term")
    db.refresh(category)

    if category.name != old_name:
        # Product.category is a loosely-coupled plain string, not a FK — a
        # rename would otherwise silently strand existing products under
        # the old name, dropping them out of category filtering/grouping.
        db.query(models.Product).filter_by(category=old_name).update(
            {models.Product.category: category.name}
        )
        db.commit()

    return category


@router.post("/sync-from-products")
def sync_categories_from_products(
    db: Session = Depends(get_db),
    x_has_category_access: str | None = Header(default=None, alias="X-Has-Category-Access"),
):
    """
    One-time catch-up: scans every distinct category string already set on
    a product and creates any that don't yet exist in the Categories
    table. New products auto-register their category going forward (see
    _ensure_category_exists in products.py) — this endpoint is for
    products that already existed before that was added (e.g. from a
    bulk import or seed script run before this feature existed).
    """
    if x_has_category_access != "true":
        raise HTTPException(status_code=403, detail="you don't have permission to create categories")

    distinct_categories = [
        row[0] for row in db.query(models.Product.category).distinct().all() if row[0]
    ]
    existing_names = {c.name for c in db.query(models.Category.name).all()}

    created = []
    for name in distinct_categories:
        if name in existing_names:
            continue
        short_term = generate_short_term(db, name)
        category = models.Category(name=name, short_term=short_term)
        db.add(category)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(category)
        created.append({"name": category.name, "short_term": category.short_term})
        existing_names.add(name)

    return {"created": created}
def download_category_bulk_import_template():
    """A blank spreadsheet with the right headers (plus one example row)
    for bulk-importing categories — matches what /bulk-import expects."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Categories"
    ws.append(["Name", "Short Term", "Description"])
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    ws.append(["Computer", "COM", "Desktop and laptop computers"])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 50

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="category-bulk-import-template.xlsx"'},
    )


@router.post("/bulk-import")
def bulk_import_categories(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_has_category_access: str | None = Header(default=None, alias="X-Has-Category-Access"),
):
    """
    Creates many categories at once from a spreadsheet — same shape as the
    template from /bulk-import-template: Name (required), Short Term
    (required — used as the SKU prefix, must be unique), Description
    (optional). Header row is matched by name, case-insensitive, so column
    order doesn't matter.
    """
    if x_has_category_access != "true":
        raise HTTPException(status_code=403, detail="you don't have permission to create categories")

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file.file.read()), data_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="couldn't read that file — make sure it's a valid .xlsx")

    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(status_code=400, detail="that spreadsheet is empty")

    header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]

    def col_index(*names):
        for name in names:
            if name in header:
                return header.index(name)
        return None

    name_idx = col_index("name")
    short_term_idx = col_index("short term", "short_term", "shortterm")
    description_idx = col_index("description")

    if name_idx is None or short_term_idx is None:
        raise HTTPException(status_code=400, detail='the spreadsheet needs "Name" and "Short Term" columns')

    def cell(row, idx):
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    created = []
    warnings = []
    for position, row in enumerate(rows[1:], start=1):
        name = cell(row, name_idx)
        short_term = cell(row, short_term_idx)
        if not name and not short_term:
            continue  # blank row — skipped silently

        if not name or not short_term:
            warnings.append(f"Row {position}: needs both a name and a short term — skipped")
            continue

        name = str(name).strip()
        short_term = str(short_term).strip()
        description = cell(row, description_idx)
        description = str(description).strip() if description else None

        if db.query(models.Category).filter_by(name=name).first():
            warnings.append(f"Row {position}: a category named \"{name}\" already exists — skipped")
            continue
        if db.query(models.Category).filter_by(short_term=short_term).first():
            warnings.append(f"Row {position}: short term \"{short_term}\" is already used by another category — skipped")
            continue

        category = models.Category(name=name, short_term=short_term, description=description)
        db.add(category)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            warnings.append(f"Row {position}: \"{name}\" couldn't be saved (duplicate name or short term) — skipped")
            continue
        db.refresh(category)
        created.append({"name": category.name, "short_term": category.short_term, "row": position})

    return {"created": created, "warnings": warnings}
