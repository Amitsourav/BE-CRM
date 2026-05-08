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

    # Relationships
    company = relationship("Company", back_populates="leads")
    assigned_agent = relationship("Profile", back_populates="assigned_leads", foreign_keys=[assigned_agent_id])
    lead_source = relationship("LeadSource", back_populates="leads")
    stage_logs = relationship("LeadStageLog", back_populates="lead", order_by="LeadStageLog.created_at.desc()")
    call_attempts = relationship("CallAttempt", back_populates="lead", order_by="CallAttempt.created_at.desc()")
    tasks = relationship("Task", back_populates="lead")
