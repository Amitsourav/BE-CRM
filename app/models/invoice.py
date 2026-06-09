from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict
from sqlalchemy import String, Integer, Text, Date, DateTime, Numeric, ForeignKey, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class Invoice(Base):
    """One row per issued invoice.

    Customer details are snapshotted at issue time (not FK'd to leads
    or some "customers" table) so the invoice remains immutable even
    if the underlying lead later updates their phone, address, etc.
    GST audit trail demands that the printed document and the stored
    record stay perfectly aligned.

    Line items live in JSONB rather than a child table — they're never
    queried/aggregated independently, only rendered alongside the
    invoice, so the JSONB approach saves a join, a migration, and an
    API shape.

    Money totals live in dedicated Numeric(14,2) columns so all
    Decimal arithmetic is precise. The per-line `amount` inside JSONB
    is for display only; canonical totals are the columns.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("status IN ('draft','issued','paid','void')", name="invoices_status_chk"),
        CheckConstraint("tax_split IN ('cgst_sgst','igst')", name="invoices_tax_split_chk"),
        Index("uniq_invoices_number_per_company", "company_id", "invoice_number", unique=True),
        Index("uniq_invoices_seq_per_fy", "company_id", "financial_year", "sequence_number", unique=True),
        Index("idx_invoices_company_date", "company_id", text("invoice_date DESC")),
        Index("idx_invoices_company_status", "company_id", "status"),
        Index("idx_invoices_lead", "lead_id", postgresql_where=text("lead_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(7), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'issued'"))

    # Customer snapshot
    customer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    customer_state_code: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    customer_state_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    customer_email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    customer_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Money totals
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cgst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    sgst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    igst_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    total_tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    tax_split: Mapped[str] = mapped_column(String(10), nullable=False)

    # Line items + audit
    line_items: Mapped[List[Dict]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    pdf_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pdf_storage_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
