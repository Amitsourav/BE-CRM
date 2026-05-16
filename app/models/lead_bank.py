from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base


class LeadBank(Base):
    """One entry per (lead, bank). Tracks the status with which a lead
    has been shared with a specific bank. A lead can have multiple entries
    — e.g., Axis Sanctioned + Credila Applied + UniCred Under Review.
    The "primary" bank shown on the Kanban tile is auto-synced to the
    highest-status entry in lead.bank_name / lead.bank_status by the
    service layer.
    """
    __tablename__ = "lead_banks"
    __table_args__ = (
        UniqueConstraint("lead_id", "bank_name", name="uniq_lead_banks_lead_bank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bank_status: Mapped[str] = mapped_column(
        ENUM(
            "applied", "docs_reviewed", "under_review", "loan_login",
            "sanctioned", "pf_paid", "disbursed",
            name="bank_status", create_type=False,
        ),
        nullable=False, server_default=text("'applied'"),
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    lead = relationship("Lead", foreign_keys=[lead_id])
