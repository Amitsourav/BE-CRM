from __future__ import annotations

import uuid
import logging
from datetime import timedelta
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.task import Task
from app.models.lead import Lead
from app.models.profile import Profile
from app.models.notification import Notification
from app.core.constants import TaskStatus, UserRole, NotificationType, RESTRICTED_VIEW_ROLES
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.utils.date_helpers import now_utc, start_of_today, end_of_today, add_business_days
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)


class TaskService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def create_task(self, data: dict, created_by: uuid.UUID) -> Task:
        assigned_to = data.get("assigned_to") or created_by
        data["company_id"] = self.company_id

        # Validate assigned_to exists in this tenant and is active.
        # Without this, a malicious or buggy client could pass any UUID
        # (cross-tenant or non-existent), and we'd either pollute another
        # tenant's task list or 500 on the FK violation. Clean 400 instead.
        target_check = await self.db.execute(
            select(Profile.id).where(
                Profile.id == assigned_to,
                Profile.company_id == self.company_id,
                Profile.is_active == True,  # noqa: E712
            )
        )
        if not target_check.scalar_one_or_none():
            raise BadRequestError(
                "Invalid assigned_to: user not found in this company or inactive"
            )

        # Validate lead_id (when provided) belongs to this tenant and isn't
        # soft-deleted. Same reasoning as assigned_to.
        lead_id = data.get("lead_id")
        if lead_id:
            lead_check = await self.db.execute(
                select(Lead.id).where(
                    Lead.id == lead_id,
                    Lead.company_id == self.company_id,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )
            if not lead_check.scalar_one_or_none():
                raise BadRequestError(
                    "Invalid lead_id: lead not found in this company or deleted"
                )

        # due_date is NOT NULL in the DB. The public API enforces it via the
        # TaskCreate Pydantic schema, but internal callers (e.g. stage machine
        # hooks) pass a plain dict — default to one business day out so a
        # missing key doesn't produce a cryptic IntegrityError at insert time.
        if not data.get("due_date"):
            data["due_date"] = add_business_days(now_utc(), 1)
        # Strip assigned_to from data before splat — `assigned_to` was
        # already resolved above (body value or fallback to created_by),
        # so passing it both via **data and as a keyword raises TypeError.
        data.pop("assigned_to", None)
        task = Task(**data, created_by=created_by, assigned_to=assigned_to)
        self.db.add(task)

        # Skip the "New Task Assigned" notification when the creator is also
        # the assignee — no point pinging yourself about your own action.
        if assigned_to != created_by:
            notif = Notification(
                company_id=self.company_id,
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
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.company_id == self.company_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            raise NotFoundError("Task not found")
        if user.role in RESTRICTED_VIEW_ROLES and task.assigned_to != user.id:
            # Fallback: allow if the user owns the underlying LEAD as
            # Counsellor (assigned_agent_id) or Pre-Counsellor
            # (pre_counsellor_id). Without this, CSV-imported leads
            # whose callback task got auto-assigned to the admin
            # uploader were untouchable by the pre-counsellor who
            # actually owns the lead.
            from app.models.lead import Lead
            if task.lead_id:
                lead = (await self.db.execute(
                    select(Lead.assigned_agent_id, Lead.pre_counsellor_id)
                    .where(Lead.id == task.lead_id)
                )).first()
                if lead and (lead.assigned_agent_id == user.id or lead.pre_counsellor_id == user.id):
                    return task
            raise ForbiddenError("Not authorized")
        return task

    async def update_task(self, task_id: uuid.UUID, data: dict, user: Profile) -> Task:
        task = await self.get_task(task_id, user)

        # Block direct status="completed" via PUT — without setting
        # completed_at + completion_notes the row looks completed but
        # reports show inconsistent data ("completed" with no timestamp).
        # Force callers through POST /tasks/{id}/complete which fills
        # those fields atomically.
        if data.get("status") == TaskStatus.COMPLETED.value:
            raise BadRequestError(
                "Use POST /tasks/{id}/complete to complete a task — "
                "this endpoint can't set completed_at + completion_notes."
            )

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
        query = select(Task).where(Task.company_id == self.company_id).order_by(Task.due_date.asc())

        if user.role in RESTRICTED_VIEW_ROLES:
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
                Task.company_id == self.company_id,
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
            Task.company_id == self.company_id,
            Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.OVERDUE]),
            Task.due_date < now_utc(),
        ).order_by(Task.due_date.asc())

        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(Task.assigned_to == user.id)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_completed_today(self, user: Profile) -> list[Task]:
        query = (
            select(Task)
            .where(
                Task.company_id == self.company_id,
                Task.status == TaskStatus.COMPLETED,
                Task.completed_at >= start_of_today(),
                Task.completed_at <= end_of_today(),
            )
            .order_by(Task.completed_at.desc())
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(Task.assigned_to == user.id)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def count_actionable_tasks(
        self,
        user: Profile,
        statuses: list[str] | None = None,
        due_before_eod: bool = True,
    ) -> int:
        """Lightweight count of "things this user needs to do right now".

        Always scopes to assigned_to = user.id regardless of role — admin's
        company-wide visibility doesn't apply to a personal-actionable
        badge ("you have N tasks to do" must mean YOUR tasks, not the
        whole company's).

        Defaults match the locked badge spec: pending + overdue tasks
        due today or earlier. Callers can override `statuses` to count
        a different slice (e.g. just `["overdue"]`).
        """
        if statuses is None:
            statuses = [TaskStatus.PENDING.value, TaskStatus.OVERDUE.value]
        q = select(func.count()).select_from(Task).where(
            Task.company_id == self.company_id,
            Task.assigned_to == user.id,
            Task.status.in_(statuses),
        )
        if due_before_eod:
            q = q.where(Task.due_date <= end_of_today())
        result = await self.db.execute(q)
        return result.scalar() or 0

    async def get_tasks_for_lead(self, lead_id: uuid.UUID, user: Profile) -> list[Task]:
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id and lead.pre_counsellor_id != user.id:
            raise ForbiddenError("Not authorized")

        result = await self.db.execute(
            select(Task).where(Task.lead_id == lead_id, Task.company_id == self.company_id).order_by(Task.created_at.desc())
        )
        return result.scalars().all()
