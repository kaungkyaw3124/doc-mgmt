import uuid
from datetime import datetime

from sqlalchemy import Column, String, Numeric, DateTime, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.db import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = Column(String(100), unique=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)  # soft delete — trashed, recoverable via recycle bin
    name = Column(String(255), nullable=False)
    description = Column(String)
    category = Column(String(100), index=True)
    unit_price = Column(Numeric(12, 2))
    currency = Column(String(3), default="USD")
    remark = Column(String(500))  # shows up as its own column on quotation exports, next to Amount
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
    short_term = Column(String(20), unique=True)  # e.g. "COM" for Computer — used as the SKU prefix; must be unique or two categories would generate colliding SKU prefixes
    description = Column(String(500))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ProductSubItem(Base):
    """
    A component reference — e.g. a "Desktop Computer" product references a
    CPU, RAM, MB, GPU (each an existing Product row) as its sub-items, in a
    fixed order. sequence_number is the display order (1, 2, 3…) and is
    also what each sub-item's catalogue file gets renamed to when bundled
    into a zip (see /products/{id}/download-bundle) — kept contiguous by
    re-numbering the remaining rows whenever one is removed.
    """
    __tablename__ = "product_sub_items"
    __table_args__ = (UniqueConstraint("parent_product_id", "sequence_number", name="uq_parent_sequence"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_product_id = Column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    sub_product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    sequence_number = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
