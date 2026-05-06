from __future__ import annotations

import uuid
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.report_service import ReportService
from app.services.call_service import CallService
from app.schemas.report import (
    DashboardReport, PipelineReport, AgentPerformance,
    SourcePerformance, TrendData,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/dashboard", response_model=DashboardReport)
async def dashboard(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.dashboard(user=admin)


@router.get("/pipeline", response_model=PipelineReport)
async def pipeline(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.pipeline(user=admin)


@router.get("/agents", response_model=list[AgentPerformance])
async def agents_summary(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.agents_summary(user=admin)


@router.get("/agents/{agent_id}", response_model=AgentPerformance)
async def agent_detail(
    agent_id: uuid.UUID,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.agent_detail(agent_id, user=admin)


@router.get("/sources", response_model=list[SourcePerformance])
async def sources(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.sources(user=admin)


@router.get("/tasks/compliance")
async def task_compliance(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.task_compliance(user=admin)


@router.get("/trends", response_model=list[TrendData])
async def trends(
    days: int = Query(30, ge=1, le=90),
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db, company_id)
    return await service.trends(days, user=admin)


@router.get("/calls")
async def call_stats(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    telecaller_id: uuid.UUID | None = Query(None),
):
    service = CallService(db, company_id)
    return await service.get_call_stats(
        date_from=date_from,
        date_to=date_to,
        telecaller_id=telecaller_id,
    )
