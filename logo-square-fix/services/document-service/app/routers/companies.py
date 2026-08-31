import io
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.storage import upload_file, get_presigned_url
from app import models, schemas

router = APIRouter(prefix="/companies", tags=["companies"])

# Every logo/seal gets resized to exactly one of these two standard pixel
# sizes at upload time — not just constrained visually via CSS — so every
# company's letterhead looks consistent regardless of what was uploaded.
STANDARD_LOGO_SIZE = (300, 300)  # square, like the seal — most logos aren't naturally 2:1 wide, and stretching a roughly-square/circular logo into a wide box made it look oval
STANDARD_SEAL_SIZE = (300, 300)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_ALLOWED_IMAGE_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
}


def _require_edit_access(x_access_level: str | None):
    """Mirrors routers/documents.py's helper — missing header defaults to
    "edit" (backward-compat, restrictions are opt-in)."""
    if x_access_level == "view":
        raise HTTPException(status_code=403, detail="your role has view-only access to documents")


def _sanitize_filename(filename: str | None) -> str:
    """Strips any directory components (blocks path traversal via the
    object key) and collapses everything else to a safe character set."""
    base = os.path.basename(filename or "") or "file"
    return _SAFE_FILENAME_RE.sub("_", base)


def _safe_content_type(filename: str) -> str:
    """Derives Content-Type from the (sanitized) file extension instead of
    trusting the client-supplied Content-Type header, so a browser can't be
    tricked into rendering an uploaded file (e.g. an SVG with embedded
    script) as something other than what the extension says it is."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _ALLOWED_IMAGE_EXT_TO_MIME.get(ext, "application/octet-stream")


def _standardize_image(file_bytes: bytes, filename: str, target_size: tuple[int, int]) -> tuple[bytes, str]:
    """
    Resizes a raster image (PNG/JPEG/etc.) to EXACTLY target_size by
    stretching it to fill — not fitting within and padding — so every
    stored logo/seal displays at full, consistent size regardless of the
    original image's own proportions. Always re-saved as PNG.

    SVGs are left completely untouched — they're vector, not raster, and
    resizing them properly would need an extra rendering dependency
    (e.g. cairosvg); they still display fine since the page CSS constrains
    their box size, they just aren't normalized to a fixed pixel size.

    Falls back to the original bytes/filename if processing fails for any
    reason, rather than blocking the upload entirely.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    if ext == "svg":
        return file_bytes, filename

    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = img.convert("RGBA")
        img = img.resize(target_size, Image.LANCZOS)  # stretch to fill exactly — no padding, no small-in-a-big-box look
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue(), "standardized.png"
    except Exception:
        return file_bytes, filename


@router.post("", response_model=schemas.CompanyOut, status_code=201)
def create_company(
    payload: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = models.Company(**payload.model_dump())

    # If this is the first company ever created, or explicitly marked primary,
    # make sure only one company is ever "primary" at a time (used as the
    # supplier on generated quotations).
    if payload.is_primary or db.query(models.Company).count() == 0:
        db.query(models.Company).update({models.Company.is_primary: False})
        company.is_primary = True

    db.add(company)
    db.commit()
    db.refresh(company)
    return company


@router.get("", response_model=list[schemas.CompanyOut])
def list_companies(db: Session = Depends(get_db)):
    return (
        db.query(models.Company)
        .filter(models.Company.is_deleted == False)  # noqa: E712
        .order_by(models.Company.created_at.desc())
        .all()
    )


@router.get("/trash", response_model=list[schemas.CompanyOut])
def list_trashed_companies(db: Session = Depends(get_db)):
    """The recycle bin — soft-deleted companies."""
    return (
        db.query(models.Company)
        .filter(models.Company.is_deleted == True)  # noqa: E712
        .order_by(models.Company.created_at.desc())
        .all()
    )


@router.get("/{company_id}", response_model=schemas.CompanyOut)
def get_company(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    return company


@router.patch("/{company_id}", response_model=schemas.CompanyOut)
def update_company(
    company_id: uuid.UUID,
    payload: schemas.CompanyUpdate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    update_data = payload.model_dump(exclude_unset=True)
    if update_data.get("is_primary") is True:
        db.query(models.Company).update({models.Company.is_primary: False})

    for field, value in update_data.items():
        setattr(company, field, value)

    db.commit()
    db.refresh(company)
    return company


@router.patch("/{company_id}/trash", response_model=schemas.CompanyOut)
def trash_company(
    company_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Soft delete — hides it from the main list, but keeps it recoverable
    via the recycle bin. Existing documents that reference this company
    are unaffected. For a permanent purge, use DELETE /{company_id}
    instead (only called from within the recycle bin)."""
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    company.is_deleted = True
    db.commit()
    db.refresh(company)
    return company


@router.patch("/{company_id}/restore", response_model=schemas.CompanyOut)
def restore_company(
    company_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    company.is_deleted = False
    db.commit()
    db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=204)
def delete_company(
    company_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Permanent purge — only reachable from within the recycle bin.
    Blocked (with a clear message) if any document still references this
    company, rather than failing with a raw database error."""
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    try:
        db.delete(company)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Can't permanently delete — one or more documents still reference this company. Reassign or delete those documents first.",
        )


@router.post("/{company_id}/logo")
def upload_company_logo(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    raw_bytes = file.file.read()
    safe_name = _sanitize_filename(file.filename)
    processed_bytes, filename = _standardize_image(raw_bytes, safe_name, STANDARD_LOGO_SIZE)
    content_type = _safe_content_type(filename)

    object_key = f"company-logos/{company_id}/{filename}"
    upload_file(io.BytesIO(processed_bytes), object_key, content_type=content_type)

    company.logo_object_key = object_key
    db.commit()
    return {"logo_object_key": object_key}


@router.get("/{company_id}/logo-url")
def get_company_logo_url(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company or not company.logo_object_key:
        raise HTTPException(status_code=404, detail="no logo attached to this company")
    is_svg = company.logo_object_key.lower().endswith(".svg")
    return {"url": get_presigned_url(company.logo_object_key, force_download=is_svg)}


@router.post("/{company_id}/seal")
def upload_company_seal(
    company_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    raw_bytes = file.file.read()
    safe_name = _sanitize_filename(file.filename)
    processed_bytes, filename = _standardize_image(raw_bytes, safe_name, STANDARD_SEAL_SIZE)
    content_type = _safe_content_type(filename)

    object_key = f"company-seals/{company_id}/{filename}"
    upload_file(io.BytesIO(processed_bytes), object_key, content_type=content_type)

    company.seal_object_key = object_key
    db.commit()
    return {"seal_object_key": object_key}


@router.get("/{company_id}/seal-url")
def get_company_seal_url(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company or not company.seal_object_key:
        raise HTTPException(status_code=404, detail="no seal attached to this company")
    is_svg = company.seal_object_key.lower().endswith(".svg")
    return {"url": get_presigned_url(company.seal_object_key, force_download=is_svg)}
