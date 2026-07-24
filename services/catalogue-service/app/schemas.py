import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProductCreate(BaseModel):
    sku: Optional[str] = None  # auto-generated if omitted, e.g. COM-0001
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: str = "USD"
    attributes: Optional[dict] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: Optional[str] = None
    attributes: Optional[dict] = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    description: Optional[str]
    category: Optional[str]
    unit_price: Optional[Decimal]
    currency: str
    attributes: Optional[dict]
    image_object_key: Optional[str]
    sub_item_count: int = 0
    created_at: datetime
    updated_at: datetime


class SubItemAdd(BaseModel):
    product_id: uuid.UUID


class SubItemOut(BaseModel):
    id: uuid.UUID
    sequence_number: int
    product_id: uuid.UUID
    sku: str
    name: str
    has_file: bool


class CategoryCreate(BaseModel):
    name: str
    short_term: str  # required — this is what SKUs get generated from, e.g. "COM"
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    short_term: Optional[str] = None
    description: Optional[str] = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    short_term: Optional[str]
    description: Optional[str]
    created_at: datetime
