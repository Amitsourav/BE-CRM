from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel


class LeadCreate(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = "India"
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    lead_source_id: uuid.UUID | None = None
    assigned_agent_id: uuid.UUID | None = None
    custom_fields: dict | None = None
    tags: list[str] | None = None
    notes: str | None = None


class LeadUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    custom_fields: dict | None = None
    tags: list[str] | None = None
    notes: str | None = None


class LeadOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    current_stage: str
    assigned_agent_id: uuid.UUID | None = None
    lead_source_id: uuid.UUID | None = None
    call_attempt_count: int = 0
    due_date: datetime | None = None
    connected_time: datetime | None = None
    won_time: datetime | None = None
    lost_time: datetime | None = None
    lost_reason: str | None = None
    custom_fields: dict = {}
    tags: list[str] = []
    notes: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadAssign(BaseModel):
    agent_id: uuid.UUID


class LeadBulkAssign(BaseModel):
    lead_ids: list[uuid.UUID]
    agent_id: uuid.UUID


class LeadSearchParams(BaseModel):
    q: str | None = None
    stage: str | None = None
    agent_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    tags: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 25


class LeadSourceCreate(BaseModel):
    name: str
    source_type: str = "manual"
    meta_form_id: str | None = None


class LeadSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    meta_form_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
