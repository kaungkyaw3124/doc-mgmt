import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.concurrency import check_version, claim_idempotency_key, next_number, remember
from app.core.group_scope import ALL, Scope, get_scope, scoped, get_or_404, group_for_create, require_superuser
from app.core.search_client import index_product, remove_product_from_index, rebuild_index
from app.core.storage import upload_file, delete_file, get_presigned_url, download_file_bytes
from app.core.document_client import get_visible_product_ids
from app.core import duplicates as dup
from app.core.upload_safety import safe_content_type, should_force_download, content_matches_extension
from app import models, schemas

router = APIRouter(prefix="/products", tags=["products"])
logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _require_edit_access(x_access_level: str | None):
    """Mirrors document-service/app/routers/companies.py's helper of the
    same name — missing header defaults to "edit" (backward-compat,
    restrictions are opt-in). A Viewer role's RoleAccess grant on
    "products" sets X-Access-Level: view via Nginx's auth_request (see
    infra/nginx/nginx.conf.template's /api/products location); this was
    previously never checked anywhere in this router, so a Viewer could
    write products despite the role model saying they shouldn't be able
    to — see docs/SECURITY_HARDENING_LOG.md's User Control entry."""
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="your role has view-only access to products")


def _sanitize_filename(filename: str | None) -> str:
    """Strips directory components (blocks path traversal via the object
    key) and collapses everything else to a safe character set — mirrors
    document-service/app/routers/companies.py's helper of the same name."""
    base = os.path.basename(filename or "") or "file"
    return _SAFE_FILENAME_RE.sub("_", base)


def _sub_item_counts_map(db: Session, product_ids: list) -> dict:
    """One query for however many products, instead of one query per product."""
    if not product_ids:
        return {}
    rows = (
        db.query(models.ProductSubItem.parent_product_id, func.count(models.ProductSubItem.id))
        .filter(models.ProductSubItem.parent_product_id.in_(product_ids))
        .group_by(models.ProductSubItem.parent_product_id)
        .all()
    )
    return {str(pid): count for pid, count in rows}


def _attach_sub_item_counts(db: Session, products: list):
    counts = _sub_item_counts_map(db, [p.id for p in products])
    for p in products:
        p.sub_item_count = counts.get(str(p.id), 0)
    return products


def _attach_sub_item_count(db: Session, product):
    product.sub_item_count = (
        db.query(models.ProductSubItem).filter_by(parent_product_id=product.id).count()
    )
    return product


def _extract_zip_entries(file_bytes: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="that catalogue file isn't a valid zip")
    entries = []
    for entry_name in zf.namelist():
        if entry_name.endswith("/"):
            continue  # a directory entry, not a file
        entries.append((entry_name.rsplit("/", 1)[-1], zf.read(entry_name)))
    return entries


def _extract_rar_entries(file_bytes: bytes):
    """
    Shells out to `unar` (installed in the container image) rather than
    relying on a Python RAR library — there's no free, pure-Python RAR
    decoder, and `unar` is a well-supported, actually-free command-line
    tool that handles it reliably.
    """
    tmp_dir = tempfile.mkdtemp(prefix="rar_import_")
    try:
        rar_path = os.path.join(tmp_dir, "archive.rar")
        with open(rar_path, "wb") as f:
            f.write(file_bytes)

        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        result = subprocess.run(
            ["unar", "-quiet", "-no-directory-confirmation", "-force-overwrite", "-output-directory", extract_dir, rar_path],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail="couldn't read that rar file — it may be corrupted, password-protected, or in an unsupported format",
            )

        entries = []
        for root, _dirs, files in os.walk(extract_dir):
            for fname in files:
                with open(os.path.join(root, fname), "rb") as f:
                    entries.append((fname, f.read()))
        return entries
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=400, detail="that rar file took too long to extract")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _extract_archive_entries(filename: str | None, file_bytes: bytes):
    """Returns [(basename, bytes), ...] from either a .zip or .rar archive,
    picked by the uploaded file's extension. Falls back to zip if the
    extension is missing or unrecognized — matches this endpoint's
    original behavior before .rar support was added."""
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if ext == "rar":
        return _extract_rar_entries(file_bytes)
    return _extract_zip_entries(file_bytes)


