from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base


class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    disposition: Mapped[str] = mapped_column(ENUM('dnp', 'connected', 'busy', 'switched_off', 'wrong_number', 'callback', name='call_disposition', create_type=False), nullable=False)
    conversation_notes: Mapped[str] = mapped_column(String, nullable=False)
    agent_agenda: Mapped[str] = mapped_column(String, nullable=False)
    due_date_for_next: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # VoIP extensibility
    call_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    call_recording_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    call_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    lead = relationship("Lead", back_populates="call_attempts")
    agent = relationship("Profile", foreign_keys=[agent_id])
