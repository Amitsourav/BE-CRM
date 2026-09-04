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


# ── Analytics dashboard ────────────────────────────────────────────────
# Money stays Decimal, matching the rest of this module. Counts are int
# and percentages float, matching app/schemas/report.py. Every field has a
# default so a partially-populated panel still validates rather than 500ing
# a whole dashboard over one empty table.


class FunnelOut(BaseModel):
    """Sanctioned -> confirmed -> disbursed -> earned -> collected.

    `confirmed` means the student paid the processing fee: FMC's proof the
    loan is real and which lender won it. A sanction without a PF is an
    offer, so the two are reported separately and never summed.

    Amounts are RUPEES. Percentages are each step against the one before,
    not against the top — that is what makes a weak step visible.
    """
    sanctioned_total: Decimal = Decimal("0")
    sanctioned_files: int = 0
    confirmed_total: Decimal = Decimal("0")
    confirmed_files: int = 0
    disbursed_total: Decimal = Decimal("0")
    tranches: int = 0
    earned_total: Decimal = Decimal("0")
    collected_total: Decimal = Decimal("0")
    outstanding_total: Decimal = Decimal("0")
    confirmed_pct_of_sanctioned: float = 0.0
    disbursed_pct_of_confirmed: float = 0.0
    collected_pct_of_earned: float = 0.0


class PipelineAheadOut(BaseModel):
    """Commission on money already approved but not yet released.

    Education loans draw down semester by semester, so a confirmed file
    keeps earning for years. `future_commission` is a FLOOR, not an
    estimate: files whose lender has no configured rate are excluded
    rather than counted as zero, and `files_missing_rate` says how many.
    """
    confirmed_files: int = 0
    sanctioned_total: Decimal = Decimal("0")
    drawn_total: Decimal = Decimal("0")
    undrawn_total: Decimal = Decimal("0")
    future_commission: Decimal = Decimal("0")
    drawn_pct: float = 0.0
    files_missing_rate: int = 0


class MonthPoint(BaseModel):
    """One month. `earned` is by disbursement date, `collected` by receipt date.

    They are different dates on purpose — a tranche earned in June and
    paid in August appears in both months, in different columns. Months
    with no activity on either side are absent, not zero-filled, and
    tranches with no date appear in NO month (see data_quality).
    """
    month: str = ""          # "YYYY-MM"
    tranches: int = 0
    disbursed: Decimal = Decimal("0")
    earned: Decimal = Decimal("0")
    collected: Decimal = Decimal("0")


class LenderDebtRow(BaseModel):
    """One lender's book. Ordered by what is outstanding, then by size."""
    bank_name: str = ""
    tranches: int = 0
    disbursed_total: Decimal = Decimal("0")
    earned_total: Decimal = Decimal("0")
    collected_total: Decimal = Decimal("0")
    outstanding_total: Decimal = Decimal("0")
    collected_pct: float = 0.0


class AgeingBucket(BaseModel):
    """`bucket` is one of 0_30 | 31_60 | 61_90 | over_90 | no_date."""
    bucket: str = ""
    tranches: int = 0
    outstanding: Decimal = Decimal("0")


class AgeingOut(BaseModel):
    """Outstanding commission by age. Settled rows are excluded entirely.

    `no_date` is a real bucket, not a rounding error: a tranche with no
    disbursement date cannot be aged, and on FMC's book that is the
    majority of everything outstanding. Render it — a panel that hides it
    understates the debt by more than half.

    `total_outstanding` can exceed `funnel.outstanding_total` by a few
    rupees and that is correct, not a bug: this sums per-row shortfall,
    which floors at zero, so a lender that OVERPAID one tranche cannot
    quietly cancel out what it owes on another. The funnel reports the net
    position; this reports what is actually chaseable.
    """
    buckets: list[AgeingBucket] = []
    total_outstanding: Decimal = Decimal("0")
    undateable_outstanding: Decimal = Decimal("0")
    undateable_pct: float = 0.0


class DataQualityOut(BaseModel):
    """Why a figure above might be wrong. Show it; do not tuck it away.

    Three different things, kept apart on purpose:
    `tranches_awaiting_payment` is nothing received at all;
    `tranches_short` is paid but light; and `tranches_materially_short`
    narrows that to over Rs 100 AND over 2% of what is due. The gap
    between the last two is rounding between the lender's arithmetic and
    ours, and it is usually most of the first number — collapsing all
    three into one figure reads as "every lender underpaid us".

    `files_on_aggregator` are files parked on UniCred / Nomad / Axis
    rather than the specific route beneath. An aggregator fronts several
    banks at different rates, so it has none of its own and such a file
    can never earn until it is moved.
    """
    tranches: int = 0
    tranches_without_date: int = 0
    payments_without_receipt_date: int = 0
    tranches_with_tds: int = 0
    tranches_awaiting_payment: int = 0
    tranches_short: int = 0
    tranches_materially_short: int = 0
    tranches_written_off: int = 0
    tranches_earning_nothing: int = 0
    live_files: int = 0
    files_without_sanctioned_amount: int = 0
    files_that_cannot_be_priced: int = 0
    files_on_aggregator: int = 0


