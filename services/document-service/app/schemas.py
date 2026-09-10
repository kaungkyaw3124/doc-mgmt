import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    budget_year: Optional[str] = None  # e.g. "2025-2026"
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    budget_year: Optional[str] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    budget_year: Optional[str]
    description: Optional[str]
    is_deleted: bool
    created_at: datetime


class CompanyCreate(BaseModel):
    name: str
    short_name: Optional[str] = None  # e.g. "SS" for Swift Solution — used in doc numbers
    position: Optional[str] = None  # signer/signature title — not shown in the quotation Supplier block
    address: Optional[str] = None
    contact_no: Optional[str] = None
    support_email: Optional[str] = None
    is_primary: bool = False


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    short_name: Optional[str] = None
    position: Optional[str] = None
    address: Optional[str] = None
    contact_no: Optional[str] = None
    support_email: Optional[str] = None
    is_primary: Optional[bool] = None


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    short_name: Optional[str]
    position: Optional[str]
    address: Optional[str]
    contact_no: Optional[str]
    support_email: Optional[str]
    logo_object_key: Optional[str]
    seal_object_key: Optional[str]
    is_primary: bool
    is_deleted: bool
    created_at: datetime


class CompanyDirectorCreate(BaseModel):
    name: str
    address: Optional[str] = None
    contact_no: Optional[str] = None
    email: Optional[str] = None


class CompanyDirectorUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    contact_no: Optional[str] = None
    email: Optional[str] = None


class CompanyDirectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    address: Optional[str]
    contact_no: Optional[str]
    email: Optional[str]
    seal_object_key: Optional[str]
    sort_order: int
    created_at: datetime


class CustomerCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    billing_address: Optional[dict] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    billing_address: Optional[dict] = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    email: Optional[str]
    phone: Optional[str]
    billing_address: Optional[dict]
    is_deleted: bool
    created_at: datetime


class DocumentItemIn(BaseModel):
    product_id: Optional[uuid.UUID] = None
    description: Optional[str] = None
    remark: Optional[str] = None
    unit: str = "Nos"
    quantity: Decimal
    unit_price: Optional[Decimal] = None
    tax_rate: Decimal = Decimal("0")


class DocumentItemOut(DocumentItemIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    line_total: Optional[Decimal] = None


class DocumentCreate(BaseModel):
    doc_type: str  # quotation | invoice | proposal | catalogue
    doc_number: Optional[str] = None  # auto-generated if omitted, e.g. INV-2026-07-17
    customer_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    company_id: Optional[uuid.UUID] = None  # which company issues this document
    director_id: Optional[uuid.UUID] = None  # which of that company's MDs signs this document
    currency: str = "USD"  # "USD" or "MMK"
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    doc_metadata: Optional[dict] = None
    terms_and_conditions: Optional[str] = None
    items: list[DocumentItemIn] = []


class DocumentUpdate(BaseModel):
    customer_id: Optional[uuid.UUID] = None
    project_id: Optional[uuid.UUID] = None
    company_id: Optional[uuid.UUID] = None
    director_id: Optional[uuid.UUID] = None
    currency: Optional[str] = None  # omit to leave the document's existing currency unchanged
    terms_and_conditions: Optional[str] = None
    items: list[DocumentItemIn] = []


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_type: str
    doc_number: str
    customer_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    company_id: Optional[uuid.UUID]
    director_id: Optional[uuid.UUID]
    status: str
    currency: str
    subtotal: Optional[Decimal]
    tax_total: Optional[Decimal]
    total: Optional[Decimal]
    issue_date: Optional[date]
    due_date: Optional[date]
    doc_metadata: Optional[dict]
    terms_and_conditions: Optional[str]
    file_object_key: Optional[str]
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_by: Optional[str] = None
    deleted_at: Optional[datetime] = None
    items: list[DocumentItemOut] = []
