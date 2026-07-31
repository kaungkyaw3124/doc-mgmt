import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    sku: Optional[str] = None  # auto-generated if omitted, e.g. COM-0001
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: str = "USD"
    remark: Optional[str] = None
    attributes: Optional[dict] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: Optional[str] = None
    remark: Optional[str] = None
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
    remark: Optional[str]
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
    description: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: Optional[str] = None
    has_file: bool


class CategoryCreate(BaseModel):
    name: str = Field(max_length=100)
    short_term: str = Field(max_length=20)  # required — this is what SKUs get generated from, e.g. "COM"
    description: Optional[str] = Field(default=None, max_length=500)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    short_term: Optional[str] = Field(default=None, max_length=20)
    description: Optional[str] = Field(default=None, max_length=500)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    short_term: Optional[str]
    description: Optional[str]
    created_at: datetime
