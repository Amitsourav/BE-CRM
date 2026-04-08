from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    user_id: uuid.UUID
    type: str
    title: str
    message: str
    is_read: bool
    lead_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountOut(BaseModel):
    count: int
