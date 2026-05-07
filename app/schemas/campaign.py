from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Optional, List
from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    ai_agent_id: uuid.UUID
    name: str
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(19, 0)
    skip_weekends: bool = True
    timezone: str = "Asia/Kolkata"
    max_retries: int = Field(3, ge=1, le=10)
    retry_gap_hours: int = Field(2, ge=1, le=24)
    max_concurrent_calls: int = Field(5, ge=1, le=20)
    lead_ids: List[uuid.UUID] = []


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    daily_start_time: Optional[time] = None
    daily_end_time: Optional[time] = None
    skip_weekends: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=1, le=10)
    retry_gap_hours: Optional[int] = Field(None, ge=1, le=24)
    max_concurrent_calls: Optional[int] = Field(None, ge=1, le=20)


class AssignLeadsRequest(BaseModel):
    lead_ids: List[uuid.UUID]


class AssignLeadsBulkRequest(BaseModel):
    """Filter-driven bulk assignment — pick all leads matching these
    filters and add them to the campaign in one round-trip. All filters
    are AND-combined; leads with no phone are skipped because they
    can't be dialed.
    """
    csv_import_id: Optional[uuid.UUID] = None
    current_stage: Optional[str] = None
    lead_source_id: Optional[uuid.UUID] = None
    assigned_agent_id: Optional[uuid.UUID] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    search: Optional[str] = None
    tags_any: Optional[List[str]] = None
    exclude_already_assigned: bool = True
    limit: int = Field(10000, ge=1, le=50000)


class AssignLeadsBulkResponse(BaseModel):
    matched: int
    added: int
    skipped_no_phone: int
    skipped_already_assigned: int
    truncated: bool
