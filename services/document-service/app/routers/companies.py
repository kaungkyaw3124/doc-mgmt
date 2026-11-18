import io
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from PIL import Image
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.concurrency import check_version, claim_idempotency_key, remember
from app.core.group_scope import ALL, scoped
from app.core.storage import upload_file, get_presigned_url
from app import models, schemas

router = APIRouter(prefix="/companies", tags=["companies"])

# Every logo/seal gets resized to exactly one of these two standard pixel
# sizes at upload time — not just constrained visually via CSS — so every
# company's letterhead looks consistent regardless of what was uploaded.
STANDARD_LOGO_SIZE = (300, 300)  # square, like the seal — most logos aren't naturally 2:1 wide, and stretching a roughly-square/circular logo into a wide box made it look oval
STANDARD_SEAL_SIZE = (300, 300)
LETTERHEAD_MAX_WIDTH = 2000  # px — a banner is scaled DOWN to this if wider, never up, and never stretched

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


SHORT_NAME_RE = re.compile(r"^[A-Za-z0-9]{2,6}$")
DEFAULT_MD_POSITION = "Managing Director"


def _clean_short_name(value):
    """Trimmed and uppercased; blank -> None (document numbers then use "DOC").
    Existing invalid values in the DB are left alone until the company is edited."""
    value = (value or "").strip()
    if not value:
        return None
    if not SHORT_NAME_RE.match(value):
        raise HTTPException(status_code=422, detail='Short name must be 2–6 letters or digits (e.g. "WF") — it is used in document numbers.')
    return value.upper()


def _clean_md_position(value):
    value = (value or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail='Position is required for a Managing Director (e.g. "Managing Director").')
    return value


LETTERHEAD_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_LETTERHEAD_FORMATS = {"PNG", "JPEG", "WEBP"}


