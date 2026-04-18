from __future__ import annotations

import uuid
import enum
from datetime import datetime, time
from typing import Optional
from sqlalchemy import String, Integer, Boolean, DateTime, Time, Text, text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base, TimestampMixin


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    active = "active"
    paused = "paused"
    completed = "completed"
    stopped = "stopped"


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    ai_agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ai_agents.id", ondelete="SET NULL"), nullable=False)

    # Basic info
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))

    # Schedule
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    daily_start_time: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'09:00:00'"))
    daily_end_time: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'19:00:00'"))
    skip_weekends: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'Asia/Kolkata'"))

    # Retry settings
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    retry_gap_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))

    # Concurrency
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))

    # Stats (denormalized for speed)
    total_leads: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    calls_made: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    calls_connected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    calls_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_cost_usd: Mapped[float] = mapped_column(nullable=False, server_default=text("0"))

    # Meta
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    company = relationship("Company", back_populates="campaigns")
    agent = relationship("AIAgent")
    creator = relationship("Profile", foreign_keys=[created_by])
    campaign_leads = relationship("CampaignLead", back_populates="campaign", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_campaigns_company_id", "company_id"),
        Index("idx_campaigns_status", "status"),
    )
