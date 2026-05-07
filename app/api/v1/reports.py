from __future__ import annotations

import uuid
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_manager, get_current_user
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


@router.get("/daily")
async def daily_activity(
    user_id: uuid.UUID | None = Query(None, description="Admin can pass any user_id; non-admins ignored (always self)"),
    date_str: str | None = Query(None, alias="date", description="YYYY-MM-DD in IST. Default = today IST."),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Per-user daily activity report. Telecaller / manager see only
    self; admin can query any user via ?user_id=. Returns metrics for
    the day, yesterday's snapshot for delta, and target/percent.
    """
    service = ReportService(db, company_id)
    return await service.daily_activity(
        requesting_user=current_user,
        target_user_id=user_id,
        date_str=date_str,
    )


@router.get("/daily/range")
async def daily_activity_range(
    user_id: uuid.UUID | None = Query(None),
    days: int = Query(30, ge=1, le=90),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Last N days (default 30) of per-user activity, oldest first.
    No deltas/targets — those only make sense for the focused single-day view.
    """
    service = ReportService(db, company_id)
    return await service.daily_activity_range(
        requesting_user=current_user,
        target_user_id=user_id,
        days=days,
    )


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
