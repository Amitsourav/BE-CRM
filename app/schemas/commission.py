from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class DisbursementCreate(BaseModel):
    """Add a tranche to an existing (lead, lender) file.

    Used for the SECOND and later tranches. The first one is recorded
    automatically when the file is marked disbursed, so this is the
    "another instalment came out" path.
    """
    # In LAKHS, like every other loan figure typed in this CRM. Converted
    # to rupees server-side.
    disbursed_amount_lakh: Decimal = Field(gt=0)
    disbursed_on: date
    # The lender's own payout reference. Worth capturing — it is what
    # settles an argument about whether a file was ever paid.
    utr_reference: str | None = Field(default=None, max_length=100)
    # Overrides the lender's configured rate for this tranche only, for
    # files negotiated separately. Omit to use the lender's rate.
    commission_rate: Decimal | None = Field(default=None, ge=0, le=100)
    notes: str | None = None

    model_config = {"extra": "forbid"}


class DisbursementUpdate(BaseModel):
    """Correct a disbursement, or record what the lender actually paid."""
    disbursed_amount_lakh: Decimal | None = Field(default=None, gt=0)
    disbursed_on: date | None = None
    utr_reference: str | None = Field(default=None, max_length=100)
    # Changing either of these recomputes the commission — they are its
    # inputs, and letting them drift apart from it would make the report
    # state a figure nobody can reproduce.
    commission_rate: Decimal | None = Field(default=None, ge=0, le=100)
    # Sets the commission directly, overriding the percentage. For the
    # cases where a lender settles at an agreed figure instead.
    commission_amount: Decimal | None = Field(default=None, ge=0)
    gst_amount: Decimal | None = Field(default=None, ge=0)
    # Untick to make this tranche earn nothing, whatever the rate says.
    # Mirrors "Eligible for Commission?" in the revenue tracker. Ticking
    # it back on restores the calculated figure.
    earns_commission: bool | None = None

    # ── Payment ────────────────────────────────────────────────────────
    # What actually hit the bank account.
    amount_received: Decimal | None = Field(default=None, ge=0)
    # What the lender withheld as TDS. NOT a shortfall — it discharges
    # the debt just as cash does. Leaving it blank on a real payment is
    # what makes every row look 2-5% short.
    tds_deducted: Decimal | None = Field(default=None, ge=0)
    received_on: date | None = None
    payment_reference: str | None = Field(default=None, max_length=100)

    # Stops chasing this row and takes it out of the outstanding total.
    write_off_reason: str | None = None
    notes: str | None = None

    model_config = {"extra": "forbid"}


class DisbursementOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_bank_id: uuid.UUID
    bank_name: str
    tranche_no: int
    disbursed_amount: Decimal
    # Null on historical rows imported without one. Ageing is
    # unavailable for those; every money figure still counts.
    disbursed_on: date | None = None
    utr_reference: str | None = None
    commission_rate: Decimal
    # False = this tranche earns nothing. The rate is still shown, so the
    # report can say what it would otherwise have been worth.
    earns_commission: bool = True
    commission_amount: Decimal
    gst_amount: Decimal | None = None
    invoice_id: uuid.UUID | None = None
    amount_received: Decimal | None = None
    tds_deducted: Decimal | None = None
    received_on: date | None = None
    payment_reference: str | None = None
    write_off_reason: str | None = None
    notes: str | None = None
    source: str | None = None
    # Derived, not stored — see the model. to_bill | billed | short_paid
    # | paid | written_off.
    status: str
    shortfall: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReconciliationRow(BaseModel):
    """One line of the reconciliation report.

    Everything except the money the user typed is filled in from what the
    CRM already knew.
    """
    id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None = None
    serial_no: int | None = None
    bank_name: str
    tranche_no: int
    disbursed_amount: Decimal
    disbursed_on: date | None = None
    commission_rate: Decimal
    earns_commission: bool = True
    commission_amount: Decimal
    gst_amount: Decimal | None = None
    invoice_id: uuid.UUID | None = None
    amount_received: Decimal | None = None
    tds_deducted: Decimal | None = None
    received_on: date | None = None
    shortfall: Decimal
    status: str
    # Days since the money left the lender. NULL when the disbursement
    # date is unknown — show "—", never 0.
    days_outstanding: int | None = None
    utr_reference: str | None = None
    source: str | None = None


class ReconciliationTotals(BaseModel):
    """Totals over the WHOLE filtered set, not the current page.

    A page total is the classic wrong answer to "how much are we owed",
    so these are computed by a separate aggregate over the same filters.
    """
    count: int
    disbursed_total: Decimal
    commission_total: Decimal
    gst_total: Decimal
    received_total: Decimal
    tds_total: Decimal
    # commission + GST, less cash received AND less TDS — because TDS was
    # paid to the tax department on our behalf and is not a shortfall.
    outstanding_total: Decimal


class ReconciliationOut(BaseModel):
    items: list[ReconciliationRow]
    totals: ReconciliationTotals
    total: int
    page: int
    page_size: int
    total_pages: int


class LenderSummaryRow(BaseModel):
    """Per-lender rollup — "who owes us what", against "what they promised".

    A lender can appear with sanctioned files and zero disbursements. That
    is the case worth looking at: approved money that has not converted.
    """
    bank_name: str
    # Actual side — from recorded disbursements
    files: int
    disbursed_total: Decimal
    commission_total: Decimal
    received_total: Decimal
    tds_total: Decimal
    gst_total: Decimal = Decimal("0")
    # (commission + GST) - (received + TDS) — same definition as
    # /reconciliation's totals.outstanding_total, so the two agree.
    outstanding_total: Decimal
    unbilled_count: int
    # Theoretical side — from sanctioned files
    sanctioned_files: int = 0
    sanctioned_total: Decimal = Decimal("0")
    gross_theoretical_revenue: Decimal = Decimal("0")
    # How many of this lender's sanctioned files have no amount recorded,
    # and are therefore missing from the figure above.
    files_missing_amount: int = 0


class GrossTheoreticalOut(BaseModel):
    """Gross theoretical revenue, and honestly what it could not count.

    GTR is the lender's rate applied to the amount it SANCTIONED — what
    FMC would earn if every approved loan drew down in full. `revenue` is
    the same rate applied to what was actually DISBURSED.

    The counters are not decoration. A file with no sanctioned amount, or
    whose lender has no rate configured, is excluded from the sum rather
    than counted as zero — so without them the total reads as complete
    when it is not.
    """
    files: int
    files_counted: int
    files_missing_amount: int
    files_missing_rate: int
    sanctioned_total: Decimal
    gross_theoretical_revenue: Decimal
    # The realistic version: gross x net_theoretical_factor. Gross assumes
    # every sanctioned loan draws down in full, which they do not, so this
    # is the figure to forecast against.
    net_theoretical_factor: Decimal
    net_theoretical_revenue: Decimal
    disbursed_total: Decimal
    revenue: Decimal
    # GTR minus revenue: approved money not yet drawn down. Can go
    # negative if a lender released more than the sanction on file, which
    # is a data problem worth surfacing rather than hiding.
    drawdown_gap: Decimal


class NetTheoreticalFactorIn(BaseModel):
    """The drawdown assumption, as a percentage of gross theoretical.

    One value for all lenders. 80 means "we expect to realise 80% of what
    the sanctions are theoretically worth".
    """
    net_theoretical_factor: Decimal = Field(gt=0, le=100)

    model_config = {"extra": "forbid"}


class NetTheoreticalFactorOut(BaseModel):
    net_theoretical_factor: Decimal
