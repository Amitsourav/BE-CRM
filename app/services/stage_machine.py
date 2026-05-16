from __future__ import annotations

import uuid
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.profile import Profile
from app.models.company import Company
from app.models.task import Task
from app.core.constants import (
    LeadStage,
    UserRole,
    RESTRICTED_VIEW_ROLES,
    TaskType,
    TaskStatus,
    get_transitions_for_brand,
    get_terminal_stages_for_brand,
    get_notes_required_for_brand,
)
from app.core.exceptions import (
    NotFoundError, ForbiddenError, BadRequestError, InvalidTransitionError,
)
from app.utils.date_helpers import now_utc, add_business_days
from app.config import get_settings

logger = logging.getLogger(__name__)


async def auto_complete_stale_call_tasks(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    company_id: uuid.UUID,
    new_stage: str,
) -> int:
    """Mark past-due PENDING/OVERDUE CALL tasks for a lead as completed.

    Called from every code path that changes a lead's stage so the
    telecaller's task list doesn't accumulate stale callbacks. Future-
    dated CALL tasks (e.g. follow-up scheduled for next week) survive —
    only tasks whose due_date <= now() are closed.

    Idempotent: re-running on the same lead does nothing once the
    pending/overdue tasks have been completed.

    Returns the number of tasks closed.
    """
    now = now_utc()
    result = await db.execute(
        update(Task)
        .where(
            Task.lead_id == lead_id,
            Task.company_id == company_id,
            Task.task_type == TaskType.CALL.value,
            Task.status.in_([TaskStatus.PENDING.value, TaskStatus.OVERDUE.value]),
            Task.due_date <= now,
        )
        .values(
            status=TaskStatus.COMPLETED.value,
            completed_at=now,
            completion_notes=f"Auto-completed: lead moved to {new_stage}",
        )
    )
    closed = result.rowcount or 0
    if closed:
        logger.info(
            "STAGE_TRANSITION_AUTO_COMPLETED_TASKS lead=%s count=%d new_stage=%s",
            lead_id, closed, new_stage,
        )
    return closed


