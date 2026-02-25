from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_admin
from app.models.profile import Profile
from app.services.report_service import ReportService
from app.schemas.report import (
    DashboardReport, PipelineReport, AgentPerformance,
    SourcePerformance, TrendData,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/dashboard", response_model=DashboardReport)
async def dashboard(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.dashboard()


@router.get("/pipeline", response_model=PipelineReport)
async def pipeline(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.pipeline()


@router.get("/agents", response_model=list[AgentPerformance])
async def agents_summary(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.agents_summary()


@router.get("/agents/{agent_id}", response_model=AgentPerformance)
async def agent_detail(
    agent_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.agent_detail(agent_id)


@router.get("/sources", response_model=list[SourcePerformance])
async def sources(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.sources()


@router.get("/tasks/compliance")
async def task_compliance(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.task_compliance()


@router.get("/trends", response_model=list[TrendData])
async def trends(
    days: int = Query(30, ge=1, le=90),
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = ReportService(db)
    return await service.trends(days)
