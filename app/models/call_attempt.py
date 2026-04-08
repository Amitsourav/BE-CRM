from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, Float, DateTime, text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base


class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)

    # Manual call logging fields (existing — do not change)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    disposition: Mapped[str] = mapped_column(ENUM('dnp', 'connected', 'busy', 'switched_off', 'wrong_number', 'callback', name='call_disposition', create_type=False), nullable=False)
    conversation_notes: Mapped[str] = mapped_column(String, nullable=False)
    agent_agenda: Mapped[str] = mapped_column(String, nullable=False)
    due_date_for_next: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # VoIP extensibility (existing — do not change)
    call_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    call_recording_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_call_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    call_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Telephony fields
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bolna_call_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    call_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ai'"))
    call_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    ai_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("ai_agents.id", ondelete="SET NULL"), nullable=True)
    telecaller_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)

    # AI post-call fields
    transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Call metrics
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=text("now()"))

    # Relationships
    lead = relationship("Lead", back_populates="call_attempts")
    agent = relationship("Profile", foreign_keys=[agent_id])
    ai_agent = relationship("AIAgent", back_populates="calls", foreign_keys=[ai_agent_id])
    telecaller = relationship("Profile", foreign_keys=[telecaller_id])

    # Indexes
    __table_args__ = (
        Index("ix_call_attempts_call_status", "call_status"),
        Index("ix_call_attempts_call_type", "call_type"),
        Index("ix_call_attempts_provider", "bolna_call_id"),
        Index("ix_call_attempts_ai_agent", "ai_agent_id"),
        Index("ix_call_attempts_telecaller", "telecaller_id"),
        Index("ix_call_attempts_sentiment", "sentiment"),
        Index("ix_call_attempts_started_at", "started_at"),
    )
