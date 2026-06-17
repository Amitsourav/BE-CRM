from __future__ import annotations

import uuid
from datetime import date
from fastapi import APIRouter, Depends, Query
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
)
from app.schemas.stage import StageLogOut
from app.schemas.call import CallAttemptOut
from app.schemas.task import TaskOut
from app.schemas.common import PaginatedResponse
from app.core.constants import UserRole

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
        regex="^(campaign|unassigned|counsellor|pre_counsellor)$",
        description="Admin-only slice: campaign | unassigned | counsellor | pre_counsellor.",
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
        regex="^(campaign|unassigned|counsellor|pre_counsellor)$",
        description="Admin-only slice: campaign | unassigned | counsellor | pre_counsellor. FE should hide this dropdown for non-admin roles since restricted-view roles already only see their own leads.",
    ),
    sort_by: str = Query(
        "created_desc",
        regex="^(created_desc|loan_asc|loan_desc|budget_asc|budget_desc)$",
        description="Per-column row order: created_desc (default), loan_asc/desc (FMC), budget_asc/desc (AV). Leads without the sort value are placed at the end.",
    ),
):
    """Kanban board endpoint — returns all leads grouped by stage in one
    round trip (replaces 19 per-column requests for Admitverse, 6 for FMC).
    """
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
        sort_by=sort_by,
    )
    return {
        "items_by_stage": {
            stage: [LeadCardOut.model_validate(lead) for lead in leads]
            for stage, leads in data["items_by_stage"].items()
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


@router.get("/banks", response_model=list[str])
async def list_banks(
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Canonical FMC bank dropdown for the Kanban-card bank_name field
    and the lead edit form. Locked list — backend rejects any bank_name
    not in here on lead update. Admitverse has no banks → returns [].
    """
    from app.core.constants import FMC_BANKS
    if await _company_slug(db, company_id) == "admitverse":
        return []
    return list(FMC_BANKS)


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


@router.get("/{lead_id}", response_model=LeadOut)
async def get_lead(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    return await service.get_lead(lead_id, current_user)


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