class StageMachine:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id
        self.settings = get_settings()
        self._slug: str | None = None

    async def _get_slug(self) -> str | None:
        if self._slug is not None:
            return self._slug
        result = await self.db.execute(
            select(Company.slug).where(Company.id == self.company_id)
        )
        self._slug = result.scalar_one_or_none()
        return self._slug

    async def transition(
        self,
        lead_id: uuid.UUID,
        to_stage: str,
        user: Profile,
        conversation_notes: str | None = None,
        agent_agenda: str | None = None,
        due_date=None,
        lost_reason: str | None = None,
    ) -> Lead:
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
            raise ForbiddenError("Not authorized to modify this lead")

        from_stage = LeadStage(lead.current_stage)
        target = LeadStage(to_stage)

        slug = await self._get_slug()
        transitions = get_transitions_for_brand(slug)
        terminal_stages = get_terminal_stages_for_brand(slug)
        notes_required = get_notes_required_for_brand(slug)

        # Validate transition
        if target not in transitions.get(from_stage, []):
            raise InvalidTransitionError(from_stage.value, target.value)

        # FMC: admin-only reopen from lost
        if from_stage == LeadStage.LOST and target == LeadStage.LEAD:
            if user.role != UserRole.ADMIN:
                raise ForbiddenError("Only admin can reopen a lost lead")

        # Require notes for certain stages
        if target in notes_required:
            if not conversation_notes or not agent_agenda:
                raise BadRequestError(
                    f"Stage '{target.value}' requires conversation_notes and agent_agenda"
                )

        # Require lost_reason when moving to lost, and validate it
        # against the locked dropdown list. Free-text reasons were
        # producing 12+ spelling variants of the same intent in reports.
        if target == LeadStage.LOST:
            if not lost_reason:
                raise BadRequestError("lost_reason is required when moving to 'lost'")
            from app.core.constants import LOST_REASONS
            if lost_reason not in LOST_REASONS:
                raise BadRequestError(
                    f"lost_reason must be one of the canonical FMC values "
                    f"(got '{lost_reason}'). See GET /leads/lost-reasons."
                )

        # Follow-up date is mandatory for every non-terminal transition.
        # Terminal stages (FMC: disbursed + lost) don't need one — the
        # lead is done. Telecallers were leaving leads in active stages
        # with no scheduled follow-up, so nothing pulled the lead back
        # onto someone's Tasks page.
        if target not in terminal_stages and not due_date:
            raise BadRequestError(
                f"Follow-up date is required when moving a lead to '{target.value}'."
            )

        # Set due date
        new_due = due_date
        if target in notes_required and not new_due:
            new_due = add_business_days(now_utc(), self.settings.default_due_days)

        # Update lead
        lead.current_stage = target.value
        lead.due_date = new_due if target not in terminal_stages else None

        if target == LeadStage.CONNECTED and not lead.connected_time:
            lead.connected_time = now_utc()
        if target == LeadStage.WON:
            lead.won_time = now_utc()
        if target == LeadStage.ENROLLED and not lead.won_time:
            # Admitverse's "Enrolled" is the final happy state — mirror to
            # won_time so existing reports/widgets keep working.
            lead.won_time = now_utc()
        if target == LeadStage.LOST:
            lead.lost_time = now_utc()
            lead.lost_reason = lost_reason
        if target == LeadStage.DNP:
            # FMC DNP attempt counter — increments each time a lead lands
            # back in the DNP column. Lets the card render "DNP-3" so the
            # telecaller knows how many times this lead has been chased.
            lead.dnp_count = (lead.dnp_count or 0) + 1
        if target == LeadStage.LEAD and from_stage == LeadStage.LOST:
            # Reopen — clear lost fields (FMC only)
            lead.lost_time = None
            lead.lost_reason = None

        # Create stage log
        stage_log = LeadStageLog(
            company_id=self.company_id,
            lead_id=lead.id,
            from_stage=from_stage.value,
            to_stage=target.value,
            changed_by=user.id,
            conversation_notes=conversation_notes,
            agent_agenda=agent_agenda,
            due_date_set=new_due,
        )
        self.db.add(stage_log)
        # Flush so stage_log.id is available for the optional Task link below.
        await self.db.flush()

        # If the transition set a callback date and the new stage isn't
        # terminal, surface it on the assignee's Tasks page. Without this
        # the telecaller sees the lead change column on the Kanban (and
        # lead.due_date update silently) but no task ever appears,
        # forcing them to remember the callback or trawl leads by date.
        # Idempotent — won't duplicate a task for the same lead+due_date.
        if new_due and target not in terminal_stages:
            existing = await self.db.execute(
                select(Task.id).where(
                    Task.lead_id == lead.id,
                    Task.company_id == self.company_id,
                    Task.task_type == TaskType.CALL.value,
                    Task.due_date == new_due,
                    Task.status.in_([
                        TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value,
                        TaskStatus.OVERDUE.value,
                    ]),
                )
            )
            if not existing.scalar_one_or_none():
                assignee = lead.assigned_agent_id or user.id
                description_parts = []
                if conversation_notes:
                    description_parts.append(f"Notes: {conversation_notes}")
                if agent_agenda:
                    description_parts.append(f"Agenda: {agent_agenda}")
                self.db.add(Task(
                    company_id=self.company_id,
                    lead_id=lead.id,
                    assigned_to=assignee,
                    created_by=user.id,
                    task_type=TaskType.CALL.value,
                    title=f"Callback ({target.value}): {lead.full_name}",
                    description=("\n\n".join(description_parts) or None),
                    status=TaskStatus.PENDING.value,
                    due_date=new_due,
                    stage_log_id=stage_log.id,
                ))
                logger.info(
                    "STAGE_TRANSITION_TASK_CREATED lead=%s assignee=%s due=%s stage=%s",
                    lead.id, assignee, new_due, target.value,
                )

        # Auto-complete past-due CALL tasks for this lead so the
        # telecaller's task list doesn't keep showing the old callback
        # after the stage moved on. Future-dated tasks survive.
        await auto_complete_stale_call_tasks(
            self.db,
            lead_id=lead.id,
            company_id=self.company_id,
            new_stage=target.value,
        )

        await self.db.commit()
        await self.db.refresh(lead)

        logger.info("Lead %s transitioned: %s → %s by %s", lead_id, from_stage.value, target.value, user.id)
        return lead

    async def get_stage_history(self, lead_id: uuid.UUID, user: Profile) -> list[LeadStageLog]:
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
            select(LeadStageLog)
            .where(LeadStageLog.lead_id == lead_id, LeadStageLog.company_id == self.company_id)
            .order_by(LeadStageLog.created_at.desc())
        )
        return result.scalars().all()
