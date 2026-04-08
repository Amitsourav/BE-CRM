from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.task_service import TaskService
from app.schemas.task import TaskCreate, TaskUpdate, TaskComplete, TaskOut
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=PaginatedResponse[TaskOut])
async def list_tasks(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: str | None = Query(None),
    assigned_to: uuid.UUID | None = Query(None),
):
    service = TaskService(db, company_id)
    return await service.list_tasks(current_user, page, page_size, status, assigned_to)


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(
    body: TaskCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    data = body.model_dump(exclude_unset=True)
    return await service.create_task(data, current_user.id)


@router.get("/today", response_model=list[TaskOut])
async def get_today_tasks(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    return await service.get_today_tasks(current_user)


@router.get("/overdue", response_model=list[TaskOut])
async def get_overdue_tasks(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    return await service.get_overdue_tasks(current_user)


@router.get("/completed-today", response_model=list[TaskOut])
async def get_completed_today(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    return await service.get_completed_today(current_user)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    return await service.get_task(task_id, current_user)


@router.put("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    data = body.model_dump(exclude_unset=True)
    return await service.update_task(task_id, data, current_user)


@router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: uuid.UUID,
    body: TaskComplete,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = TaskService(db, company_id)
    return await service.complete_task(task_id, current_user, body.completion_notes)
