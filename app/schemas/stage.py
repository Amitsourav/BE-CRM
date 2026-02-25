from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class StageTransitionRequest(BaseModel):
    to_stage: str
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    due_date: datetime | None = None
    lost_reason: str | None = None


class StageLogOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    from_stage: str | None = None
    to_stage: str
    changed_by: uuid.UUID
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    due_date_set: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
