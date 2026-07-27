import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
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
STANDARD_LOGO_SIZE = (400, 200)
STANDARD_SEAL_SIZE = (300, 300)


def _standardize_image(file_bytes: bytes, filename: str, target_size: tuple[int, int]) -> tuple[bytes, str]:
    """
    Resizes a raster image (PNG/JPEG/etc.) to fit within target_size,
    preserving aspect ratio, then pads it onto a transparent canvas of
    EXACTLY target_size so every stored logo/seal has identical pixel
    dimensions. Always re-saved as PNG for consistency.

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
        img.thumbnail(target_size, Image.LANCZOS)
        canvas = Image.new("RGBA", target_size, (255, 255, 255, 0))
        offset = ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2)
        canvas.paste(img, offset, img)
        output = io.BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue(), "standardized.png"
    except Exception:
        return file_bytes, filename


@router.post("", response_model=schemas.CompanyOut, status_code=201)
def create_company(payload: schemas.CompanyCreate, db: Session = Depends(get_db)):
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
def update_company(company_id: uuid.UUID, payload: schemas.CompanyUpdate, db: Session = Depends(get_db)):
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
def trash_company(company_id: uuid.UUID, db: Session = Depends(get_db)):
    """Soft delete — hides it from the main list, but keeps it recoverable
    via the recycle bin. Existing documents that reference this company
    are unaffected. For a permanent purge, use DELETE /{company_id}
    instead (only called from within the recycle bin)."""
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    company.is_deleted = True
    db.commit()
    db.refresh(company)
    return company


@router.patch("/{company_id}/restore", response_model=schemas.CompanyOut)
def restore_company(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    company.is_deleted = False
    db.commit()
    db.refresh(company)
    return company


@router.delete("/{company_id}", status_code=204)
def delete_company(company_id: uuid.UUID, db: Session = Depends(get_db)):
    """Permanent purge — only reachable from within the recycle bin.
    Blocked (with a clear message) if any document still references this
    company, rather than failing with a raw database error."""
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
def upload_company_logo(company_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    raw_bytes = file.file.read()
    processed_bytes, filename = _standardize_image(raw_bytes, file.filename, STANDARD_LOGO_SIZE)
    content_type = "image/png" if filename.endswith(".png") else (file.content_type or "application/octet-stream")

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
    return {"url": get_presigned_url(company.logo_object_key)}


@router.post("/{company_id}/seal")
def upload_company_seal(company_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    raw_bytes = file.file.read()
    processed_bytes, filename = _standardize_image(raw_bytes, file.filename, STANDARD_SEAL_SIZE)
    content_type = "image/png" if filename.endswith(".png") else (file.content_type or "application/octet-stream")

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
    return {"url": get_presigned_url(company.seal_object_key)}
