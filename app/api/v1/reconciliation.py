"""Commission reconciliation — what lenders owe us, and what has arrived.

Answers the three questions that find money:
  1. which disbursements were never billed  (status=to_bill)
  2. which bills were never paid            (status=billed)
  3. where the lender paid less than it owed (status=short_paid)

Admin-only throughout, matching the invoice module: this is the money
surface, and the same people who raise the bills reconcile them.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_admin
from app.core.tenant import get_current_company_id
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.profile import Profile
from app.models.lead_bank import LeadBank
from app.services.commission_service import CommissionService
from app.services.commission_analytics_service import (
    CommissionAnalyticsService, Filters,
)
from app.schemas.commission import (
    ReconciliationDashboardOut,
    PipelineOut, SourcesOut, ExceptionsOut, DrilldownOut,
    DisbursementCreate, DisbursementUpdate, DisbursementOut,
    ReconciliationOut, LenderSummaryRow, GrossTheoreticalOut,
    NetTheoreticalFactorIn, NetTheoreticalFactorOut,
)

router = APIRouter(prefix="/reconciliation", tags=["Commission"])


async def _require_fmc(db: AsyncSession, company_id: uuid.UUID) -> None:
    """Admitverse has no lenders, so it has no commission to reconcile.

    Same gate the bank features already use — stated here rather than
    imported so this module doesn't depend on the leads router.
    """
    from app.models.company import Company
    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one_or_none()
    if (slug or "").lower() == "admitverse":
        raise BadRequestError(
            "Commission reconciliation is a lender feature and is not "
            "available for this tenant."
        )


# ─────────────────────────────────────────────
# The report
# ─────────────────────────────────────────────

def _filters(
    bank_name: list[str] | None = Query(
        None, description="Repeatable; matches any of the supplied lenders",
    ),
    source_id: list[uuid.UUID] | None = Query(
        None, description="Repeatable; matches any of the supplied lead sources",
    ),
    disbursed_from: date | None = Query(None),
    disbursed_to: date | None = Query(None),
) -> Filters:
    """The one filter set every analytics panel honours.

    A FastAPI dependency rather than five repeated params per route, so a
    filter cannot come to mean different things on different tabs. Passing
    none of them means the whole book, which is what every panel returned
    before filters existed.
    """
    return Filters(
        bank_name=bank_name or [],
        source_id=source_id or [],
        disbursed_from=disbursed_from,
        disbursed_to=disbursed_to,
    )


@router.get("", response_model=ReconciliationOut)
async def reconciliation(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    bank_name: list[str] | None = Query(
        None, description="Repeatable; matches any of the supplied lenders",
    ),
    status: list[str] | None = Query(
        None,
        description=(
            "Repeatable. to_bill | billed | short_paid | paid | written_off. "
            "to_bill = disbursed but never invoiced; billed = invoiced, "
            "nothing received; short_paid = they paid less than they owed."
        ),
    ),
    disbursed_from: date | None = Query(None),
    disbursed_to: date | None = Query(None),
    q: str | None = Query(None, description="Search the student's name"),
):
    """Every disbursement with what it earned and what came back.

    `totals` covers the whole filtered set, not the page — a page total
    is the wrong answer to "how much are we owed".
    """
    await _require_fmc(db, company_id)
    return await CommissionService(db, company_id).reconciliation(
        page=page, page_size=page_size, bank_name=bank_name, status=status,
        disbursed_from=disbursed_from, disbursed_to=disbursed_to, q=q,
    )


@router.get("/summary", response_model=list[LenderSummaryRow])
async def summary(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """One row per lender: disbursed, earned, received, still owed.

    The "who do we chase this month" view. Ordered by commission earned.
    """
    await _require_fmc(db, company_id)
    return await CommissionService(db, company_id).summary()


@router.get("/theoretical", response_model=GrossTheoreticalOut)
async def gross_theoretical(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Gross theoretical revenue against actual revenue.

    Theoretical is the lender's rate on what it SANCTIONED — what FMC
    would earn if every approved loan drew down in full. Revenue is the
    same rate on what was actually DISBURSED. `drawdown_gap` is the
    difference: approved money that has not converted.

    Read `files_missing_amount` and `files_missing_rate` before quoting
    the total anywhere. Files they count are excluded from the sum, not
    counted as zero, so the figure is a floor rather than a full picture
    until they reach nil.
    """
    await _require_fmc(db, company_id)
    return await CommissionService(db, company_id).revenue_vs_theoretical()


