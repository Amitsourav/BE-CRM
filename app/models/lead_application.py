from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Text, DateTime, Date, Numeric, text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base


class LeadApplication(Base):
    """One entry per (lead, university, program) — the Admitverse analog of
    LeadBank. A study-abroad lead applies to several universities at once;
    each application has its own status (applied → offer_received → ... →
    enrolled). The "primary" application shown on the Kanban tile is
    auto-synced to the highest-status entry in lead.primary_university /
    lead.application_status by the service layer.
    """
    __tablename__ = "lead_applications"
    __table_args__ = (
        UniqueConstraint(
            "lead_id", "university_name", "program",
            name="uniq_lead_apps_lead_uni_program",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)

    university_name: Mapped[str] = mapped_column(String(200), nullable=False)
    program: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    intake: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    application_status: Mapped[str] = mapped_column(
        ENUM(
            "applied", "shortlisted", "offer_received", "conditional_offer",
            "unconditional_offer", "deposit_paid", "cas_received",
            "visa_applied", "visa_approved", "enrolled", "rejected", "withdrawn",
            name="application_status", create_type=False,
        ),
        nullable=False, server_default=text("'applied'"),
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Offer / admission details — writable once the application reaches an
    # offer-or-later status (analog of FMC's sanction details). All nullable.
    application_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    offer_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    tuition_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    scholarship_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    deposit_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    deposit_paid_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    cas_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    visa_status: Mapped[Optional[str]] = mapped_column(
        ENUM("not_started", "applied", "approved", "rejected", name="visa_status_enum", create_type=False),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    lead = relationship("Lead", foreign_keys=[lead_id])
