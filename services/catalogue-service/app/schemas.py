import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Common spellings -> the stored form (loop iteration 30). Anything else is
# kept as typed; documents print "No" as No/Nos by quantity.
_UNIT_ALIASES = {
    **dict.fromkeys(("m", "meter", "metre", "meters", "metres"), "M"),
    **dict.fromkeys(("no", "nos", "number"), "No"),
    **dict.fromkeys(("pc", "pcs", "piece", "pieces"), "Pcs"),
    **dict.fromkeys(("ft", "feet", "foot"), "Ft"),
    **dict.fromkeys(("kg", "kgs"), "Kg"),
    **{u.lower(): u for u in ("Set", "Roll", "Box", "Lot")},  # the form's dropdown spelling
}


def normalize_unit(value) -> str:
    """Blank -> "No"; raises ValueError over 20 characters (the column size)."""
    unit = str(value or "").strip()
    if not unit:
        return "No"
    if len(unit) > 20:
        raise ValueError("unit must be at most 20 characters")
    return _UNIT_ALIASES.get(unit.lower(), unit)


class _UnitField(BaseModel):
    @field_validator("unit", mode="before", check_fields=False)
    @classmethod
    def _unit(cls, v):
        return normalize_unit(v)


class ProductCreate(_UnitField):
    group_id: Optional[uuid.UUID] = None  # owning group; required only when the caller is in several (or is a superuser)
    sku: Optional[str] = None  # auto-generated if omitted, e.g. COM-0001
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    unit: str = "No"
    unit_price: Optional[Decimal] = None
    currency: str = "USD"
    remark: Optional[str] = None
    attributes: Optional[dict] = None


class ProductUpdate(_UnitField):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[Decimal] = None
    currency: Optional[str] = None
    remark: Optional[str] = None
    attributes: Optional[dict] = None
    version: Optional[int] = None  # the version the client loaded; stale -> 409 (omit = no check)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    group_id: Optional[uuid.UUID] = None
    sku: str
    name: str
    description: Optional[str]
    category: Optional[str]
    unit: str = "No"
    unit_price: Optional[Decimal]
    currency: str
    remark: Optional[str]
    attributes: Optional[dict]
    image_object_key: Optional[str]
    sub_item_count: int = 0
    created_at: datetime
    updated_at: datetime
    version: int = 1


class SubItemAdd(BaseModel):
    product_id: uuid.UUID


class SubItemOut(BaseModel):
    id: uuid.UUID
    sequence_number: int
    product_id: uuid.UUID
    sku: str
    name: str
    description: Optional[str] = None
    unit: str = "No"
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


class AssignUnassigned(BaseModel):
    group_id: uuid.UUID


class MoveToGroup(BaseModel):
    """Body of POST /products/{id}/move (superuser). with_links: also move
    the bundle/sub-item products linked to it. dry_run: only count."""
    group_id: uuid.UUID
    with_links: bool = True
    dry_run: bool = False


class BulkMoveToGroup(MoveToGroup):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class DismissDuplicates(BaseModel):
    """Body of POST /products/duplicates/dismiss: same-name products of one group declared different."""
    product_ids: list[uuid.UUID] = Field(min_length=2, max_length=100)


class SetUnit(_UnitField):
    """Body of POST /products/set-unit: one unit for every selected product."""
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    unit: str