@router.get("/dashboard", response_model=ReconciliationDashboardOut)
async def dashboard(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    months: int = Query(
        12, ge=1, le=24,
        description="How many months of the earned-vs-collected trend to return.",
    ),
    f: Filters = Depends(_filters),
):
    """The commission book at a glance — six panels, one round trip.

    funnel          approved -> PF confirmed -> disbursed -> earned -> collected
    pipeline_ahead  commission on money approved but not yet released
    monthly         earned by disbursement month vs collected by receipt month
    by_lender       who owes what, biggest debt first
    ageing          outstanding by age, with an explicit no-date bucket
    data_quality    the counters that say why a figure might be wrong

    No filters: every panel is the whole book by definition. Use
    `GET /reconciliation` when you want to slice.

    Read `data_quality` before quoting anything. Tranches with no
    disbursement date sit in no month and in the `no_date` ageing bucket,
    and on a book where that is most of the outstanding balance the trend
    line alone will mislead.

    Carries no invoice figures deliberately — see the response schema.
    """
    await _require_fmc(db, company_id)
    return await CommissionAnalyticsService(db, company_id).dashboard(months, f)


@router.get("/pipeline", response_model=PipelineOut)
async def pipeline(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    f: Filters = Depends(_filters),
    limit: int = Query(20, ge=1, le=100, description="Rows in the opportunities queue."),
):
    """Where files are stuck, what is still unlockable, and what to chase.

    `stage_funnel` counts STUDENTS by their lead stage and carries the
    value at each step, so a stalled stage shows up as money rather than
    as a headcount.

    `revenue_bridge` is commission already booked against commission still
    unlockable on approved-but-undrawn money. The unlockable side is a
    floor — files whose lender has no rate are excluded, not zeroed.

    `opportunities` ranks confirmed files by what their pending drawdown
    is worth after the 80% net haircut. This is the queue to work.
    """
    await _require_fmc(db, company_id)
    return await CommissionAnalyticsService(db, company_id).pipeline(f, limit)


@router.get("/sources", response_model=SourcesOut)
async def sources(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    f: Filters = Depends(_filters),
):
    """Which channels create revenue, measured on money not lead count.

    `students` counts only students who have actually disbursed.

    Unattributed comes back in its own field rather than inside `sources`.
    It is the largest single bucket on this book, and ranking it among
    real marketing channels would be misleading — render it as a footer
    row, not a winner.
    """
    await _require_fmc(db, company_id)
    return await CommissionAnalyticsService(db, company_id).sources(f)


@router.get("/exceptions", response_model=ExceptionsOut)
async def exceptions(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    f: Filters = Depends(_filters),
    limit: int = Query(200, ge=1, le=1000),
):
    """Records to fix, each naming the student it belongs to.

    `data_quality` on the dashboard counts these; this lists them so
    somebody can act. Sorted by severity then by the money at stake.

    One record can trip several rules and each is a separate fix, so each
    becomes its own row — which is why the count per `code` matches the
    matching counter in `data_quality`.
    """
    await _require_fmc(db, company_id)
    return await CommissionAnalyticsService(db, company_id).exceptions(f, limit)


