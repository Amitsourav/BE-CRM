from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, ForeignKey, PrimaryKeyConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class InvoiceCounter(Base):
    """Atomic per-(company, FY) sequence counter for invoice numbering.

    Composite PK (company_id, financial_year) gives us:
      • one row per tenant per fiscal year — automatic April rollover
        without manual reset code; first invoice of new FY creates a
        new row at next_number=1
      • row-level locking via the ON CONFLICT DO UPDATE pattern so
        concurrent invoice creates can't collide on numbering

    Mirrors `company_lead_counters` which serves the lead serial number
    feature (see `LeadService._reserve_serial_numbers`).
    """
    __tablename__ = "invoice_counters"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "financial_year", name="invoice_counters_pkey"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
