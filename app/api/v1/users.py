from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user, get_current_admin
from app.models.profile import Profile
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt
from app.models.task import Task as TaskModel
from app.schemas.user import UserOut, UserUpdate, AdminUserUpdate, UserStats
from app.core.exceptions import NotFoundError
from app.core.constants import TaskStatus

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserOut)
async def get_me(current_user: Profile = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
async def update_me(
    body: UserUpdate,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_user, key, value)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.get("", response_model=list[UserOut])
async def list_users(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    role: str | None = Query(None),
    is_active: bool | None = Query(None),
):
    query = select(Profile).order_by(Profile.created_at.desc())
    if role:
        query = query.where(Profile.role == role)
    if is_active is not None:
        query = query.where(Profile.is_active == is_active)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    return user


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}")
async def deactivate_user(
    user_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Profile).where(Profile.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    user.is_active = False
    await db.commit()
    return {"message": "User deactivated"}


@router.get("/{user_id}/stats", response_model=UserStats)
async def get_user_stats(
    user_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    # Verify user exists
    result = await db.execute(select(Profile).where(Profile.id == user_id))
    if not result.scalar_one_or_none():
        raise NotFoundError("User not found")

    # Total leads
    total_leads = (await db.execute(
        select(func.count()).select_from(Lead).where(Lead.assigned_agent_id == user_id)
    )).scalar() or 0

    # Leads by stage
    stage_counts = (await db.execute(
        select(Lead.current_stage, func.count())
        .where(Lead.assigned_agent_id == user_id)
        .group_by(Lead.current_stage)
    )).all()
    leads_by_stage = {stage: count for stage, count in stage_counts}

    # Calls
    total_calls = (await db.execute(
        select(func.count()).select_from(CallAttempt).where(CallAttempt.agent_id == user_id)
    )).scalar() or 0

    # Tasks
    total_tasks = (await db.execute(
        select(func.count()).select_from(TaskModel).where(TaskModel.assigned_to == user_id)
    )).scalar() or 0

    completed_tasks = (await db.execute(
        select(func.count()).select_from(TaskModel)
        .where(TaskModel.assigned_to == user_id, TaskModel.status == TaskStatus.COMPLETED)
    )).scalar() or 0

    overdue_tasks = (await db.execute(
        select(func.count()).select_from(TaskModel)
        .where(TaskModel.assigned_to == user_id, TaskModel.status == TaskStatus.OVERDUE)
    )).scalar() or 0

    return UserStats(
        total_leads=total_leads,
        leads_by_stage=leads_by_stage,
        total_calls=total_calls,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        overdue_tasks=overdue_tasks,
    )
