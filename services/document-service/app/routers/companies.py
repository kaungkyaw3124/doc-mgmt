import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.storage import upload_file, get_presigned_url
from app import models, schemas

router = APIRouter(prefix="/companies", tags=["companies"])


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
    return db.query(models.Company).order_by(models.Company.created_at.desc()).all()


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


@router.post("/{company_id}/logo")
def upload_company_logo(company_id: uuid.UUID, file: UploadFile = File(...), db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="company not found")

    object_key = f"company-logos/{company_id}/{file.filename}"
    upload_file(file.file, object_key, content_type=file.content_type or "application/octet-stream")

    company.logo_object_key = object_key
    db.commit()
    return {"logo_object_key": object_key}


@router.get("/{company_id}/logo-url")
def get_company_logo_url(company_id: uuid.UUID, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter_by(id=company_id).first()
    if not company or not company.logo_object_key:
        raise HTTPException(status_code=404, detail="no logo attached to this company")
    return {"url": get_presigned_url(company.logo_object_key)}
