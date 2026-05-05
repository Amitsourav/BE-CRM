from __future__ import annotations

import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.profile import Profile
from app.models.company import Company
from app.core.constants import (
    LeadStage,
    UserRole,
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

        if user.role == UserRole.TELECALLER and lead.assigned_agent_id != user.id:
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

        # Require lost_reason when moving to lost
        if target == LeadStage.LOST and not lost_reason:
            raise BadRequestError("lost_reason is required when moving to 'lost'")

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
        if user.role == UserRole.TELECALLER and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        result = await self.db.execute(
            select(LeadStageLog)
            .where(LeadStageLog.lead_id == lead_id, LeadStageLog.company_id == self.company_id)
            .order_by(LeadStageLog.created_at.desc())
        )
        return result.scalars().all()
