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