@router.get("/drilldown", response_model=DrilldownOut)
async def drilldown(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    segment: str = Query(
        ...,
        description="stage | lender | ageing_bucket | source | funnel_step",
    ),
    value: str = Query(..., description="The segment's value, e.g. 'UC Axis' or 'over_90'."),
    f: Filters = Depends(_filters),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """The students behind any segment on the dashboard.

    One endpoint for every panel: the answer has the same shape whichever
    segment was clicked, and a drill-down per panel would drift the way
    the outstanding formula once did.

    Returns TWO counts. `total` is students; `tranche_total` is tranches
    within this segment. The ageing and by-lender panels count tranches
    while the stage funnel counts students, so showing both is what stops
    a drill-down appearing to contradict the number that was clicked.

    `value` for a source is the source id, or the literal `unattributed`.
    """
    await _require_fmc(db, company_id)
    return await CommissionAnalyticsService(db, company_id).drilldown(
        segment, value, f, page, page_size,
    )


@router.get("/settings", response_model=NetTheoreticalFactorOut)
async def get_settings(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """The drawdown assumption behind net theoretical revenue."""
    await _require_fmc(db, company_id)
    factor = await CommissionService(db, company_id).net_theoretical_factor()
    return {"net_theoretical_factor": factor}


@router.patch("/settings", response_model=NetTheoreticalFactorOut)
async def update_settings(
    body: NetTheoreticalFactorIn,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Change the % of gross theoretical revenue expected to be realised.

    Its own endpoint rather than a field on PUT /invoices/settings, which
    is a full upsert — changing one assumption should not mean re-sending
    the company's legal name, GSTIN and bank details.

    Takes effect immediately and applies to every figure, historical
    included: it is an assumption about the future, not a record of what
    happened, so there is nothing to snapshot.
    """
    await _require_fmc(db, company_id)
    factor = await CommissionService(db, company_id).set_net_theoretical_factor(
        body.net_theoretical_factor
    )
    return {"net_theoretical_factor": factor}


# ─────────────────────────────────────────────
# Individual disbursements
# ─────────────────────────────────────────────

@router.get("/disbursements/{disbursement_id}", response_model=DisbursementOut)
async def get_disbursement(
    disbursement_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    await _require_fmc(db, company_id)
    return await CommissionService(db, company_id).get(disbursement_id)


@router.patch("/disbursements/{disbursement_id}", response_model=DisbursementOut)
async def update_disbursement(
    disbursement_id: uuid.UUID,
    body: DisbursementUpdate,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Record a payment, correct a figure, or write the row off.

    Recording a payment means sending `amount_received` AND
    `tds_deducted` together. Sending only the cash makes the row look
    short by whatever the lender withheld, which is the single most
    common way this kind of report fills up with false shortfalls.
    """
    await _require_fmc(db, company_id)
    return await CommissionService(db, company_id).update(
        disbursement_id, body.model_dump(exclude_unset=True), admin,
    )


@router.delete("/disbursements/{disbursement_id}")
async def delete_disbursement(
    disbursement_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a disbursement entered by mistake.

    Refused once a bill claims it — an invoice pointing at nothing is
    worse than a wrong row, and invoice numbers are permanent.
    """
    await _require_fmc(db, company_id)
    await CommissionService(db, company_id).delete(disbursement_id)
    return {"message": "Disbursement deleted"}


# ─────────────────────────────────────────────
# Tranches on one (lead, lender) file
# ─────────────────────────────────────────────

lead_router = APIRouter(prefix="/leads", tags=["Commission"])


async def _entry(db: AsyncSession, company_id, lead_id, entry_id) -> LeadBank:
    entry = (await db.execute(
        select(LeadBank).where(
            LeadBank.id == entry_id,
            LeadBank.lead_id == lead_id,
            LeadBank.company_id == company_id,
        )
    )).scalar_one_or_none()
    if not entry:
        raise NotFoundError("Bank entry not found")
    return entry


@lead_router.get(
    "/{lead_id}/banks/{entry_id}/disbursements",
    response_model=list[DisbursementOut],
)
async def list_disbursements(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Every tranche this lender has released on this file, in order."""
    await _require_fmc(db, company_id)
    await _entry(db, company_id, lead_id, entry_id)
    return await CommissionService(db, company_id).list_for_entry(entry_id)


@lead_router.post(
    "/{lead_id}/banks/{entry_id}/disbursements",
    response_model=DisbursementOut, status_code=201,
)
async def add_disbursement(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: DisbursementCreate,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Record another tranche on a file that has already disbursed once.

    The FIRST tranche is captured automatically when the file is marked
    `disbursed`, so this endpoint is for the instalments that follow.
    `tranche_no` is assigned automatically.
    """
    await _require_fmc(db, company_id)
    entry = await _entry(db, company_id, lead_id, entry_id)

    from decimal import Decimal
    from app.core.constants import LAKH_IN_RUPEES

    data = body.model_dump(exclude_unset=True)
    amount = (
        Decimal(data["disbursed_amount_lakh"]) * LAKH_IN_RUPEES
    ).quantize(Decimal("0.01"))

    return await CommissionService(db, company_id).record_disbursement(
        entry=entry,
        disbursed_amount=amount,
        disbursed_on=data["disbursed_on"],
        user=admin,
        rate_override=data.get("commission_rate"),
        utr_reference=data.get("utr_reference"),
        notes=data.get("notes"),
        source="manual",
        commit=True,
    )
