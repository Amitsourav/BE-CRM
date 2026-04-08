from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class CompanyCreate(BaseModel):
    name: str
    slug: str
    timezone: str = "UTC"


class CompanyUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    timezone: str | None = None
    is_active: bool | None = None


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