def _raise_if_exact_duplicate(db: Session, name, description, x_allowed_projects: str | None, group_id, exclude_id=None):
    """409 if another active product of the same group has the same
    normalized name AND description (each department keeps its own
    catalogue). The existing product's details are only included when the
    caller is allowed to see it (project rule)."""
    existing = dup.find_exact(db, name, description, group_id, exclude_id)
    if existing is None:
        return
    visible_ids = get_visible_product_ids(x_allowed_projects)
    shown = visible_ids is None or str(existing.id) in visible_ids
    raise HTTPException(status_code=409, detail={
        "message": "This item already exists",
        "existing": {"id": str(existing.id), "sku": existing.sku, "name": existing.name} if shown else None,
    })


def _sku_prefix(db: Session, category_name: str | None) -> str:
    """
    e.g. "COM" for a product in the "Computer" category — that category's
    manually-set short_term (not derived from the category name itself).
    Falls back to "PRD" if no category was given, or the category has no
    short_term set.
    """
    if category_name:
        category = db.query(models.Category).filter_by(name=category_name).first()
        if category and category.short_term:
            return category.short_term.upper()
    return "PRD"


def _max_sku_number(db: Session, prefix: str) -> int:
    """Highest N in "<prefix>-N" already used — seeds the prefix's counter.
    sku is unique across ALL groups, so this looks at every product."""
    rows = scoped(db, models.Product, ALL).with_entities(models.Product.sku).filter(models.Product.sku.like(f"{prefix}-%"))
    return max((int(m.group(1)) for (sku,) in rows if (m := re.fullmatch(rf"{re.escape(prefix)}-(\d+)", sku))), default=0)


def _add_with_next_sku(db: Session, product, category_name: str | None) -> None:
    """
    Gives `product` the next SKU of its category (e.g. COM-0001), from the
    locked per-prefix counter — two simultaneous creates can never get the
    same one, and a rolled-back create gives its number back — then adds and
    flushes it. A clash with a hand-typed SKU is retried once with the
    counter moved past the highest SKU in use (a savepoint keeps the rest of
    the transaction, e.g. the held idempotency key).
    """
    prefix = _sku_prefix(db, category_name)
    for attempt in range(2):
        product.sku = f"{prefix}-{next_number(db, f'sku:{prefix}', lambda: _max_sku_number(db, prefix), reseed=attempt > 0):04d}"
        try:
            with db.begin_nested():  # add inside: begin_nested() flushes pending objects before its SAVEPOINT
                db.add(product)
                db.flush()
            return
        except IntegrityError:
            pass
    raise HTTPException(status_code=409, detail="sku already exists")


