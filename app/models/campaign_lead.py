from __future__ import annotations

import uuid
import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class CampaignLeadStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    calling = "calling"
    completed = "completed"
    failed = "failed"
    dnd = "dnd"
    opted_out = "opted_out"


class CampaignLead(Base):
    __tablename__ = "campaign_leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))

    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Last call result
    last_call_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("call_attempts.id", ondelete="SET NULL"), nullable=True)
    last_call_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Priority (higher = called first)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    campaign = relationship("Campaign", back_populates="campaign_leads")
    lead = relationship("Lead")
    last_call = relationship("CallAttempt", foreign_keys=[last_call_id])

    __table_args__ = (
        Index("idx_campaign_leads_campaign_id", "campaign_id"),
        Index("idx_campaign_leads_lead_id", "lead_id"),
        Index("idx_campaign_leads_company_id", "company_id"),
        Index("idx_campaign_leads_status", "status"),
        Index("idx_campaign_leads_next_retry", "next_retry_at"),
        Index("idx_campaign_leads_priority", "priority"),
    )
