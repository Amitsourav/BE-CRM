from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, Field


# --- Existing manual call logging (keep working) ---

class CallAttemptCreate(BaseModel):
    """For manual call logging via POST /leads/{id}/calls."""
    disposition: str
    conversation_notes: str
    agent_agenda: str
    due_date_for_next: datetime | None = None
    call_provider: str | None = None
    call_recording_url: str | None = None
    external_call_id: str | None = None
    call_duration_seconds: int | None = None


# --- New telephony schemas ---

class CallInitiate(BaseModel):
    """For initiating a new call (AI or live)."""
    lead_id: uuid.UUID
    ai_agent_id: uuid.UUID | None = None
    call_type: str = "ai"
    phone_number: str | None = None  # override lead's phone if provided


class CallStatusUpdate(BaseModel):
    """For updating call status (from Bolna webhooks or manual)."""
    call_status: str
    bolna_call_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class CallPostData(BaseModel):
    """For saving post-call AI data (transcript, summary, sentiment)."""
    transcript: str | None = None
    summary: str | None = None
    sentiment: str | None = None
    sentiment_score: float | None = Field(None, ge=0.0, le=1.0)
    cost: float | None = Field(None, ge=0.0)
    call_duration_seconds: int | None = None
    call_recording_url: str | None = None


# --- Output schemas ---

class CallAttemptOut(BaseModel):
    """Full call detail output."""
    id: uuid.UUID
    lead_id: uuid.UUID
    company_id: uuid.UUID
    call_type: str
    call_status: str
    ai_agent_id: uuid.UUID | None = None
    telecaller_id: uuid.UUID | None = None
    agent_id: uuid.UUID
    bolna_call_id: str | None = None
    attempt_number: int
    disposition: str | None = None
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    transcript: str | None = None
    summary: str | None = None
    sentiment: str | None = None
    sentiment_score: float | None = None
    cost: float | None = None
    call_duration_seconds: int | None = None
    call_recording_url: str | None = None
    call_provider: str | None = None
    external_call_id: str | None = None
    due_date_for_next: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class CallAttemptWithLead(CallAttemptOut):
    """Call detail with lead and agent info."""
    lead_name: str | None = None
    lead_phone: str | None = None
    agent_name: str | None = None
