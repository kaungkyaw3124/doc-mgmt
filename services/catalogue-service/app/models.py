import uuid
from datetime import datetime

from sqlalchemy import Column, String, Numeric, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.db import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = Column(String(100), unique=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)  # soft delete — trashed, recoverable via recycle bin
    name = Column(String(255), nullable=False)
    description = Column(String)
    category = Column(String(100))
    unit_price = Column(Numeric(12, 2))
    currency = Column(String(3), default="USD")
    attributes = Column(JSONB)              # flexible per-category fields
    image_object_key = Column(String(500))  # pointer into MinIO, reusing document-service's bucket approach
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class Category(Base):
    """
    A managed list of product categories — created by whoever has the
    'categories' permission grant, then offered as a real dropdown (not
    free text) on the product form. Products still store category as a
    plain string (loosely coupled, not a strict FK), matching this
    platform's existing pattern for cross-cutting reference fields.
    """
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)
    short_term = Column(String(20))  # e.g. "COM" for Computer — manually entered, used as the SKU prefix
    description = Column(String(500))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
