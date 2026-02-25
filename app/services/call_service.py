from __future__ import annotations

import uuid
import logging
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import (
    LeadStage, CallDisposition, UserRole, NotificationType,
)
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.utils.date_helpers import now_utc, add_business_days
from app.config import get_settings

logger = logging.getLogger(__name__)


class CallService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def log_call(
        self,
        lead_id: uuid.UUID,
        user: Profile,
        disposition: str,
        conversation_notes: str,
        agent_agenda: str,
        due_date_for_next=None,
        **extra_fields,
    ) -> CallAttempt:
        result = await self.db.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")

        if user.role == UserRole.AGENT and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        # Lead must be in 'called' stage or transitioning into it
        if lead.current_stage not in (LeadStage.LEAD, LeadStage.CALLED):
            raise BadRequestError(
                f"Cannot log calls for leads in '{lead.current_stage}' stage. "
                "Lead must be in 'lead' or 'called' stage."
            )

        # Check max attempts
        if lead.call_attempt_count >= self.settings.max_call_attempts:
            raise BadRequestError("Maximum call attempts reached for this lead")

        attempt_number = lead.call_attempt_count + 1
        disp = CallDisposition(disposition)

        # Default next due date
        next_due = due_date_for_next or add_business_days(now_utc(), self.settings.default_due_days)

        call = CallAttempt(
            lead_id=lead_id,
            agent_id=user.id,
            attempt_number=attempt_number,
            disposition=disp.value,
            conversation_notes=conversation_notes,
            agent_agenda=agent_agenda,
            due_date_for_next=next_due,
            **extra_fields,
        )
        self.db.add(call)

        # Update lead
        lead.call_attempt_count = attempt_number
        lead.due_date = next_due

        # Move to 'called' if still in 'lead'
        if lead.current_stage == LeadStage.LEAD:
            lead.current_stage = LeadStage.CALLED

        # Handle DNP logic
        if disp == CallDisposition.DNP:
            if attempt_number == 5:
                # Warning notification
                notif = Notification(
                    user_id=user.id,
                    type=NotificationType.DNP_WARNING,
                    title="DNP Warning",
                    message=f"Lead '{lead.full_name}' has 5 DNP attempts. One more will auto-close as Lost.",
                    lead_id=lead_id,
                )
                self.db.add(notif)
                logger.warning("DNP warning for lead %s (attempt 5)", lead_id)

            elif attempt_number >= self.settings.max_call_attempts:
                # Auto-move to Lost
                lead.current_stage = LeadStage.LOST
                lead.lost_time = now_utc()
                lead.lost_reason = f"Auto-lost: {self.settings.max_call_attempts} DNP attempts"
                lead.due_date = None

                notif = Notification(
                    user_id=user.id,
                    type=NotificationType.DNP_AUTO_LOST,
                    title="Lead Auto-Lost (DNP)",
                    message=f"Lead '{lead.full_name}' auto-moved to Lost after {self.settings.max_call_attempts} DNP attempts.",
                    lead_id=lead_id,
                )
                self.db.add(notif)
                logger.info("Lead %s auto-lost after %d DNP attempts", lead_id, attempt_number)

        # If disposition is connected, lead can be moved to connected stage
        if disp == CallDisposition.CONNECTED:
            lead.current_stage = LeadStage.CONNECTED
            if not lead.connected_time:
                lead.connected_time = now_utc()

        await self.db.commit()
        await self.db.refresh(call)
        return call

    async def get_calls_for_lead(self, lead_id: uuid.UUID, user: Profile) -> list[CallAttempt]:
        # Auth check
        result = await self.db.execute(select(Lead).where(Lead.id == lead_id))
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role == UserRole.AGENT and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        result = await self.db.execute(
            select(CallAttempt)
            .where(CallAttempt.lead_id == lead_id)
            .order_by(CallAttempt.created_at.desc())
        )
        return result.scalars().all()
