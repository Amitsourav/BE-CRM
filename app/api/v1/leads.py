from __future__ import annotations

import uuid
from datetime import date
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user, get_current_admin, get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.models.lead_source import LeadSource
from app.services.lead_service import LeadService
from app.schemas.lead import (
    LeadCreate, LeadUpdate, LeadOut, LeadAssign, LeadBulkAssign,
    LeadSearchParams, LeadSourceCreate, LeadSourceOut,
    LeadCardOut, LeadsByStageOut,
    LeadDistributeRangeRequest, LeadDistributeRangeResponse,
    LeadImportantToggle, LeadRemarkCreate, LeadRemarkOut,
    LeadBankCreate, LeadBankUpdate, LeadBankOut,
    LeadApplicationCreate, LeadApplicationUpdate, LeadApplicationOut,
    LeadReassign,
    BankCreate, BankUpdate, BankOut,
    LeadPipelineMove,
    BankShareCreate, BankShareOut, BankShareDetailOut,
    BankMessageCreate, BankMessageOut, BankShareGridOut,
)
from app.schemas.stage import StageLogOut
from app.schemas.call import CallAttemptOut
from app.schemas.task import TaskOut
from app.schemas.common import PaginatedResponse
from app.core.constants import UserRole
from app.core.exceptions import BadRequestError, NotFoundError

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["Leads"])


async def _company_slug(db: AsyncSession, company_id: uuid.UUID) -> str:
    """Resolve the tenant's brand slug (lowercased) for brand-gating."""
    from app.models.company import Company
    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one_or_none()
    return (slug or "").lower()


