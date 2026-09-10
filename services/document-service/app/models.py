import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Numeric, Date, DateTime, ForeignKey, Integer, Text, Boolean
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    budget_year = Column(String(20))  # e.g. "2025-2026"
    description = Column(Text)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    documents = relationship("Document", back_populates="project")


class Company(Base):
    __tablename__ = "companies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    short_name = Column(String(20))  # e.g. "SS" for Swift Solution — used in doc numbers (SS-20260723/001)
    position = Column(String(100))  # e.g. "Director" — title of the authorized signer; used by signer/signature generation, not shown in the Supplier block
    address = Column(Text)
    contact_no = Column(String(50))
    support_email = Column(String(255))
    logo_object_key = Column(String(500))
    seal_object_key = Column(String(500))  # official company seal/stamp, shown on quotation exports
    is_primary = Column(Boolean, default=False, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class CompanyDirector(Base):
    """
    A Managing Director (MD) for a company — a company can have several,
    each with their own name/address/contact_no/email and their own
    personal seal (distinct from the company's own general
    seal_object_key). Selected per-document to show alongside the
    company's own Supplier info (see Document.director_id).
    """
    __tablename__ = "company_directors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    address = Column(Text)
    contact_no = Column(String(50))
    email = Column(String(255))
    seal_object_key = Column(String(500))
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    email = Column(String(255))
    phone = Column(String(50))
    billing_address = Column(JSONB)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    documents = relationship("Document", back_populates="customer")


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_type = Column(String(20), nullable=False, index=True)  # quotation | invoice | catalogue
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)  # soft delete — trashed, recoverable via recycle bin
    deleted_by = Column(String(100))  # username of who trashed it (from the server-verified X-Username header, never client-supplied) — cleared on restore
    deleted_at = Column(DateTime(timezone=True))  # cleared on restore; NULL for both a never-deleted doc and one trashed before this column existed (see docs/SECURITY_HARDENING_LOG.md's Recycle Bin entry)
    doc_number = Column(String(50), unique=True, nullable=False)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), index=True)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), index=True)  # which company issues this document — chosen per-document, no more single "primary" company
    director_id = Column(UUID(as_uuid=True), ForeignKey("company_directors.id"), index=True)  # which MD's name/seal to show on this document
    status = Column(String(20), nullable=False, default="draft", index=True)
    currency = Column(String(3), default="USD")
    subtotal = Column(Numeric(12, 2))
    tax_total = Column(Numeric(12, 2))
    total = Column(Numeric(12, 2))
    issue_date = Column(Date)
    due_date = Column(Date)
    doc_metadata = Column("metadata", JSONB)
    terms_and_conditions = Column(Text)
    file_object_key = Column(String(500))
    version = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="documents")
    project = relationship("Project", back_populates="documents")
    items = relationship("LineItem", back_populates="document", cascade="all, delete-orphan", order_by="LineItem.sort_order")


class LineItem(Base):
    __tablename__ = "line_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    product_id = Column(UUID(as_uuid=True), index=True)
    sort_order = Column(Integer, default=0)  # preserves entry order — ids are random UUIDs, not sequential
    description = Column(Text)
    remark = Column(String(500))  # per-line-item override — falls back to the product's own catalog remark if blank
    unit = Column(String(20), default="Nos")
    quantity = Column(Numeric(12, 2))
    unit_price = Column(Numeric(12, 2))
    tax_rate = Column(Numeric(5, 2))
    line_total = Column(Numeric(12, 2))

    document = relationship("Document", back_populates="items")


class AuditLogEntry(Base):
    """
    Tracks who viewed or edited a document, and when. Visible to whoever
    has the "audit-log" service grant (see auth-service's RBAC — intended
    for a "Director"-style role), not to regular editors/viewers.
    """
    __tablename__ = "audit_log_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(100), nullable=False)
    action = Column(String(30), nullable=False)  # "created" | "viewed" | "edited" | "status_changed" | "file_uploaded" | "deleted" | "restored"
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
