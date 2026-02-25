from __future__ import annotations

import uuid
import logging
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.models.lead import Lead
from app.models.profile import Profile
from app.models.notification import Notification
from app.core.constants import TaskStatus, UserRole, NotificationType
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.utils.date_helpers import now_utc, start_of_today, end_of_today
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_task(self, data: dict, created_by: uuid.UUID) -> Task:
        assigned_to = data.get("assigned_to") or created_by
        task = Task(**data, created_by=created_by, assigned_to=assigned_to)
        self.db.add(task)

        # Send notification
        notif = Notification(
            user_id=assigned_to,
            type=NotificationType.TASK_CREATED,
            title="New Task Assigned",
            message=f"Task: {task.title}",
            lead_id=data.get("lead_id"),
            task_id=task.id,
        )
        self.db.add(notif)

        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def get_task(self, task_id: uuid.UUID, user: Profile) -> Task:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise NotFoundError("Task not found")
        if user.role == UserRole.AGENT and task.assigned_to != user.id:
            raise ForbiddenError("Not authorized")
        return task

    async def update_task(self, task_id: uuid.UUID, data: dict, user: Profile) -> Task:
        task = await self.get_task(task_id, user)
        for key, value in data.items():
            setattr(task, key, value)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def complete_task(self, task_id: uuid.UUID, user: Profile, completion_notes: str | None = None) -> Task:
        task = await self.get_task(task_id, user)
        task.status = TaskStatus.COMPLETED
        task.completed_at = now_utc()
        task.completion_notes = completion_notes
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def list_tasks(
        self,
        user: Profile,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        assigned_to: uuid.UUID | None = None,
    ) -> dict:
        query = select(Task).order_by(Task.due_date.asc())

        if user.role == UserRole.AGENT:
            query = query.where(Task.assigned_to == user.id)
        elif assigned_to:
            query = query.where(Task.assigned_to == assigned_to)

        if status:
            query = query.where(Task.status == status)

        return await paginate(self.db, query, page, page_size)

    async def get_today_tasks(self, user: Profile) -> list[Task]:
        query = (
            select(Task)
            .where(
                Task.assigned_to == user.id,
                Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
                Task.due_date <= end_of_today(),
            )
            .order_by(Task.due_date.asc())
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_overdue_tasks(self, user: Profile) -> list[Task]:
        query = select(Task).where(
            Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]),
            Task.due_date < now_utc(),
        ).order_by(Task.due_date.asc())

        if user.role == UserRole.AGENT:
            query = query.where(Task.assigned_to == user.id)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_completed_today(self, user: Profile) -> list[Task]:
        query = (
            select(Task)
            .where(
                Task.status == TaskStatus.COMPLETED,
                Task.completed_at >= start_of_today(),
                Task.completed_at <= end_of_today(),
            )
            .order_by(Task.completed_at.desc())
        )
        if user.role == UserRole.AGENT:
            query = query.where(Task.assigned_to == user.id)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_tasks_for_lead(self, lead_id: uuid.UUID, user: Profile) -> list[Task]:
        # Auth check
        result = await self.db.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role == UserRole.AGENT and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        result = await self.db.execute(
            select(Task).where(Task.lead_id == lead_id).order_by(Task.created_at.desc())
        )
        return result.scalars().all()
