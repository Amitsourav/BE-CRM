from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base


class LeadStageLog(Base):
    __tablename__ = "lead_stage_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    from_stage: Mapped[Optional[str]] = mapped_column(ENUM('lead', 'called', 'connected', 'qualified_lead', 'won', 'lost', name='lead_stage', create_type=False), nullable=True)
    to_stage: Mapped[str] = mapped_column(ENUM('lead', 'called', 'connected', 'qualified_lead', 'won', 'lost', name='lead_stage', create_type=False), nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    conversation_notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_agenda: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    due_date_set: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    lead = relationship("Lead", back_populates="stage_logs")
    changed_by_user = relationship("Profile", foreign_keys=[changed_by])
