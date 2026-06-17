from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict
from sqlalchemy import String, Integer, Boolean, Numeric, Date, DateTime, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, ENUM
from app.core.constants import LEAD_STAGE_VALUES
from app.models.base import Base, TimestampMixin


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    # Per-company serial. Resets to 1 per tenant — each company sees
    # leads as #1, #2, #3, ... in created-at order. Used by the
    # admin "Distribute by range" flow and shown on every Kanban card.
    # Auto-assigned in LeadService.create_lead via the company_lead_counters
    # table; nullable for backwards compatibility with rows created
    # before the migration ran (none on FMC/AV — backfill assigned to all).
    serial_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Identity
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    alternate_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String, nullable=True, server_default=text("'India'"))
    pincode: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Education
    highest_qualification: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stream: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    passing_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    college_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    university: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    percentage: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    target_degree: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_intake: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    preferred_countries: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    preferred_universities: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)

    # Pipeline
    current_stage: Mapped[str] = mapped_column(ENUM(*LEAD_STAGE_VALUES, name='lead_stage', create_type=False), nullable=False, server_default=text("'lead'"))
    assigned_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    pre_counsellor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    lead_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("lead_sources.id", ondelete="SET NULL"), nullable=True)
    csv_import_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("csv_imports.id", ondelete="SET NULL"), nullable=True)
    call_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    won_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Meta
    custom_fields: Mapped[Dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    tags: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default=text("'{}'"))
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # VoIP extensibility
    last_call_provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_call_recording_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Tracking
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Important flag — boolean star/escalation marker. Doesn't affect
    # stage; an Important lead at stage=Processing stays in Processing
    # but renders with a star on the Kanban card.
    is_important: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # FMC enhanced tile fields (May 2026). Admitverse leaves these
    # untouched. loan_amount is free text so the telecaller can write
    # "25 L" or "2.5 cr" without thinking about units.
    loan_amount: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Parsed numeric mirror of `loan_amount` (in lakhs). Auto-populated
    # on create/update/CSV import via app.utils.loan_parser. Used by the
    # Kanban budget-range filter so the query compares numbers instead
    # of guessing whether "25 lakh" is bigger than "1cr".
    loan_amount_lakh: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bank_status: Mapped[Optional[str]] = mapped_column(
        ENUM(
            'applied', 'docs_reviewed', 'under_review', 'loan_login',
            'sanctioned', 'pf_paid', 'disbursed',
            name='bank_status', create_type=False,
        ),
        nullable=True,
    )
    docs_required: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("6"))
    docs_submitted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # FMC DNP attempt counter — auto-incremented in StageMachine when a
    # lead moves into the 'dnp' stage. Admitverse ignores this counter.
    dnp_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    submitted_docs: Mapped[List[str]] = mapped_column(ARRAY(String), nullable=False, server_default=text("'{}'"))

    # Admitverse tile field (May 2026). Free-text budget the counsellor
    # captures from the lead ("50 L", "2 cr", "12,000 GBP"). FMC leaves
    # this NULL — present here only so the shared model class doesn't
    # diverge across the two backends.
    budget: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Parsed numeric mirror of `budget` (in the currency's base unit) +
    # detected currency. Auto-populated via app.utils.budget_parser so the
    # AV Kanban budget-range filter can compare numbers within a currency.
    # Analog of loan_amount_lakh, but multi-currency for study-abroad.
    budget_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    budget_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True, server_default=text("'INR'"))

    # Admitverse per-university application mirror (analog of bank_name /
    # bank_status). Auto-synced by the service to the highest-priority
    # lead_applications entry, shown as the primary application on the tile.
    primary_university: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    application_status: Mapped[Optional[str]] = mapped_column(
        ENUM(
            'applied', 'shortlisted', 'offer_received', 'conditional_offer',
            'unconditional_offer', 'deposit_paid', 'cas_received',
            'visa_applied', 'visa_approved', 'enrolled', 'rejected', 'withdrawn',
            name='application_status', create_type=False,
        ),
        nullable=True,
    )

    # Relationships
    company = relationship("Company", back_populates="leads")
    assigned_agent = relationship("Profile", back_populates="assigned_leads", foreign_keys=[assigned_agent_id])
    pre_counsellor = relationship("Profile", foreign_keys=[pre_counsellor_id])
    lead_source = relationship("LeadSource", back_populates="leads")
    stage_logs = relationship("LeadStageLog", back_populates="lead", order_by="LeadStageLog.created_at.desc()")
    call_attempts = relationship("CallAttempt", back_populates="lead", order_by="CallAttempt.created_at.desc()")
    tasks = relationship("Task", back_populates="lead")
