"""Pydantic v2 request/response models.

Validation here is intentionally LOCAL only. We do not (yet) check whether
`user_id` exists in the User Service — see README "Referential Integrity".
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SaleBase(BaseModel):
    user_id: int = Field(..., ge=1, description="Logical id of the owning user (User Service).")
    item_name: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(..., gt=0, description="Must be strictly greater than zero.")

    @field_validator("item_name")
    @classmethod
    def _strip_item_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("item_name must not be blank")
        return v


class SaleCreate(SaleBase):
    """Body for `POST /sales` — a sale created natively by this service."""


class SaleUpdate(SaleBase):
    """Body for `PUT /sales/{id}` — full replacement of the mutable fields."""


class SaleImport(SaleBase):
    """Body for `POST /internal/sales/import` — legacy migration only.

    The id and created_at are supplied by the caller and preserved verbatim.
    """

    id: int = Field(..., ge=1)
    created_at: datetime


class SaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    item_name: str
    quantity: int
    created_at: datetime


class ImportResult(BaseModel):
    """Envelope for import responses so callers can tell created vs. no-op."""

    outcome: str  # "created" | "unchanged"
    sale: SaleRead