def _standardize_letterhead(file_bytes: bytes) -> bytes | None:
    """
    The proportional "banner" variant of an uploaded logo: unlike the logo
    itself (stretched to a fixed square), it keeps its own proportions, is
    only downscaled if wider than LETTERHEAD_MAX_WIDTH, and is re-saved as
    PNG. Returns None (no banner variant) for anything that isn't a readable
    PNG/JPEG/WebP — the logo upload still succeeds without it.
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.format not in _LETTERHEAD_FORMATS:
            return None
        img = img.convert("RGBA")
        if img.width > LETTERHEAD_MAX_WIDTH:
            img = img.resize((LETTERHEAD_MAX_WIDTH, round(img.height * LETTERHEAD_MAX_WIDTH / img.width)), Image.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return None


@router.post("", response_model=schemas.CompanyOut, status_code=201)
def create_company(
    payload: schemas.CompanyCreate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    _require_edit_access(x_access_level)
    data = payload.model_dump()
    data["short_name"] = _clean_short_name(data["short_name"])
    held, replay_id = claim_idempotency_key(db, idempotency_key, "POST /companies")
    if replay_id:
        return get_company(replay_id, db)
    company = models.Company(id=uuid.uuid4(), **data)
    remember(held, company.id)

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
    check_version(company, payload.version)

    update_data = payload.model_dump(exclude_unset=True, exclude={"version"})
    if "short_name" in update_data:
        update_data["short_name"] = _clean_short_name(update_data["short_name"])
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


# DEV-ONLY: delete-all BEGIN — one-click wipe for dev testing (loop iteration 15)
@router.delete("")
def delete_all_companies(
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    """Permanently deletes EVERY company (recycle bin included), its MDs and
    their logo/seal files. Documents are kept with company/MD cleared."""
    from app.core.dev_delete_all import refuse_in_production_or_view_only, delete_files

    refuse_in_production_or_view_only(x_access_level)
    keys = [k for row in db.query(models.Company.logo_object_key, models.Company.seal_object_key) for k in row]
    keys += [k for (k,) in db.query(models.CompanyDirector.seal_object_key)]
    # Companies are shared by every group, so documents of ALL groups lose the link.
    scoped(db, models.Document, ALL).update(
        {models.Document.company_id: None, models.Document.director_id: None}, synchronize_session=False
    )
    db.query(models.CompanyDirector).delete(synchronize_session=False)
    deleted = db.query(models.Company).delete(synchronize_session=False)
    db.commit()
    delete_files(keys)
    return {"deleted": deleted}
# DEV-ONLY: delete-all END


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

    # Read one byte past the limit so a huge upload isn't pulled fully into memory.
    raw_bytes = file.file.read(LETTERHEAD_MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > LETTERHEAD_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large (max 10 MB).")
    safe_name = _sanitize_filename(file.filename)
    is_svg = safe_name.lower().endswith(".svg")
    if not is_svg:
        try:
            Image.open(io.BytesIO(raw_bytes)).load()
        except Exception:
            raise HTTPException(status_code=400, detail="Logo must be a PNG, JPG, WebP or SVG image.")

    # One upload, two stored variants: the squared logo (header_style "logo")
    # and the proportional banner (header_style "letterhead"), so switching
    # the style later needs no re-upload.
    processed_bytes, filename = _standardize_image(raw_bytes, safe_name, STANDARD_LOGO_SIZE)
    object_key = f"company-logos/{company_id}/{filename}"
    upload_file(io.BytesIO(processed_bytes), object_key, content_type=_safe_content_type(filename))

    banner_bytes = None if is_svg else _standardize_letterhead(raw_bytes)
    letterhead_key = None
    if banner_bytes:
        letterhead_key = f"company-letterheads/{company_id}/letterhead.png"
        upload_file(io.BytesIO(banner_bytes), letterhead_key, content_type="image/png")

    company.logo_object_key = object_key
    company.letterhead_object_key = letterhead_key  # an older banner belonged to the previous image
    db.commit()
    result = {"logo_object_key": object_key, "letterhead_object_key": letterhead_key}
    if not letterhead_key:
        result["note"] = "Saved as a logo only — a letterhead banner needs a PNG, JPG or WebP image."
    return result


@router.get("/{company_id}/logo-url")
def get_company_logo_url(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company or not company.logo_object_key:
        raise HTTPException(status_code=404, detail="no logo attached to this company")
    is_svg = company.logo_object_key.lower().endswith(".svg")
    return {"url": get_presigned_url(company.logo_object_key, force_download=is_svg)}


@router.get("/{company_id}/letterhead-url")
def get_company_letterhead_url(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company or not company.letterhead_object_key:
        raise HTTPException(status_code=404, detail="no letterhead attached to this company")
    return {"url": get_presigned_url(company.letterhead_object_key)}


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


# ---------- directors (Managing Directors — a company can have several, each with their own seal) ----------

@router.get("/{company_id}/directors", response_model=list[schemas.CompanyDirectorOut])
def list_company_directors(company_id: uuid.UUID, db: Session = Depends(get_db)):
    return (
        db.query(models.CompanyDirector)
        .filter_by(company_id=company_id)
        .order_by(models.CompanyDirector.sort_order)
        .all()
    )


@router.post("/{company_id}/directors", response_model=schemas.CompanyDirectorOut, status_code=201)
def add_company_director(
    company_id: uuid.UUID,
    payload: schemas.CompanyDirectorCreate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    next_order = (
        db.query(func.max(models.CompanyDirector.sort_order))
        .filter_by(company_id=company_id)
        .scalar() or 0
    ) + 1

    director = models.CompanyDirector(
        company_id=company_id,
        name=payload.name,
        address=payload.address,
        contact_no=payload.contact_no,
        email=payload.email,
        position=_clean_md_position(payload.position) if "position" in payload.model_fields_set else DEFAULT_MD_POSITION,
        sort_order=next_order,
    )
    db.add(director)
    db.commit()
    db.refresh(director)
    return director


@router.patch("/{company_id}/directors/{director_id}", response_model=schemas.CompanyDirectorOut)
def update_company_director(
    company_id: uuid.UUID,
    director_id: uuid.UUID,
    payload: schemas.CompanyDirectorUpdate,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    director = (
        db.query(models.CompanyDirector)
        .filter_by(id=director_id, company_id=company_id)
        .first()
    )
    if not director:
        raise HTTPException(status_code=404, detail="director not found")
    changes = payload.model_dump(exclude_unset=True)
    if "position" in changes:
        changes["position"] = _clean_md_position(changes["position"])
    for field, value in changes.items():
        setattr(director, field, value)
    db.commit()
    db.refresh(director)
    return director


@router.delete("/{company_id}/directors/{director_id}", status_code=204)
def remove_company_director(
    company_id: uuid.UUID,
    director_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    director = (
        db.query(models.CompanyDirector)
        .filter_by(id=director_id, company_id=company_id)
        .first()
    )
    if not director:
        raise HTTPException(status_code=404, detail="director not found")
    db.delete(director)
    db.commit()


@router.post("/{company_id}/directors/{director_id}/seal")
def upload_director_seal(
    company_id: uuid.UUID,
    director_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_access_level: str | None = Header(default=None, alias="X-Access-Level"),
):
    _require_edit_access(x_access_level)
    director = (
        db.query(models.CompanyDirector)
        .filter_by(id=director_id, company_id=company_id)
        .first()
    )
    if not director:
        raise HTTPException(status_code=404, detail="director not found")

    raw_bytes = file.file.read()
    safe_name = _sanitize_filename(file.filename)
    processed_bytes, filename = _standardize_image(raw_bytes, safe_name, STANDARD_SEAL_SIZE)
    content_type = _safe_content_type(filename)

    object_key = f"director-seals/{company_id}/{director_id}/{filename}"
    upload_file(io.BytesIO(processed_bytes), object_key, content_type=content_type)

    director.seal_object_key = object_key
    db.commit()
    return {"seal_object_key": object_key}


@router.get("/{company_id}/directors/{director_id}/seal-url")
def get_director_seal_url(company_id: uuid.UUID, director_id: uuid.UUID, db: Session = Depends(get_db)):
    director = (
        db.query(models.CompanyDirector)
        .filter_by(id=director_id, company_id=company_id)
        .first()
    )
    if not director or not director.seal_object_key:
        raise HTTPException(status_code=404, detail="no seal attached to this director")
    is_svg = director.seal_object_key.lower().endswith(".svg")
    return {"url": get_presigned_url(director.seal_object_key, force_download=is_svg)}
