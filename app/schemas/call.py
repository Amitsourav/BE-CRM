from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class CallAttemptCreate(BaseModel):
    disposition: str
    conversation_notes: str
    agent_agenda: str
    due_date_for_next: datetime | None = None
    call_provider: str | None = None
    call_recording_url: str | None = None
    external_call_id: str | None = None
    call_duration_seconds: int | None = None


class CallAttemptOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    agent_id: uuid.UUID
    attempt_number: int
    disposition: str
    conversation_notes: str
    agent_agenda: str
    due_date_for_next: datetime | None = None
    call_provider: str | None = None
    call_recording_url: str | None = None
    external_call_id: str | None = None
    call_duration_seconds: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
