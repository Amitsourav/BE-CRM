from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class TaskCreate(BaseModel):
    lead_id: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    task_type: str = "follow_up"
    title: str
    description: str | None = None
    due_date: datetime


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    due_date: datetime | None = None
    task_type: str | None = None


class TaskComplete(BaseModel):
    completion_notes: str | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID | None = None
    assigned_to: uuid.UUID
    created_by: uuid.UUID
    task_type: str
    title: str
    description: str | None = None
    status: str
    due_date: datetime
    completed_at: datetime | None = None
    completion_notes: str | None = None
    stage_log_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
