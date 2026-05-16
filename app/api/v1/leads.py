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
)
from app.schemas.stage import StageLogOut
from app.schemas.call import CallAttemptOut
from app.schemas.task import TaskOut
from app.schemas.common import PaginatedResponse
from app.core.constants import UserRole

router = APIRouter(prefix="/leads", tags=["Leads"])


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
):
    service = LeadService(db, company_id)
    return await service.list_leads(
        user=current_user, page=page, page_size=page_size,
        stage=stage, agent_id=agent_id, source_id=source_id,
        csv_import_id=csv_import_id, campaign_id=campaign_id,
        date_from=date_from, date_to=date_to,
    )


@router.post("", response_model=LeadOut, status_code=201)
async def create_lead(
    body: LeadCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = LeadService(db, company_id)
    data = body.model_dump(exclude_unset=True)
    return await service.create_lead(data, current_user.id)


@router.get("/by-stage", response_model=LeadsByStageOut)
async def list_leads_by_stage(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    agent_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    per_stage_limit: int = Query(50, ge=1, le=200),
):
    """Kanban board endpoint — returns all leads grouped by stage in one
    round trip (replaces 19 per-column requests for Admitverse, 6 for FMC).
    """
    service = LeadService(db, company_id)
    data = await service.list_leads_by_stage(
        user=current_user, agent_id=agent_id, campaign_id=campaign_id,
        per_stage_limit=per_stage_limit,
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
async def list_lost_reasons():
    """Canonical FMC dropdown for the "Move to Lost" modal. Locked list —
    backend rejects any lost_reason not in here. FE should populate the
    dropdown from this endpoint rather than hardcoding the list.
    """
    from app.core.constants import LOST_REASONS
    return list(LOST_REASONS)


@router.get("/banks", response_model=list[str])
async def list_banks():
    """Canonical FMC bank dropdown for the Kanban-card bank_name field
    and the lead edit form. Locked list — backend rejects any bank_name
    not in here on lead update.
    """
    from app.core.constants import FMC_BANKS
    return list(FMC_BANKS)


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
):
    """Return the standard FMC document checklist (key + label pairs).
    FE renders the per-doc checkboxes on the Kanban tile from this list.
    Hardcoded server-side so adding/removing docs doesn't need a FE
    change — just a backend constant + migration if defaults shift.
    """
    from app.core.constants import FMC_DOC_CHECKLIST
    return {"items": FMC_DOC_CHECKLIST}


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
