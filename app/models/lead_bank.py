from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Text, DateTime, Date, Integer, Numeric, text, ForeignKey, UniqueConstraint
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

    # Sanction details — populated once bank_status reaches 'sanctioned'
    # or beyond. All nullable; the API gates write access so they can
    # only be set when the bank is in a sanctioned-or-later state.
    application_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sanction_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    loan_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    roi: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    tenure_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pf_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    first_tranche_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    no_of_tranches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pf_status: Mapped[Optional[str]] = mapped_column(
        ENUM("paid", "pending", name="pf_status_enum", create_type=False),
        nullable=True,
    )

    # ── Share provenance (Aug 2026) ────────────────────────────────────
    # WHEN and BY WHOM this lead's file was put in front of this bank —
    # as distinct from bank_status, which is the bank's decision about it.
    # Recorded by the WhatsApp bot when a lead is shared into a lender's
    # group, and available to the manual UI flow too.
    #
    # All nullable: the 436 rows that predate this were created through
    # the UI with no provenance captured. The migration backfills
    # shared_at from created_at and source='manual' for those, which is
    # an inference — a lead_banks row has always been created at the
    # point someone put the lead to that bank.
    shared_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    shared_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    # 'whatsapp' | 'manual'. Free string rather than an enum so a future
    # channel (email, portal) doesn't need a migration to be recorded.
    source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # Which lender's WhatsApp group the share happened in. Kept for
    # tracing a row back to the conversation it came from.
    wa_group_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    lead = relationship("Lead", foreign_keys=[lead_id])
    sharer = relationship("Profile", foreign_keys=[shared_by], lazy="joined")
    messages = relationship(
        "LeadBankMessage",
        back_populates="lead_bank",
        cascade="all, delete-orphan",
        order_by="LeadBankMessage.created_at",
    )