class ReconciliationDashboardOut(BaseModel):
    """The whole dashboard in one response.

    Deliberately carries NO invoice figures. invoice_service never touches
    bank_disbursements, so invoice_id is only ever set by a direct write
    and the `billed` status is unreachable through the API — FMC raises
    its bills outside the CRM. An "unbilled" number here would report a
    gap that is really a workflow living elsewhere.
    """
    funnel: FunnelOut
    pipeline_ahead: PipelineAheadOut
    monthly: list[MonthPoint] = []
    by_lender: list[LenderDebtRow] = []
    ageing: AgeingOut
    data_quality: DataQualityOut


# ── Operating layer: pipeline, sources, exceptions, drill-down ─────────


class StageFunnelRow(BaseModel):
    """One pipeline stage, by student count AND by value.

    Grouped by the LEAD's stage, not by lender-file status: the question
    is where a STUDENT sits, and a student with three lender files sits in
    exactly one place.
    """
    stage: str = ""
    leads: int = 0
    sanctioned: Decimal = Decimal("0")
    disbursed: Decimal = Decimal("0")


class RevenueBridgeOut(BaseModel):
    """Commission already booked, against commission still unlockable.

    `unlockable` is a FLOOR: files whose lender has no configured rate are
    excluded rather than counted as zero, and `files_missing_rate` says
    how many. Say "at least" in front of it when that count is non-zero.
    """
    booked: Decimal = Decimal("0")
    unlockable: Decimal = Decimal("0")
    undrawn_total: Decimal = Decimal("0")
    drawn_pct: float = 0.0
    files_missing_rate: int = 0


class OpportunityRow(BaseModel):
    """A confirmed file with money still to draw, ranked by what it is worth.

    `potential_net_revenue` already has the 80% net-theoretical haircut
    applied — it is what FMC realistically keeps, not the gross rate on
    the pending amount.
    """
    lead_id: uuid.UUID
    serial_no: int | None = None
    full_name: str = ""
    stage: str = ""
    bank_name: str = ""
    sanctioned: Decimal = Decimal("0")
    disbursed: Decimal = Decimal("0")
    pending: Decimal = Decimal("0")
    potential_net_revenue: Decimal = Decimal("0")


class PipelineOut(BaseModel):
    stage_funnel: list[StageFunnelRow] = []
    revenue_bridge: RevenueBridgeOut
    opportunities: list[OpportunityRow] = []


class SourceRow(BaseModel):
    """One lead source, measured on money rather than on lead count.

    `students` counts only students who have actually disbursed. Counting
    every lead carrying the source instead makes unattributed read as
    8,651 students earning Rs 83 each — a number nobody can act on.
    """
    source_id: uuid.UUID | None = None
    source_name: str = ""
    students: int = 0
    tranches: int = 0
    disbursed_total: Decimal = Decimal("0")
    commission_total: Decimal = Decimal("0")
    collected_total: Decimal = Decimal("0")
    revenue_per_student: Decimal = Decimal("0")
    collected_pct: float = 0.0
    share_of_disbursed_pct: float = 0.0


class SourcesOut(BaseModel):
    """Attributed channels, ranked — and unattributed, kept apart.

    Unattributed is returned SEPARATELY and deliberately not ranked among
    real channels. On FMC's book it is the single largest bucket (~40% of
    disbursement), and letting it head a league table of marketing
    channels would be actively misleading. Render it as a footer row.
    """
    sources: list[SourceRow] = []
    unattributed: SourceRow | None = None


class ExceptionRow(BaseModel):
    """One record to fix, naming the student it belongs to.

    A single tranche or file can trip more than one rule and each is a
    separate thing to fix, so each becomes its own row. That is why the
    row count per `code` matches the matching counter in `data_quality`.
    """
    severity: str = ""          # high | medium | low
    code: str = ""
    issue: str = ""
    why: str = ""
    lead_id: uuid.UUID
    serial_no: int | None = None
    full_name: str = ""
    bank_name: str | None = None
    amount: Decimal | None = None


class ExceptionsOut(BaseModel):
    total: int = 0
    by_code: dict[str, int] = {}
    items: list[ExceptionRow] = []
    truncated: bool = False


class DrilldownRow(BaseModel):
    lead_id: uuid.UUID
    serial_no: int | None = None
    full_name: str = ""
    stage: str | None = None
    bank_name: str | None = None
    sanctioned: Decimal = Decimal("0")
    disbursed: Decimal = Decimal("0")
    earned: Decimal = Decimal("0")
    collected: Decimal = Decimal("0")
    outstanding: Decimal = Decimal("0")


class DrilldownOut(BaseModel):
    """The students behind any segment of the dashboard.

    TWO counts, because the panels do not all count the same thing:
    `total` is STUDENTS, `tranche_total` is TRANCHES within this segment.
    The ageing and by-lender panels count tranches; the stage funnel counts
    students. Show both — "17 students · 22 tranches" — so the drill-down
    never appears to contradict the segment that was clicked.
    """
    segment: str = ""
    value: str = ""
    total: int = 0
    tranche_total: int = 0
    page: int = 1
    page_size: int = 50
    items: list[DrilldownRow] = []
