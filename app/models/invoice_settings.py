from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Text, DateTime, Numeric, ForeignKey, CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class InvoiceSettings(Base):
    """One row per tenant — the company info printed on every invoice
    (legal name, GSTIN, address, logo, signature, bank). PK = company_id
    enforces the one-row-per-company invariant without an extra unique
    constraint.

    GSTIN's first 2 characters are the state code; we store both `gstin`
    and a derived `state_code` so the tax-math service doesn't have to
    substring at runtime. The service layer keeps them in sync on PUT.
    """
    __tablename__ = "invoice_settings"
    __table_args__ = (
        CheckConstraint("char_length(gstin) = 15", name="invoice_settings_gstin_len_chk"),
        CheckConstraint("char_length(state_code) = 2", name="invoice_settings_state_code_len_chk"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    pan: Mapped[str] = mapped_column(String(10), nullable=False)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    state_name: Mapped[str] = mapped_column(String(50), nullable=False)
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    pincode: Mapped[str] = mapped_column(String(10), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bank_account_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    bank_account_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(11), nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bank_branch: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    signature_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invoice_prefix: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'FMC'"))
    default_tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, server_default=text("18.00"))
    default_terms: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
