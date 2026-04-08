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
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    service = LeadService(db, company_id)
    return await service.list_leads(
        user=current_user, page=page, page_size=page_size,
        stage=stage, agent_id=agent_id, source_id=source_id,
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
