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
    created_at: datetime
    updated_at: datetime


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: Optional[str]
    created_at: datetime