@router.post("", response_model=schemas.ProductOut, status_code=201)
def create_product(
    payload: schemas.ProductCreate,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    group_id = group_for_create(scope, payload.group_id)
    # First: a repeat must get the first answer, not "duplicate"/"sku exists".
    held, replay_id = claim_idempotency_key(db, idempotency_key, "POST /products")
    if replay_id:
        return _attach_sub_item_count(db, get_or_404(db, models.Product, replay_id, scope, "product not found"))
    _raise_if_exact_duplicate(db, payload.name, payload.description, x_allowed_projects, group_id)
    if payload.sku and scoped(db, models.Product, ALL).filter_by(sku=payload.sku).first():  # unique across all groups
        raise HTTPException(status_code=409, detail="sku already exists")
    product = models.Product(**{**payload.model_dump(), "id": uuid.uuid4(), "group_id": group_id})
    remember(held, product.id)
    if payload.sku:
        db.add(product)
    else:
        _add_with_next_sku(db, product, payload.category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="sku already exists")
    db.refresh(product)
    index_product(product)  # after the commit; a failure is logged and fixed by scripts/reindex.sh
    return _attach_sub_item_count(db, product)


@router.get("", response_model=list[schemas.ProductOut])
def list_products(
    category: str | None = None,
    q: str | None = None,   # simple name search, e.g. ?q=widget
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    query = scoped(db, models.Product, scope).filter(models.Product.is_deleted == False)  # noqa: E712
    if category:
        query = query.filter(models.Product.category == category)
    if q:
        query = query.filter(models.Product.name.ilike(f"%{q}%"))

    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None:
        query = query.filter(models.Product.id.in_(visible_ids))

    return _attach_sub_item_counts(db, query.order_by(models.Product.created_at.desc()).all())


@router.get("/duplicate-check")
def duplicate_check(
    name: str = "",
    description: str | None = None,
    exclude_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """Exact / same-name / similar active products for a name+description
    about to be saved (the product form calls this before submitting)."""
    products = dup.active_products(db, scope, exclude_id, get_visible_product_ids(x_allowed_projects))
    return {kind: [dup.brief(p) for p in matches] for kind, matches in dup.check(products, name, description).items()}


@router.get("/duplicates")
def list_duplicates(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """Groups of possible duplicates across the active catalogue, for the
    "Possible duplicates" review page."""
    products = dup.active_products(db, scope, visible_ids=get_visible_product_ids(x_allowed_projects))
    result = dup.groups(products, dup.effective_dismissals(db, scope, products).keys())
    return {"exact_groups": [[dup.brief(p) for p in g] for g in result["exact_groups"]],
            "same_name_groups": [[dup.brief(p) for p in g] for g in result["same_name_groups"]],
            "hidden_count": result["hidden_count"]}


@router.post("/duplicates/dismiss")
def dismiss_duplicates(
    payload: schemas.DismissDuplicates,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    x_username: str | None = Header(default=None, alias="X-Username"),
    scope: Scope = Depends(get_scope),
):
    """"Not a duplicate": every different-description pair of these
    same-name products stops showing on the duplicates page until one of
    them is edited. Exact duplicates are never dismissed. Idempotent: a
    repeat refreshes the stored fingerprints."""
    _require_edit_access(x_access_level)
    ids = set(payload.product_ids)
    found = [p for p in dup.active_products(db, scope, visible_ids=get_visible_product_ids(x_allowed_projects)) if p.id in ids]
    if len(found) != len(ids):
        raise HTTPException(status_code=404, detail="product not found")
    if len({p.group_id for p in found}) > 1:
        raise HTTPException(status_code=422, detail="all products must belong to the same group")
    if len({dup.norm(p.name) for p in found}) > 1:
        raise HTTPException(status_code=422, detail="only products with the same name can be marked as not duplicates")
    pairs = [dup.pair(a, b) for a, b in combinations(found, 2) if dup.norm(a.description) != dup.norm(b.description)]
    if not pairs:
        raise HTTPException(status_code=422, detail="exact duplicates (same name and description) cannot be marked as not duplicates")
    existing = {(d.product_a_id, d.product_b_id): d for d in db.query(models.ProductDuplicateDismissal).filter(
        models.ProductDuplicateDismissal.product_a_id.in_(ids), models.ProductDuplicateDismissal.product_b_id.in_(ids))}
    for a, b in pairs:
        row = existing.get((a.id, b.id)) or models.ProductDuplicateDismissal(id=uuid.uuid4(), product_a_id=a.id, product_b_id=b.id)
        row.group_id, row.fingerprint_a, row.fingerprint_b = a.group_id, dup.fingerprint(a), dup.fingerprint(b)
        row.created_by, row.created_at = x_username or None, datetime.utcnow()
        db.add(row)
    try:
        db.commit()
    except IntegrityError:  # two simultaneous clicks on the same group: the other one already saved it
        db.rollback()
        raise HTTPException(status_code=409, detail="already being marked — refresh the page")
    return {"dismissed_pairs": len(pairs)}


@router.get("/duplicates/dismissed")
def list_dismissed_duplicates(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """The pairs currently hidden by "Not a duplicate" (stale ones — a product edited since — are left out)."""
    products = dup.active_products(db, scope, visible_ids=get_visible_product_ids(x_allowed_projects))
    by_id = {p.id: p for p in products}
    rows = sorted(dup.effective_dismissals(db, scope, products).values(), key=lambda d: (dup.norm(by_id[d.product_a_id].name), str(d.id)))
    return [{"id": str(d.id), "created_by": d.created_by, "created_at": d.created_at,
             "products": [dup.brief(by_id[d.product_a_id]), dup.brief(by_id[d.product_b_id])]} for d in rows]


@router.delete("/duplicates/dismissed/{dismissal_id}", status_code=204)
def restore_dismissed_duplicate(
    dismissal_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """Undo "Not a duplicate" for one pair: it can show on the duplicates page again."""
    _require_edit_access(x_access_level)
    db.delete(get_or_404(db, models.ProductDuplicateDismissal, dismissal_id, scope, "dismissal not found"))
    db.commit()


@router.get("/bulk-import-template")
def download_product_bulk_import_template():
    """A blank spreadsheet with the right headers (plus one example row)
    for bulk-importing products — matches what /bulk-import expects."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(["Name", "Category", "Price", "Description"])
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
    ws.append(["Dell Vostro 15", "Computer", 950.00, "15-inch business laptop, Intel i5, 8GB RAM, 512GB SSD"])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 55

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="product-bulk-import-template.xlsx"'},
    )


@router.post("/bulk-import")
def bulk_import_products(
    excel_file: UploadFile = File(...),
    catalogue_zip: UploadFile | None = File(None),
    group_id: uuid.UUID | None = Form(None),
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """
    Creates many products at once from a spreadsheet, optionally matched
    up with catalogue files from a zip.

    Excel file: first row is headers (case-insensitive) — needs a "Name"
    column; "Category", "Price", and "Description" are optional. Item
    Number/SKU is always auto-generated from the category, same as
    creating one product at a time.

    Catalogue archive (optional, .zip or .rar): files named "1.ext",
    "2.ext", "3.ext"…
    (any extension, subfolders ignored) — matched by POSITION to the
    spreadsheet's data rows: file "1" attaches to the first product row,
    "2" to the second, and so on. A blank/skipped row still "uses up"
    its number, so numbering always matches the row's position in the
    sheet, not the count of products actually created.

    Duplicates: a row that is an exact duplicate (same normalized name and
    description) of an active product, or of an earlier row in this file,
    is skipped and listed in `skipped`; it still uses up its number like a
    blank row. A row that only shares a name with a product is created, with
    a {row, name, reason} entry in `warnings` (next to the existing
    plain-text warnings).
    """
    _require_edit_access(x_access_level)
    group_id = group_for_create(scope, group_id)  # every created row belongs to the importer's group
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(excel_file.file.read()), data_only=True)
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
    category_idx = col_index("category")
    price_idx = col_index("price", "unit_price", "unit price")
    description_idx = col_index("description")

    if name_idx is None:
        raise HTTPException(status_code=400, detail='the spreadsheet needs a "Name" column')

    def cell(row, idx):
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    # Pull every "N.ext" file out of the archive, keyed by its position number.
    files_by_position = {}
    if catalogue_zip is not None:
        entries = _extract_archive_entries(catalogue_zip.filename, catalogue_zip.file.read())
        for basename, data in entries:
            stem = basename.rsplit(".", 1)[0] if "." in basename else basename
            if stem.isdigit():
                files_by_position[int(stem)] = (basename, data)

    # Normalized (name, description) -> existing sku, and normalized name ->
    # sku, both kept up to date as rows are created, so later rows in the
    # same file are checked against earlier ones too.
    existing = dup.active_products(db, ALL, group_id=group_id)  # duplicates are per group
    sku_by_key = {dup.key(p.name, p.description): p.sku for p in existing}
    sku_by_name = {dup.norm(p.name): p.sku for p in existing}
    row_by_key = {}

    created = []
    skipped = []
    name_warnings = []
    for position, row in enumerate(rows[1:], start=1):
        name = cell(row, name_idx)
        if not name:
            continue  # blank row — skipped, but still "uses up" this position number
        name = str(name).strip()

        category = cell(row, category_idx)
        category = str(category).strip() if category else None

        price = None
        raw_price = cell(row, price_idx)
        if raw_price is not None:
            try:
                price = Decimal(str(raw_price))
            except InvalidOperation:
                price = None

        description = cell(row, description_idx)
        description = str(description).strip() if description else None

        row_key = dup.key(name, description)
        if row_key in sku_by_key:
            reason = f"duplicate of row {row_by_key[row_key]}" if row_key in row_by_key else "already exists"
            skipped.append({"row": position, "name": name, "reason": reason, "existing_sku": sku_by_key[row_key]})
            continue  # still "uses up" this position number, like a blank row
        if row_key[0] in sku_by_name:
            name_warnings.append({"row": position, "name": name, "reason": f"same name as {sku_by_name[row_key[0]]}"})

        product = models.Product(name=name, category=category, unit_price=price, description=description, group_id=group_id)
        _add_with_next_sku(db, product, category)
        sku = product.sku
        entry = {"sku": sku, "name": name, "row": position, "has_file": False}

        # All or nothing per row: the file goes up before the commit, a failed
        # upload drops the row (and gives its SKU back), and a failed commit
        # removes the uploaded object again — no row without its file, no
        # orphan file without a row.
        object_key = None
        if position in files_by_position:
            filename, file_bytes = files_by_position.pop(position)
            object_key = f"{sku}/{filename}"
            try:
                upload_file(io.BytesIO(file_bytes), object_key, content_type="application/octet-stream")
            except Exception:
                logger.exception("bulk import: catalogue file upload failed for row %s", position)
                db.rollback()
                skipped.append({"row": position, "name": name, "reason": "catalogue file upload failed — row not created", "existing_sku": ""})
                continue
            product.image_object_key = object_key
            entry["has_file"] = True
        try:
            db.commit()
        except Exception:
            db.rollback()
            if object_key:
                delete_file(object_key)
            raise
        db.refresh(product)
        index_product(product)

        sku_by_key[row_key] = sku
        sku_by_name.setdefault(row_key[0], sku)
        row_by_key[row_key] = position

        created.append(entry)

    warnings = []
    if files_by_position:
        leftover = ", ".join(str(k) for k in sorted(files_by_position.keys()))
        warnings.append(f"{len(files_by_position)} catalogue file(s) didn't match any spreadsheet row (numbers: {leftover})")

    return {"created": created, "skipped": skipped, "warnings": warnings + name_warnings}


@router.post("/assign-unassigned")
def assign_unassigned(payload: schemas.AssignUnassigned, db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    """Superuser only: gives every product with no group yet (group_id NULL —
    data from before groups existed) to one group, then rebuilds the products
    search index. Safe to run again: the second run finds nothing."""
    require_superuser(scope)
    if payload.group_id not in scope.group_ids:
        raise HTTPException(status_code=422, detail="That group does not exist or is disabled.")
    count = scoped(db, models.Product, scope).filter(models.Product.group_id == None).update(  # noqa: E711
        {models.Product.group_id: payload.group_id}, synchronize_session=False
    )
    db.commit()
    return {"products": count, "reindexed": rebuild_index(scoped(db, models.Product, scope).all())}


# ---------- moving products to another group (superuser, loop iteration 20) ----------
# A bundle and its sub-items must stay in one group (add_sub_item refuses a
# cross-group link), so a move works on the connected set: the selected
# products plus every bundle/sub-item linked to them, transitively. Products
# already in the target group are not followed (their links are there too).
# with_links=False moves only the selection and refuses (409) when that would
# leave a cross-group link. Documents keep their snapshot lines either way.
audit_logger = logging.getLogger("app.audit")
audit_logger.setLevel(logging.INFO)  # root is WARN (alembic.ini); its console handler still prints these


def _move_products(ids, payload: schemas.MoveToGroup, db: Session, scope: Scope, username: str | None):
    require_superuser(scope)
    if payload.group_id not in scope.group_ids:  # a superuser's scope = every ACTIVE group
        raise HTTPException(status_code=422, detail="That group does not exist or is disabled.")
    P, S, target = models.Product, models.ProductSubItem, payload.group_id
    ids = set(ids)
    rows = {p.id: p for p in scoped(db, P, ALL).filter(P.id.in_(ids)).all()}
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="product not found")
    while True:
        follow = [i for i, p in rows.items() if p.group_id != target]
        links = db.query(S.parent_product_id, S.sub_product_id).filter(
            S.parent_product_id.in_(follow) | S.sub_product_id.in_(follow)).all() if follow else []
        new_ids = {i for link in links for i in link} - rows.keys()
        if not new_ids:
            break
        rows.update({p.id: p for p in scoped(db, P, ALL).filter(P.id.in_(new_ids)).all()})
    extra = sum(1 for i, p in rows.items() if i not in ids and p.group_id != target)
    if extra and not payload.with_links:
        raise HTTPException(
            status_code=409,
            detail=f"Not moved: it is linked (bundle/sub-item) to {extra} product{'s' if extra > 1 else ''} "
                   "in another group. Tick “also move linked products” to move them together.",
        )
    to_move = [p for i, p in rows.items() if p.group_id != target and (payload.with_links or i in ids)]
    if not payload.dry_run:
        moved = [(p.id, p.group_id) for p in to_move]
        for p in to_move:
            p.group_id = target
        db.commit()
        for row_id, old in moved:
            audit_logger.info("group_move user=%s product=%s from=%s to=%s", username or "unknown", row_id, old, target)
        for p in to_move:
            index_product(p)  # the old group stops finding it in search, the new one does at once
    return {"moved": {"products": len(to_move)}, "dry_run": payload.dry_run}


@router.post("/move")
def bulk_move_products(payload: schemas.BulkMoveToGroup, db: Session = Depends(get_db), scope: Scope = Depends(get_scope),
                       x_username: str | None = Header(default=None, alias="X-Username")):
    return _move_products(payload.ids, payload, db, scope, x_username)


@router.post("/{product_id}/move")
def move_product(product_id: uuid.UUID, payload: schemas.MoveToGroup, db: Session = Depends(get_db),
                 scope: Scope = Depends(get_scope), x_username: str | None = Header(default=None, alias="X-Username")):
    return _move_products([product_id], payload, db, scope, x_username)


@router.get("/unassigned-counts")
def unassigned_product_counts(db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    """Admin → Groups "Unassigned data" card (recycle bin included, like assign-unassigned)."""
    require_superuser(scope)
    return {"products": scoped(db, models.Product, ALL).filter(models.Product.group_id == None).count()}  # noqa: E711


# DEV-ONLY: delete-all BEGIN — temporary tool for clearing the
# catalogue between test runs (loop iteration 15). Remove this whole block
# plus the matching DEV-ONLY blocks in web/index.html and web/js/app.js.
@router.get("/dev-config")
def dev_config():
    """Tells the UI whether to show the "Delete ALL products (dev)" button.
    Kept here, not in a new router, so the feature removes in one cut."""
    from app.core.config import settings
    return {"delete_all_enabled": settings.environment != "production"}


@router.delete("")
def delete_all_products(
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """Permanently deletes every product of the caller's groups (recycle bin
    included; a superuser: all) — no project-visibility filter, on purpose:
    the point is an empty catalogue. Refused in production so it can never
    wipe the live VPS. Categories stay."""
    import logging
    from app.core.config import settings
    from app.core.storage import s3_client

    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="delete-all is disabled in production")
    _require_edit_access(x_access_level)

    products = scoped(db, models.Product, scope).with_entities(models.Product.id, models.Product.image_object_key).all()
    ids = [product_id for product_id, _ in products]
    if ids:
        # Sub-item links first: sub_product_id has no ON DELETE CASCADE, so a
        # product used as someone's component would otherwise fail the FK.
        db.query(models.ProductSubItem).filter(
            models.ProductSubItem.parent_product_id.in_(ids) | models.ProductSubItem.sub_product_id.in_(ids)
        ).delete(synchronize_session=False)
        scoped(db, models.Product, scope).filter(models.Product.id.in_(ids)).delete(synchronize_session=False)
    db.commit()

    for product_id, object_key in products:
        remove_product_from_index(product_id)  # logs, never raises
        if object_key:
            try:
                s3_client.delete_object(Bucket=settings.minio_bucket, Key=object_key)
            except Exception:  # a missing/unreachable file must not fail the wipe — the rows are already gone
                logging.getLogger(__name__).exception("could not delete %s from storage", object_key)
    return {"deleted": len(products)}
# DEV-ONLY: delete-all END


@router.get("/trash", response_model=list[schemas.ProductOut])
def list_trashed_products(
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """The recycle bin — soft-deleted products, same project-visibility
    rule as the main list."""
    query = scoped(db, models.Product, scope).filter(models.Product.is_deleted == True)  # noqa: E712

    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None:
        query = query.filter(models.Product.id.in_(visible_ids))

    return _attach_sub_item_counts(db, query.order_by(models.Product.updated_at.desc()).all())


def _check_product_visible(product, x_allowed_projects: str | None):
    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None and str(product.id) not in visible_ids:
        raise HTTPException(status_code=403, detail="you don't have access to this product")


@router.patch("/{product_id}/trash", response_model=schemas.ProductOut)
def trash_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    """Soft delete — hides it from the catalogue and main list, but keeps
    it recoverable via the recycle bin. For a permanent purge, use
    DELETE /{product_id} instead (only called from within the recycle bin)."""
    _require_edit_access(x_access_level)
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)
    product.is_deleted = True
    db.commit()
    db.refresh(product)
    return _attach_sub_item_count(db, product)


@router.patch("/{product_id}/restore", response_model=schemas.ProductOut)
def restore_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)
    _raise_if_exact_duplicate(db, product.name, product.description, x_allowed_projects, product.group_id, exclude_id=product.id)
    product.is_deleted = False
    db.commit()
    db.refresh(product)
    return _attach_sub_item_count(db, product)


@router.get("/{product_id}", response_model=schemas.ProductOut)
def get_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    product = get_or_404(db, models.Product, product_id, scope, "product not found")

    visible_ids = get_visible_product_ids(x_allowed_projects)
    if visible_ids is not None and str(product.id) not in visible_ids:
        raise HTTPException(status_code=403, detail="you don't have access to this product")

    return _attach_sub_item_count(db, product)


@router.patch("/{product_id}", response_model=schemas.ProductOut)
def update_product(
    product_id: uuid.UUID,
    payload: schemas.ProductUpdate,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)
    check_version(product, payload.version)

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    new_name = changes.get("name", product.name)
    new_description = changes.get("description", product.description)
    # Only when the text changes: products that were already duplicates
    # must still accept price/category/file edits.
    if (new_name, new_description) != (product.name, product.description):
        _raise_if_exact_duplicate(db, new_name, new_description, x_allowed_projects, product.group_id, exclude_id=product.id)

    for field, value in changes.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    index_product(product)
    return _attach_sub_item_count(db, product)


@router.delete("/{product_id}", status_code=204)
def delete_product(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)
    db.delete(product)
    db.commit()
    remove_product_from_index(product_id)


@router.post("/{product_id}/file")
def upload_product_file(
    product_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)

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

    object_key = f"{product.sku}/{sanitized_filename}"
    # Content-Type is derived from the filename server-side, NEVER trusted
    # from the client's upload — see app/core/upload_safety.py.
    try:
        upload_file(file.file, object_key, content_type=safe_content_type(sanitized_filename))
    except Exception:
        logger.exception("catalogue file upload failed for product %s", product.id)
        raise HTTPException(status_code=502, detail="The file couldn't be stored (file storage unavailable). Nothing was changed — try again.")

    previous_key, product.image_object_key = product.image_object_key, object_key
    try:
        db.commit()
    except Exception:
        db.rollback()
        if object_key != previous_key:  # same key = the upload replaced the file the row still points at
            delete_file(object_key)  # the row doesn't point at it: don't leave an orphan
        raise
    return {"image_object_key": object_key}


@router.get("/{product_id}/file-url")
def get_product_file_url(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    product = get_or_404(db, models.Product, product_id, scope, "no file attached to this product")
    if not product.image_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this product")
    _check_product_visible(product, x_allowed_projects)
    return {
        "url": get_presigned_url(
            product.image_object_key, force_download=should_force_download(product.image_object_key)
        )
    }


@router.get("/{product_id}/file-content")
def get_product_file_content(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """
    Called by document-service (internally, no X-Allowed-Projects header —
    treated as unrestricted, same as every other internal service-to-
    service call here) to pull a product's raw catalogue file bytes when
    building a per-document catalogue zip. Also reachable directly through
    nginx's /api/products routing, which DOES forward the header, so it's
    still project-gated for that path. Not meant for direct browser use:
    no presigned convenience, no download filename handling — the
    browser-facing equivalent is /file-url.
    """
    product = get_or_404(db, models.Product, product_id, scope, "no file attached to this product")
    if not product.image_object_key:
        raise HTTPException(status_code=404, detail="no file attached to this product")
    _check_product_visible(product, x_allowed_projects)
    try:
        file_bytes = download_file_bytes(product.image_object_key)
    except Exception:
        raise HTTPException(status_code=404, detail="the file for this product is missing from storage")
    return Response(content=file_bytes, media_type="application/octet-stream")


# ---------- sub-items (components a "bundle" product is made of) ----------

def _renumber_sub_items(db: Session, parent_product_id: uuid.UUID):
    """Keeps sequence numbers contiguous (1, 2, 3…) after a removal —
    otherwise a deleted #2 would leave a gap and #3 would stay "3" forever,
    which would be a confusing mismatch with the zip's filenames."""
    remaining = (
        db.query(models.ProductSubItem)
        .filter_by(parent_product_id=parent_product_id)
        .order_by(models.ProductSubItem.sequence_number)
        .all()
    )
    for i, item in enumerate(remaining, start=1):
        item.sequence_number = i
    db.commit()


@router.get("/{product_id}/sub-items", response_model=list[schemas.SubItemOut])
def list_sub_items(product_id: uuid.UUID, db: Session = Depends(get_db), scope: Scope = Depends(get_scope)):
    get_or_404(db, models.Product, product_id, scope, "product not found")
    rows = (
        db.query(models.ProductSubItem)
        .filter_by(parent_product_id=product_id)
        .order_by(models.ProductSubItem.sequence_number)
        .all()
    )
    result = []
    for row in rows:
        sub_product = scoped(db, models.Product, scope).filter_by(id=row.sub_product_id).first()
        if not sub_product:
            continue  # hard-deleted since, or not visible to the caller — skip it rather than error
        result.append(schemas.SubItemOut(
            id=row.id,
            sequence_number=row.sequence_number,
            product_id=sub_product.id,
            sku=sub_product.sku,
            name=sub_product.name,
            description=sub_product.description,
            unit_price=sub_product.unit_price,
            currency=sub_product.currency,
            has_file=bool(sub_product.image_object_key),
        ))
    return result


@router.post("/{product_id}/sub-items", response_model=schemas.SubItemOut, status_code=201)
def add_sub_item(
    product_id: uuid.UUID,
    payload: schemas.SubItemAdd,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    parent = get_or_404(db, models.Product, product_id, scope, "product not found")

    if payload.product_id == product_id:
        raise HTTPException(status_code=400, detail="a product can't be its own sub-item")

    sub_product = get_or_404(db, models.Product, payload.product_id, scope, "that product doesn't exist")
    if sub_product.group_id != parent.group_id:
        raise HTTPException(status_code=422, detail="That product belongs to another group than this one.")

    next_seq = (
        db.query(func.max(models.ProductSubItem.sequence_number))
        .filter_by(parent_product_id=product_id)
        .scalar() or 0
    ) + 1

    sub_item = models.ProductSubItem(
        parent_product_id=product_id,
        sub_product_id=payload.product_id,
        sequence_number=next_seq,
    )
    db.add(sub_item)
    db.commit()
    db.refresh(sub_item)

    return schemas.SubItemOut(
        id=sub_item.id,
        sequence_number=sub_item.sequence_number,
        product_id=sub_product.id,
        sku=sub_product.sku,
        name=sub_product.name,
        description=sub_product.description,
        unit_price=sub_product.unit_price,
        currency=sub_product.currency,
        has_file=bool(sub_product.image_object_key),
    )


@router.delete("/{product_id}/sub-items/{sub_item_id}", status_code=204)
def remove_sub_item(
    product_id: uuid.UUID,
    sub_item_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    scope: Scope = Depends(get_scope),
):
    _require_edit_access(x_access_level)
    get_or_404(db, models.Product, product_id, scope, "product not found")
    sub_item = (
        db.query(models.ProductSubItem)
        .filter_by(id=sub_item_id, parent_product_id=product_id)
        .first()
    )
    if not sub_item:
        raise HTTPException(status_code=404, detail="sub-item not found")
    db.delete(sub_item)
    db.commit()
    _renumber_sub_items(db, product_id)


@router.get("/{product_id}/download-bundle")
def download_product_bundle(
    product_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_allowed_projects: str | None = Header(default=None, alias="X-Allowed-Projects"),
    scope: Scope = Depends(get_scope),
):
    """
    For a product with sub-items: fetches every sub-item's catalogue file,
    renames each to its sequence number (1, 2, 3… keeping the original
    extension), and streams back a single zip. For a product with no
    sub-items, the frontend should use /file-url instead — this endpoint
    only makes sense once there's something to bundle.
    """
    product = get_or_404(db, models.Product, product_id, scope, "product not found")
    _check_product_visible(product, x_allowed_projects)

    sub_items = (
        db.query(models.ProductSubItem)
        .filter_by(parent_product_id=product_id)
        .order_by(models.ProductSubItem.sequence_number)
        .all()
    )
    if not sub_items:
        raise HTTPException(status_code=404, detail="this product has no sub-items to bundle")

    zip_buffer = io.BytesIO()
    added_any = False
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sub_items:
            sub_product = scoped(db, models.Product, scope).filter_by(id=item.sub_product_id).first()
            if not sub_product or not sub_product.image_object_key:
                continue  # nothing to include for this one — skip rather than fail the whole zip
            ext = ""
            if "." in sub_product.image_object_key.rsplit("/", 1)[-1]:
                ext = "." + sub_product.image_object_key.rsplit(".", 1)[-1]
            try:
                file_bytes = download_file_bytes(sub_product.image_object_key)
            except Exception:
                continue  # object missing from storage — skip rather than fail the whole zip
            zf.writestr(f"{item.sequence_number}{ext}", file_bytes)
            added_any = True

    if not added_any:
        raise HTTPException(status_code=404, detail="none of this product's sub-items have a catalogue file")

    zip_buffer.seek(0)
    filename = f"{product.sku}-catalogue.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
