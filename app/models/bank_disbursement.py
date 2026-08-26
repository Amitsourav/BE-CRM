from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date, Integer, Numeric, String, Text, ForeignKey, Index, UniqueConstraint,
    case, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, TimestampMixin


# Anything within a rupee of settled counts as settled. TDS and GST both
# round to the nearest rupee at the lender's end, so an exact-equality
# test would leave a permanent tail of rows short by paise that nobody
# can ever close.
SETTLEMENT_TOLERANCE = Decimal("1.00")


class BankDisbursement(Base, TimestampMixin):
    """One release of money by one lender against one lead's file.

    This is the unit FMC actually earns on: commission is a percentage of
    what a lender DISBURSES, not of what it sanctions. A loan can be
    released in tranches, so a single (lead, lender) file can produce
    several of these rows over years and a separate bill for each.

    It exists because nothing in the CRM recorded the money. `lead_banks`
    knew a file had reached `disbursed`, but not how much left the bank
    and not when, and there was no commission concept anywhere in the
    schema. As of 2026-08-26 that meant 78 leads sat at the `disbursed`
    stage with 17 amounts and ZERO dates between them, while the real
    commission ledger lived on a spreadsheet outside the system.

    `lead_banks` is the parent — the relationship with that lender. This
    is what came of it.
    """

    __tablename__ = "bank_disbursements"
    __table_args__ = (
        # Blocks the same tranche being entered twice, which is the
        # obvious way for a commission figure to silently double.
        UniqueConstraint("lead_bank_id", "tranche_no", name="uniq_disbursement_tranche"),
        Index("ix_bank_disbursements_company_date", "company_id", "disbursed_on"),
        Index("ix_bank_disbursements_invoice", "company_id", "invoice_id"),
        Index("ix_bank_disbursements_lead_bank", "lead_bank_id"),
        # The query that finds money: disbursed, never billed.
        Index(
            "ix_bank_disbursements_unbilled", "company_id",
            postgresql_where=text("invoice_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    lead_bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lead_banks.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from lead_banks. The reconciliation report is a
    # lead-centric list filtered by lead and counsellor; carrying lead_id
    # here avoids joining back through lead_banks on every row of it.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    # Snapshot, matching how lead_banks stores the lender as text rather
    # than an FK. Renaming a lender must not silently re-attribute money
    # already earned under the old name.
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── What actually happened ─────────────────────────────────────────
    # RUPEES, like lead_banks.loan_amount. Users type lakhs; the API
    # converts via LAKH_IN_RUPEES so the two money columns can never
    # disagree about their unit.
    disbursed_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    disbursed_on: Mapped[date] = mapped_column(Date, nullable=False)
    tranche_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # The lender's own payout reference, when they give one. The single
    # most useful field when arguing about whether a file was ever paid.
    utr_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── What we are owed ───────────────────────────────────────────────
    # SNAPSHOT of the rate that applied to THIS disbursement, never looked
    # up at read time. banks.commission_rate is only the current default:
    # moving Axis from 1.5% to 1.75% must not rewrite what was owed on
    # files disbursed under the old deal. For the same reason a backfilled
    # historical tranche must be given the rate that was in force on its
    # disbursed_on, not today's.
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    # disbursed_amount * commission_rate / 100, rounded to paise. Stored
    # rather than computed so a figure can be corrected by hand when a
    # lender settles something off-formula.
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # GST charged on top, filled in when the bill is raised (the rate
    # lives in invoice_settings, so it isn't known until then). Part of
    # what the lender owes, so it belongs in the settlement sum.
    gst_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)

    # ── Billing and settlement ─────────────────────────────────────────
    # Which bill claims this. Many-to-one: one monthly invoice can cover
    # fifty disbursements, which is how some lenders want to be billed.
    # An FK rather than a line_items entry because invoices.line_items is
    # JSONB that the model documents as never queried — reconciliation
    # truth cannot live somewhere unaggregatable.
    invoice_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True
    )
    # What actually hit the bank account.
    amount_received: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    # What the lender withheld as TDS (s.194H). NOT a shortfall — it is
    # money paid to the tax department on our behalf and reclaimed against
    # our own liability. Without this column every single receipt looks
    # 2-5% short and the shortfall figure becomes noise nobody reads,
    # which defeats the entire point of the report.
    tds_deducted: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), nullable=True)
    received_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Set when we stop chasing. A reason rather than a boolean, so the
    # report can say why and so writing off is a deliberate act instead
    # of a row quietly disappearing from the outstanding list.
    write_off_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # manual | stage_machine | bank_grid | backfill. Free string rather
    # than an enum, same reasoning as lead_banks.source. Matters because
    # backfilled rows carry guessed figures and must stay distinguishable
    # from captured ones.
    source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )

    lead_bank = relationship("LeadBank", backref="disbursements")

    # ── Derived state ──────────────────────────────────────────────────
    # A hybrid rather than a stored column: the amounts are the source of
    # truth, and a stored status drifts the moment one is corrected. A
    # hybrid keeps a single definition that is ALSO usable in SQL, so the
    # report can still filter, sort and aggregate on it server-side
    # instead of paginating in Python.

    @hybrid_property
    def total_due(self) -> Decimal:
        """Commission plus the GST we have to charge on it."""
        return (self.commission_amount or Decimal("0")) + (self.gst_amount or Decimal("0"))

    @total_due.expression
    def total_due(cls):
        return func.coalesce(cls.commission_amount, 0) + func.coalesce(cls.gst_amount, 0)

    @hybrid_property
    def total_settled(self) -> Decimal:
        """Cash received plus TDS withheld — both discharge the debt."""
        return (self.amount_received or Decimal("0")) + (self.tds_deducted or Decimal("0"))

    @total_settled.expression
    def total_settled(cls):
        return func.coalesce(cls.amount_received, 0) + func.coalesce(cls.tds_deducted, 0)

    @hybrid_property
    def shortfall(self) -> Decimal:
        """Still owed on this row. Never negative."""
        gap = self.total_due - self.total_settled
        return gap if gap > 0 else Decimal("0")

    @shortfall.expression
    def shortfall(cls):
        gap = cls.total_due - cls.total_settled
        return case((gap > 0, gap), else_=0)

    @hybrid_property
    def status(self) -> str:
        """Where this row sits between "earned" and "settled".

        Settlement is checked BEFORE billing on purpose. Money that has
        arrived is not money to chase, even against a row nobody got
        round to invoicing — reporting that as `to_bill` would send
        someone after a lender that has already paid. Bills that were
        never raised still surface: they are the rows with no payment,
        which fall through to `to_bill` exactly as before.
        """
        if self.write_off_reason:
            return "written_off"
        if self.amount_received is None and self.tds_deducted is None:
            return "billed" if self.invoice_id is not None else "to_bill"
        if self.total_settled < self.total_due - SETTLEMENT_TOLERANCE:
            return "short_paid"
        return "paid"

    @status.expression
    def status(cls):
        nothing_received = cls.amount_received.is_(None) & cls.tds_deducted.is_(None)
        return case(
            (cls.write_off_reason.isnot(None), "written_off"),
            (nothing_received & cls.invoice_id.isnot(None), "billed"),
            (nothing_received, "to_bill"),
            (
                cls.total_settled < cls.total_due - SETTLEMENT_TOLERANCE,
                "short_paid",
            ),
            else_="paid",
        )
