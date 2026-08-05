from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    # The service-account profile the key acts as. Create it first with
    # POST /auth/register (role="admin" for admin-equivalent scope), then
    # pass its id here. Must belong to the caller's company.
    profile_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    # NULL / omitted = no expiry; the key lives until it is revoked.
    expires_at: datetime | None = None

    model_config = {"extra": "forbid"}


class ApiKeyOut(BaseModel):
    """A key as listed. Never carries the secret."""
    id: uuid.UUID
    company_id: uuid.UUID
    profile_id: uuid.UUID
    profile_email: str | None = None
    profile_role: str | None = None
    name: str
    key_prefix: str
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    """Returned once, from POST /api-keys only.

    `api_key` is the plaintext. It is not stored and cannot be shown
    again — only its SHA-256 lives in the database.
    """
    api_key: str