@router.get("", response_model=PaginatedResponse[LeadOut])
async def list_leads(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    stage: str | None = Query(None, alias="current_stage"),
    agent_id: uuid.UUID | None = Query(None),
    source_id: uuid.UUID | None = Query(None),
    csv_import_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    lead_segment: str | None = Query(
        None,
        regex="^(campaign|normal|unassigned|counsellor|pre_counsellor)$",
        description="Admin-only slice: campaign (AI-calling leads) | normal (never in a campaign) | unassigned | counsellor | pre_counsellor.",
    ),
):
    service = LeadService(db, company_id)
    return await service.list_leads(
        user=current_user, page=page, page_size=page_size,
        stage=stage, agent_id=agent_id, source_id=source_id,
        csv_import_id=csv_import_id, campaign_id=campaign_id,
        date_from=date_from, date_to=date_to,
        lead_segment=lead_segment,
    )


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(
    body: LeadCreate,
    # Single-lead create is open to any authenticated user — including
    # Pre-Counsellors who occasionally need to enter a walk-in / phone-in
    # lead they personally got. CSV bulk-import stays gated to Manager+/Admin
    # so it can't be used to mass-inject leads outside the source pipeline.
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    data = body.model_dump(exclude_unset=True)
    return await service.create_lead(data, current_user.id, creator_role=current_user.role)


@router.get("/by-stage", response_model=LeadsByStageOut)
async def list_leads_by_stage(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    agent_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    per_stage_limit: int = Query(50, ge=1, le=200),
    # FMC pipeline filter set. Every filter is optional; if all are None
    # the endpoint behaves exactly as before. Filters apply to BOTH the
    # card list and the per-column counters so the Kanban stays self-
    # consistent.
    q: str | None = Query(None, description="Search name/phone/email (ILIKE)"),
    source_id: uuid.UUID | None = Query(None),
    loan_min: float | None = Query(None, ge=0, description="Min loan amount in lakhs"),
    loan_max: float | None = Query(None, ge=0, description="Max loan amount in lakhs"),
    bank_name: str | None = Query(None, description="Exact bank name (use FMC_BANKS values)"),
    bank_status: str | None = Query(None, description="applied/sanctioned/disbursed/etc."),
    target_country: str | None = Query(None, description="Preferred study destination"),
    target_intake: str | None = Query(None, description="e.g. Jan-2026, Sep-2026"),
    tags: list[str] | None = Query(None, description="Repeatable; matches any of the supplied tags"),
    created_from: date | None = Query(None),
    created_to: date | None = Query(None),
    due_from: date | None = Query(None, description="Follow-up date range start (e.g. today's callbacks)"),
    due_to: date | None = Query(None),
    dnp_min: int | None = Query(None, ge=0),
    dnp_max: int | None = Query(None, ge=0),
    # Admitverse-only filters. Ignored on FMC. application_status/university
    # filter the per-university application data; budget_* filter the parsed
    # numeric budget within a currency.
    application_status: str | None = Query(None, description="AV: filter by a university-application status"),
    university: str | None = Query(None, description="AV: ILIKE match on primary_university"),
    budget_min: float | None = Query(None, ge=0, description="AV: min budget (in budget_currency units)"),
    budget_max: float | None = Query(None, ge=0, description="AV: max budget (in budget_currency units)"),
    budget_currency: str = Query("INR", description="AV: currency the budget_min/max are expressed in"),
    important_only: bool = Query(False, description="Only starred leads"),
    lead_segment: str | None = Query(
        None,
        regex="^(campaign|normal|unassigned|counsellor|pre_counsellor)$",
        description="Admin-only slice: campaign (AI-calling leads) | normal (never in a campaign) | unassigned | counsellor | pre_counsellor. FE should hide this dropdown for non-admin roles since restricted-view roles already only see their own leads.",
    ),
    pipeline: str | None = Query(
        None,
        regex="^(ai|normal)$",
        description=(
            "Which board to render. 'ai' = leads worked by AI campaigns "
            "(short stage set: created/contacted/dnp/qualified/lost). "
            "'normal' = leads worked by counsellors (full FMC funnel). "
            "Omit for both, which is the pre-Aug-2026 behaviour. The "
            "response's `stages` array is this board's column order — "
            "render from it rather than hard-coding."
        ),
    ),
    sort_by: str = Query(
        "created_desc",
        regex="^(created_desc|loan_asc|loan_desc|budget_asc|budget_desc)$",
        description="Per-column row order: created_desc (default), loan_asc/desc (FMC), budget_asc/desc (AV). Leads without the sort value are placed at the end.",
    ),
    stage: str | None = Query(
        None,
        description=(
            "Restrict the response to ONE column. Powers the board's "
            "'Show more' button: pair with `offset` to fetch the next page "
            "of a single stage instead of refetching every column. The "
            "response keeps the same shape, with one entry in "
            "`items_by_stage` and `stages`. Omit for the whole board."
        ),
    ),
    offset: int = Query(
        0, ge=0,
        description=(
            "Skip this many cards within `stage` before returning "
            "`per_stage_limit` more. Requires `stage`. Resend every filter "
            "the board currently has applied, or page 2 will not match "
            "page 1."
        ),
    ),
):
    """Kanban board endpoint — returns all leads grouped by stage in one
    round trip (replaces 19 per-column requests for Admitverse, 6 for FMC).

    Pass `stage` + `offset` to page a single column instead ("Show more").
    """
    # offset without stage would page every column at once, which no
    # caller wants and which silently hides the first N cards of each.
    # Rejected loudly rather than guessing which column was meant.
    from app.core.constants import get_stages_for_pipeline
    if offset and not stage:
        raise BadRequestError("offset requires stage")
    # A stage that isn't on this board returns an empty column rather than
    # an error — but a typo would then look like "no leads here" forever,
    # so it's validated against the board the caller actually asked for.
    if stage:
        slug = await _company_slug(db, company_id)
        valid = {st.value for st in get_stages_for_pipeline(pipeline, slug)}
        if stage not in valid:
            raise BadRequestError(
                f"unknown stage '{stage}' for this board; expected one of "
                f"{sorted(valid)}"
            )
    service = LeadService(db, company_id)
    data = await service.list_leads_by_stage(
        user=current_user, agent_id=agent_id, campaign_id=campaign_id,
        per_stage_limit=per_stage_limit,
        q=q, source_id=source_id,
        loan_min=loan_min, loan_max=loan_max,
        bank_name=bank_name, bank_status=bank_status,
        target_country=target_country, target_intake=target_intake,
        tags=tags,
        created_from=created_from, created_to=created_to,
        due_from=due_from, due_to=due_to,
        dnp_min=dnp_min, dnp_max=dnp_max,
        application_status=application_status, university=university,
        budget_min=budget_min, budget_max=budget_max, budget_currency=budget_currency,
        important_only=important_only,
        lead_segment=lead_segment,
        pipeline=pipeline,
        sort_by=sort_by,
        stage=stage,
        offset=offset,
    )
    return {
        "stages": data.get("stages", []),
        "pipeline": data.get("pipeline"),
        "items_by_stage": {
            # Loop var deliberately not named `stage` — that's the
            # single-column query param in this scope now, and shadowing
            # it here is how the plumbing above went missing unnoticed.
            st: [LeadCardOut.model_validate(lead) for lead in leads]
            for st, leads in data["items_by_stage"].items()
        },
        "counts_by_stage": data["counts_by_stage"],
        "total": data["total"],
    }


@router.get("/lost-reasons", response_model=list[str])
async def list_lost_reasons(
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Brand-scoped dropdown for the "Move to Lost" modal. FMC returns
    its locked 21-value list (backend enforces membership). Admitverse
    returns [] — FE should render a free-text field when the list is
    empty, since AV doesn't have a canonical reason list yet.
    """
    from app.models.company import Company
    from app.core.constants import get_lost_reasons_for_brand
    slug = (await db.execute(
        select(Company.slug).where(Company.id == company_id)
    )).scalar_one_or_none()
    reasons = get_lost_reasons_for_brand(slug)
    return list(reasons) if reasons else []


@router.get("/bank-statuses", response_model=list[dict], tags=["Bank Shares"])
async def list_bank_statuses(
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Options for the per-bank status dropdown in the bank-share grid.

    Returned as [{"value": "loan_login", "label": "Login"}, …] so the
    wire value and the words on screen are decided in one place instead
    of the frontend inventing its own labels for the enum.

    This is the OFFERED set, not the accepted set: 'docs_reviewed' and
    'under_review' are still valid and still stored on 6 existing rows,
    they are simply no longer offered. A cell holding one of them must
    still render its own value — treat this list as additions to
    whatever the cell already has, not as a whitelist.

    Per-bank and per-bank only: this status describes one lender's
    decision about one file. It does not move the lead's own
    current_stage, and 'lost' here means that lender declined, not that
    the lead is lost.
    """
    from app.core.constants import BANK_STATUS_OPTIONS
    if await _company_slug(db, company_id) == "admitverse":
        return []
    return [{"value": v, "label": lbl} for v, lbl in BANK_STATUS_OPTIONS]


@router.get("/banks", response_model=list[str])
async def list_banks(
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Canonical FMC bank dropdown for the Kanban-card bank_name field
    and the lead edit form. Locked list — backend rejects any bank_name
    not in here on lead update. Admitverse has no banks → returns [].
    """
    from app.services.bank_registry import get_bank_names
    if await _company_slug(db, company_id) == "admitverse":
        return []
    return list(await get_bank_names(db))


@router.get("/banks/manage", response_model=list[BankOut], tags=["Bank List"])
async def list_banks_for_management(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(True),
):
    """The lender list with ids and usage counts, for administering it.

    `GET /leads/banks` stays the plain list of selectable names that the
    dropdown and integrations read.
    """
    from sqlalchemy import func as sa_func
    from app.models.bank import Bank
    from app.models.lead_bank import LeadBank

    query = select(Bank).order_by(Bank.sort_order, Bank.name)
    if not include_inactive:
        query = query.where(Bank.is_active == True)  # noqa: E712
    rows = (await db.execute(query)).scalars().all()

    counts = dict((await db.execute(
        select(LeadBank.bank_name, sa_func.count())
        .where(LeadBank.company_id == company_id)
        .group_by(LeadBank.bank_name)
    )).all())
    return [
        {
            "id": b.id, "name": b.name, "is_active": b.is_active,
            "sort_order": b.sort_order, "usage_count": counts.get(b.name, 0),
            "commission_rate": b.commission_rate,
            "is_aggregator": b.is_aggregator,
            "partner_code": b.partner_code,
            "created_at": b.created_at, "updated_at": b.updated_at,
        }
        for b in rows
    ]


@router.post("/banks", response_model=BankOut, status_code=201, tags=["Bank List"])
async def add_bank_to_list(
    body: BankCreate,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a lender to the canonical list. Admin only.

    Takes effect within 60 seconds everywhere — the dropdown, bank_name
    validation on leads and shares, and the grid's columns all read this
    list. No deploy needed.

    Still a controlled vocabulary. A name that already exists in ANY
    casing is rejected: "gyandhan" cannot be added alongside "GyanDhan",
    which is the drift this list exists to prevent. Type the lender's own
    branded spelling.
    """
    from sqlalchemy import func as sa_func
    from app.models.bank import Bank
    from app.services.bank_registry import invalidate_bank_cache

    name = body.name.strip()
    if not name:
        raise BadRequestError("name cannot be blank")

    clash = (await db.execute(
        select(Bank).where(sa_func.lower(Bank.name) == name.lower())
    )).scalar_one_or_none()
    if clash is not None:
        raise BadRequestError(
            f"'{clash.name}' is already on the list"
            + ("" if clash.is_active else " (deactivated — PATCH it to re-activate)")
        )

    if body.sort_order is None:
        nxt = (await db.execute(select(sa_func.max(Bank.sort_order)))).scalar() or 0
        sort_order = nxt + 1
    else:
        sort_order = body.sort_order

    bank = Bank(
        name=name, sort_order=sort_order, created_by=admin.id,
        commission_rate=body.commission_rate,
        is_aggregator=bool(body.is_aggregator),
        partner_code=body.partner_code,
    )
    db.add(bank)
    await db.commit()
    await db.refresh(bank)
    invalidate_bank_cache()
    logger.info("BANK_ADDED name=%s by=%s", bank.name, admin.email)
    return {
        "id": bank.id, "name": bank.name, "is_active": bank.is_active,
        "sort_order": bank.sort_order, "usage_count": 0,
        "commission_rate": bank.commission_rate,
        "is_aggregator": bank.is_aggregator,
        "partner_code": bank.partner_code,
        "created_at": bank.created_at, "updated_at": bank.updated_at,
    }


@router.patch("/banks/{bank_id}", response_model=BankOut, tags=["Bank List"])
async def update_bank_in_list(
    bank_id: uuid.UUID,
    body: BankUpdate,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Rename, reorder, or activate/deactivate a lender. Admin only.

    **Deactivating** removes it from the dropdown and stops new shares
    being recorded against it. It does NOT remove its grid column or
    touch existing rows — a lender you have stopped working with still
    had real files go to it, and hiding that would lose history.

    **Renaming does not rewrite existing `lead_banks` rows.** They store
    the name as text, so a rename splits the lender in two: old rows keep
    the old string, new ones get the new. Only rename to fix a spelling
    before a lender is used; `usage_count` on
    `GET /leads/banks/manage` tells you what is at stake.
    """
    from sqlalchemy import func as sa_func
    from app.models.bank import Bank
    from app.models.lead_bank import LeadBank
    from app.services.bank_registry import invalidate_bank_cache

    bank = (await db.execute(select(Bank).where(Bank.id == bank_id))).scalar_one_or_none()
    if bank is None:
        raise NotFoundError("Bank not found")

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        new_name = data["name"].strip()
        clash = (await db.execute(
            select(Bank).where(
                sa_func.lower(Bank.name) == new_name.lower(), Bank.id != bank.id,
            )
        )).scalar_one_or_none()
        if clash is not None:
            raise BadRequestError(f"'{clash.name}' is already on the list")
        bank.name = new_name
    if "is_active" in data:
        bank.is_active = data["is_active"]
    if "sort_order" in data and data["sort_order"] is not None:
        bank.sort_order = data["sort_order"]
    # Explicit `in data` rather than a truthiness check so a rate can be
    # cleared back to None — a lender whose deal lapsed must be able to
    # stop having commission computed for it.
    if "commission_rate" in data:
        bank.commission_rate = data["commission_rate"]
    if "is_aggregator" in data:
        bank.is_aggregator = data["is_aggregator"]
    if "partner_code" in data:
        bank.partner_code = data["partner_code"]

    await db.commit()
    await db.refresh(bank)
    invalidate_bank_cache()
    logger.info(
        "BANK_UPDATED id=%s name=%s active=%s by=%s",
        bank.id, bank.name, bank.is_active, admin.email,
    )
    usage = (await db.execute(
        select(sa_func.count()).select_from(LeadBank).where(
            LeadBank.bank_name == bank.name, LeadBank.company_id == company_id,
        )
    )).scalar() or 0
    return {
        "id": bank.id, "name": bank.name, "is_active": bank.is_active,
        "sort_order": bank.sort_order, "usage_count": usage,
        "commission_rate": bank.commission_rate,
        "is_aggregator": bank.is_aggregator,
        "partner_code": bank.partner_code,
        "created_at": bank.created_at, "updated_at": bank.updated_at,
    }


@router.get("/universities", response_model=list[str])
async def list_universities(
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """University autocomplete suggestions for the Admitverse application
    form. NOT a locked list (unlike /leads/banks) — university_name is
    free text. FMC has no universities → returns [].
    """
    from app.core.constants import get_universities_for_brand
    slug = await _company_slug(db, company_id)
    return get_universities_for_brand(slug)


@router.get("/search", response_model=PaginatedResponse[LeadOut])
async def search_leads(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    return await service.search_leads(q, current_user, page, page_size)


# Declared BEFORE /{lead_id}: FastAPI matches in declaration order, and
# a literal path registered after the UUID route would be captured by it
# ('bank-share-grid' parsed as a lead_id -> 422). Same reason /by-stage
# and /search sit up here.
@router.get("/bank-share-grid", response_model=BankShareGridOut, tags=["Bank Shares"])
async def bank_share_grid(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: str | None = Query(None, description="Search name/phone/email"),
    # Repeatable filters — a multi-select in the toolbar sends the param
    # once per checked box (?current_stage=created&current_stage=dnp).
    # Passing a single value behaves exactly as it did before, so an
    # existing single-select frontend keeps working untouched.
    stage: list[str] | None = Query(
        None, alias="current_stage",
        description="Repeatable; matches any of the supplied stages",
    ),
    agent_id: list[uuid.UUID] | None = Query(
        None, description="Repeatable; matches any of the supplied counsellors",
    ),
    bank_name: list[str] | None = Query(
        None, description="Repeatable; leads shared with ANY of these banks",
    ),
    shared_only: bool = Query(False, description="Only leads shared with at least one bank"),
):
    """The grid: one row per lead, one column per bank, in one call.

    `banks` is the column order. Each row's `shares` maps bank_name → cell
    for the banks that lead has gone to; banks absent from the map are
    blank cells.

    Each cell carries `shared_at`, `shared_by_name`, `message_count`,
    `last_message_at` and a short `last_message_preview` — enough to
    render and to show a useful tooltip immediately. The FULL conversation
    for a cell is fetched on hover from
    `GET /leads/{id}/bank-shares/{bank}` rather than inlined, since
    embedding every message for 25 leads x 19 banks would dwarf the rest
    of the payload.

    `current_stage`, `agent_id` and `bank_name` are repeatable and OR
    together within a filter, while different filters AND with each other
    — so ?bank_name=PNB&bank_name=Axis&current_stage=processing reads as
    "at PNB *or* Axis, *and* in processing". Same rule the `tags` filter
    on /leads already follows.

    Three queries regardless of page size.
    """
    service = LeadService(db, company_id)
    return await service.bank_share_grid(
        user=current_user, page=page, page_size=page_size,
        stage=stage, agent_id=agent_id, bank_name=bank_name,
        shared_only=shared_only, q=q,
    )


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    lead = await service.get_lead(lead_id, current_user)
    # Which campaign(s) this lead came from — shown on the lead page so a
    # counsellor can see why an AI-called lead is in front of them.
    # Enriched here rather than in get_lead(), which is called internally
    # by a dozen methods that don't need the extra query.
    lead.campaigns = (await service.get_lead_campaigns([lead.id])).get(lead.id, [])
    return lead


@router.put("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    data = body.model_dump(exclude_unset=True)
    return await service.update_lead(lead_id, data, current_user)


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: uuid.UUID,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    await service.delete_lead(lead_id)
    return {"message": "Lead deleted"}


@router.get("/{lead_id}/banks", response_model=list[LeadBankOut])
async def list_lead_banks(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """All bank entries for a lead, newest first. Each entry has its
    own status. lead.bank_name + lead.bank_status reflect the highest-
    priority entry as the "primary" bank shown on the Kanban tile.
    """
    service = LeadService(db, company_id)
    return await service.list_banks(lead_id, current_user)


@router.post("/{lead_id}/banks", response_model=LeadBankOut, status_code=201)
async def add_lead_bank(
    lead_id: uuid.UUID,
    body: LeadBankCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a bank entry to a lead. bank_name must be in GET /leads/banks.
    Returns 400 if this lead already has an entry for that bank — use
    PATCH instead.
    """
    service = LeadService(db, company_id)
    return await service.add_bank(lead_id, body.bank_name, body.bank_status, body.notes, current_user)


@router.patch("/{lead_id}/banks/{entry_id}", response_model=LeadBankOut)
async def update_lead_bank(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: LeadBankUpdate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Update bank_status, notes, and/or the 9 sanction-detail fields on
    a single bank entry. Sanction details are only writable once the
    bank reaches sanctioned/pf_paid/disbursed.
    """
    service = LeadService(db, company_id)
    payload = body.model_dump(exclude_unset=True)
    return await service.update_bank_entry(lead_id, entry_id, payload, current_user)


@router.delete("/{lead_id}/banks/{entry_id}")
async def delete_lead_bank(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a bank entry from a lead."""
    service = LeadService(db, company_id)
    await service.delete_bank_entry(lead_id, entry_id, current_user)
    return {"message": "Bank entry deleted"}


@router.get("/{lead_id}/applications", response_model=list[LeadApplicationOut])
async def list_lead_applications(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """All university-application entries for a lead, newest first. Each
    entry has its own status. lead.primary_university + application_status
    reflect the highest-priority entry shown on the Kanban tile.
    """
    service = LeadService(db, company_id)
    return await service.list_applications(lead_id, current_user)


@router.post("/{lead_id}/applications", response_model=LeadApplicationOut, status_code=201)
async def add_lead_application(
    lead_id: uuid.UUID,
    body: LeadApplicationCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a university application to a lead (Admitverse only). Returns
    400 if the lead already has an entry for that university+program.
    """
    service = LeadService(db, company_id)
    return await service.add_application(lead_id, body.model_dump(exclude_unset=True), current_user)


@router.patch("/{lead_id}/applications/{entry_id}", response_model=LeadApplicationOut)
async def update_lead_application(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    body: LeadApplicationUpdate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Update application_status, notes, and/or the offer-detail fields on
    a single application entry. Offer details are only writable once the
    application reaches offer_received or later.
    """
    service = LeadService(db, company_id)
    return await service.update_application_entry(
        lead_id, entry_id, body.model_dump(exclude_unset=True), current_user
    )


@router.delete("/{lead_id}/applications/{entry_id}")
async def delete_lead_application(
    lead_id: uuid.UUID,
    entry_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a university-application entry from a lead."""
    service = LeadService(db, company_id)
    await service.delete_application_entry(lead_id, entry_id, current_user)
    return {"message": "Application entry deleted"}


# ── Bank shares (WhatsApp phase 2) ─────────────────────────────────────
#
# "This lead's file was shared with this bank." Stored on lead_banks —
# the table that already models one row per (lead, bank) — so there is a
# single place recording which bank a lead is with. These routes never
# write bank_status; that is the bank's decision, handled by the existing
# /banks endpoints.


@router.post("/{lead_id}/bank-shares", response_model=BankShareOut, tags=["Bank Shares"])
async def record_bank_share(
    lead_id: uuid.UUID,
    body: BankShareCreate,
    response: Response,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Record that this lead's file was shared with a bank.

    **Idempotent on (lead, bank).** Sharing the same lead into the same
    group again returns the existing row untouched — the original
    `shared_at` is kept, because the grid answers "when did this file
    first reach this bank". Log the repeat as a message instead.

    `201` when a new share was recorded, `200` when one already existed.
    Both are success; the bot does not need to branch.

    Never writes `bank_status`. A brand-new row takes the schema default
    `applied`, which is the lowest rung and is what sharing a file means;
    an existing row's status is left exactly as the bank set it.
    """
    service = LeadService(db, company_id)
    row, created = await service.record_bank_share(
        lead_id, body.model_dump(exclude_unset=True), current_user
    )
    response.status_code = 201 if created else 200
    rollup = await service._share_rollup([row.id])
    return service._share_to_dict(row, rollup.get(row.id, {}))


@router.get("/{lead_id}/bank-shares", response_model=list[BankShareOut], tags=["Bank Shares"])
async def list_bank_shares(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Every bank this lead has been shared with, most recent first."""
    service = LeadService(db, company_id)
    return await service.list_bank_shares(lead_id, current_user)


@router.get(
    "/{lead_id}/bank-shares/{bank_name}",
    response_model=BankShareDetailOut, tags=["Bank Shares"],
)
async def get_bank_share(
    lead_id: uuid.UUID,
    bank_name: str,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """One share plus its full conversation — what a grid cell's hover loads."""
    service = LeadService(db, company_id)
    return await service.get_bank_share_detail(lead_id, bank_name, current_user)


@router.post(
    "/{lead_id}/bank-shares/{bank_name}/messages",
    response_model=BankMessageOut, tags=["Bank Shares"],
)
async def add_bank_share_message(
    lead_id: uuid.UUID,
    bank_name: str,
    body: BankMessageCreate,
    response: Response,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Append a message to this lead's conversation in that bank's group.

    **Idempotent on `wa_message_id`** — a redelivered WhatsApp message
    returns the row already stored instead of duplicating the thread.
    `201` when stored, `200` when it was already there.

    Both sides of the conversation belong here; set `is_our_team` to
    distinguish our staff from the bank's. Kept separate from
    `/leads/{id}/remarks`, which is the lead's general internal timeline.

    404s if the lead has not been shared with this bank yet — record the
    share first.
    """
    service = LeadService(db, company_id)
    msg, created = await service.add_bank_message(
        lead_id, bank_name, body.model_dump(exclude_unset=True), current_user
    )
    response.status_code = 201 if created else 200
    return msg


@router.post("/{lead_id}/remarks", response_model=LeadRemarkOut, status_code=201)
async def add_lead_remark(
    lead_id: uuid.UUID,
    body: LeadRemarkCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Add a free-form remark on a lead. Visible to anyone with access
    to the lead (admin, manager, assigned counsellor, pre-counsellor).
    Captures author identity + role at write time.
    """
    service = LeadService(db, company_id)
    return await service.add_remark(lead_id, body.body, current_user)


@router.get("/{lead_id}/remarks", response_model=list[LeadRemarkOut])
async def list_lead_remarks(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """List remarks on a lead, newest first. Returns author_name and
    author_role so the FE can render "Posted by Ashmita (Manager)".
    """
    service = LeadService(db, company_id)
    return await service.list_remarks(lead_id, current_user)


@router.get("/{lead_id}/timeline", response_model=list[StageLogOut])
async def get_timeline(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    return await service.get_timeline(lead_id, current_user)


@router.get("/{lead_id}/calls", response_model=list[CallAttemptOut])
async def get_lead_calls(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.services.call_service import CallService
    call_service = CallService(db, company_id)
    return await call_service.get_calls_for_lead(lead_id, current_user)


@router.get("/{lead_id}/tasks", response_model=list[TaskOut])
async def get_lead_tasks(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    from app.services.task_service import TaskService
    task_service = TaskService(db, company_id)
    return await task_service.get_tasks_for_lead(lead_id, current_user)


@router.get("/docs/checklist")
async def get_docs_checklist(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Return the standard document checklist (key + label pairs) for the
    tenant's brand. FMC gets the loan-doc list; Admitverse gets the
    study-abroad list. FE renders the per-doc checkboxes from this list.
    """
    from app.core.constants import get_doc_checklist_for_brand
    slug = await _company_slug(db, company_id)
    return {"items": get_doc_checklist_for_brand(slug)}


@router.post("/{lead_id}/assign", response_model=LeadOut)
async def assign_lead(
    lead_id: uuid.UUID,
    body: LeadAssign,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    return await service.assign_lead(lead_id, body.agent_id)


@router.post("/{lead_id}/reassign", response_model=LeadOut)
async def reassign_lead(
    lead_id: uuid.UUID,
    body: LeadReassign,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Reassign Counsellor and/or Pre-Counsellor on a single lead.

    Send either or both of `assigned_agent_id` and `pre_counsellor_id`
    in the body. Use `null` to explicitly unassign that slot. Omit the
    field to leave it unchanged. Optional `reason` is logged on the
    lead's timeline.

    Examples:
      { "assigned_agent_id": "<uuid>" }                              → set Counsellor
      { "pre_counsellor_id": null }                                  → clear Pre-Counsellor
      { "assigned_agent_id": "<a>", "pre_counsellor_id": "<b>" }     → both
      { "assigned_agent_id": "<a>", "reason": "Hindi-speaking lead" } → with audit reason

    Manager/Admin only. Writes a lead_remarks entry capturing
    before→after for both fields so admins can audit reassignments.
    """
    service = LeadService(db, company_id)
    updates = body.model_dump(exclude_unset=True, exclude={"reason"})
    return await service.reassign_lead(
        lead_id,
        actor=admin,
        updates=updates,
        reason=body.reason,
    )


@router.post("/{lead_id}/pipeline", response_model=LeadOut, tags=["Pipelines"])
async def move_lead_pipeline(
    lead_id: uuid.UUID,
    body: LeadPipelineMove,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Hand a lead between the AI board and the counsellor board.

    `{"pipeline": "normal"}` is the handover a counsellor makes when an
    AI-called lead is worth working by hand. The lead leaves the AI board
    and **future campaigns skip it**, so the AI never cold-calls someone
    being actively worked. Its campaign rows and call history stay — that
    history is usually why the lead is worth taking over.

    `{"pipeline": "ai"}` puts it back, so a mistake is reversible. That
    direction is refused when the lead's stage isn't one the AI board
    renders, since it would otherwise vanish from both boards.

    Idempotent, and logged as a remark on the lead's timeline.
    """
    service = LeadService(db, company_id)
    return await service.move_pipeline(
        lead_id, body.pipeline, current_user, reason=body.reason,
    )


@router.patch("/{lead_id}/important", response_model=LeadOut)
async def toggle_important(
    lead_id: uuid.UUID,
    body: LeadImportantToggle,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the is_important star on a lead. Doesn't change stage —
    Important is a flag, not a column. Telecallers can star their own
    leads; admins/managers can star any lead they can see."""
    service = LeadService(db, company_id)
    return await service.set_important(lead_id, body.is_important, current_user)


@router.post("/bulk-assign")
async def bulk_assign(
    body: LeadBulkAssign,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    count = await service.bulk_assign(body.lead_ids, body.agent_id)
    return {"message": f"{count} leads assigned"}


@router.post("/distribute-by-range", response_model=LeadDistributeRangeResponse)
async def distribute_by_range(
    body: LeadDistributeRangeRequest,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Distribute leads to multiple agents by row range.

    Example body — first 200 unassigned leads to user A, next 200 to user B:

        {
            "ranges": [
                {"from": 1, "to": 200, "agent_id": "<uuid-a>"},
                {"from": 201, "to": 400, "agent_id": "<uuid-b>"}
            ],
            "unassigned_only": true,
            "order_by": "created_at_desc"
        }

    Row positions are 1-indexed inclusive. Ranges must be disjoint. If a
    range extends past the eligible count (e.g. only 350 leads exist
    for a 1-400 range), the missing slots are silently skipped — the
    response shows the actual assigned_count per range.
    """
    service = LeadService(db, company_id)
    payload = await service.distribute_by_range(
        ranges=[
            {"from_pos": r.from_pos, "to_pos": r.to_pos, "agent_id": r.agent_id}
            for r in body.ranges
        ],
        unassigned_only=body.unassigned_only,
        stage=body.stage,
        order_by=body.order_by,
    )
    return payload


# --- Lead Sources ---
@router.get("/sources/list", response_model=list[LeadSourceOut], tags=["Lead Sources"])
async def list_lead_sources(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LeadSource)
        .where(LeadSource.company_id == company_id, LeadSource.is_active == True)
        .order_by(LeadSource.name)
    )
    return result.scalars().all()


# ── Meta Lead Ads — admin routing table ────────────────────────────────

@router.get("/meta-routing", tags=["Meta Routing"])
async def list_meta_routing(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List every meta_form_routing entry. Admin-only.

    Only meaningful on the FMC gateway deployment — AV deployment will
    return an empty list since it never writes here.
    """
    from app.models.meta_form_routing import MetaFormRouting
    rows = (await db.execute(
        select(MetaFormRouting).order_by(MetaFormRouting.created_at.desc())
    )).scalars().all()
    return [
        {
            "form_id": r.form_id,
            "target": r.target,
            "source_id": str(r.source_id) if r.source_id else None,
            "display_name": r.display_name,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/meta-routing", status_code=201, tags=["Meta Routing"])
async def upsert_meta_routing(
    body: dict,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add or update a meta_form_routing entry. Body:
    {"form_id": "...", "target": "fmc"|"av", "source_id": uuid|null, "display_name": "..."}.
    """
    from app.models.meta_form_routing import MetaFormRouting
    from sqlalchemy import insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    form_id = (body.get("form_id") or "").strip()
    target = (body.get("target") or "").strip()
    display_name = (body.get("display_name") or "").strip()
    if not form_id or target not in ("fmc", "av") or not display_name:
        from app.core.exceptions import BadRequestError
        raise BadRequestError(
            "form_id, target ('fmc' or 'av'), and display_name are required"
        )
    sid_raw = body.get("source_id")
    sid = uuid.UUID(sid_raw) if sid_raw else None

    stmt = pg_insert(MetaFormRouting).values(
        form_id=form_id, target=target, source_id=sid, display_name=display_name,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["form_id"],
        set_={"target": target, "source_id": sid, "display_name": display_name},
    )
    await db.execute(stmt)
    await db.commit()
    return {"form_id": form_id, "target": target, "source_id": str(sid) if sid else None, "display_name": display_name}


@router.delete("/meta-routing/{form_id}", tags=["Meta Routing"])
async def delete_meta_routing(
    form_id: str,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.models.meta_form_routing import MetaFormRouting
    from sqlalchemy import delete as sqla_delete
    await db.execute(sqla_delete(MetaFormRouting).where(MetaFormRouting.form_id == form_id))
    await db.commit()
    return {"status": "deleted", "form_id": form_id}


@router.post("/sources", response_model=LeadSourceOut, status_code=201, tags=["Lead Sources"])
async def create_lead_source(
    body: LeadSourceCreate,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    source = LeadSource(company_id=company_id, **body.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source
