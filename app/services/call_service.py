from __future__ import annotations

import uuid
import logging
from datetime import date, datetime
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.lead import Lead
from app.models.call_attempt import CallAttempt
from app.models.notification import Notification
from app.models.profile import Profile
from app.models.ai_agent import AIAgent
from app.models.company import Company
from app.models.task import Task
from app.core.constants import (
    LeadStage, CallDisposition, UserRole, NotificationType,
    TaskType, TaskStatus,
    ADMITVERSE_TERMINAL, RESTRICTED_VIEW_ROLES,
)
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.utils.date_helpers import now_utc, add_business_days
from app.config import get_settings

logger = logging.getLogger(__name__)


class CallService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id
        self.settings = get_settings()

    # ── Existing manual call logging (unchanged) ──────────────────────

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

        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        # Brand-aware gate. FMC's pipeline is strict forward-only and only
        # the LEAD/CALLED early stages are sensible for manual call logging.
        # Admitverse allows free movement and counselors call leads at any
        # non-terminal stage (e.g. follow-up call to a lead already in
        # processing). Hardcoding LEAD/CALLED here used to reject every
        # Admitverse call beyond the first one — and the error message
        # referenced FMC stage names that don't even exist on Admitverse.
        slug_result = await self.db.execute(
            select(Company.slug).where(Company.id == self.company_id)
        )
        slug = (slug_result.scalar_one_or_none() or "").lower()
        if slug == "admitverse":
            current = LeadStage(lead.current_stage)
            if current in ADMITVERSE_TERMINAL:
                raise BadRequestError(
                    f"Cannot log calls for leads in '{lead.current_stage}' stage "
                    "(terminal — lead is enrolled or lost)."
                )
        else:
            if lead.current_stage not in (LeadStage.LEAD, LeadStage.CALLED):
                raise BadRequestError(
                    f"Cannot log calls for leads in '{lead.current_stage}' stage. "
                    "Lead must be in 'lead' or 'called' stage."
                )

        if lead.call_attempt_count >= self.settings.max_call_attempts:
            raise BadRequestError("Maximum call attempts reached for this lead")

        # Capture the stage as it was before any of the disposition-driven
        # bumps below — used to detect a real transition for the auto-
        # complete-stale-tasks helper at the bottom of the function.
        stage_before = lead.current_stage

        attempt_number = lead.call_attempt_count + 1
        disp = CallDisposition(disposition)
        next_due = due_date_for_next or add_business_days(now_utc(), self.settings.default_due_days)

        call = CallAttempt(
            company_id=self.company_id,
            lead_id=lead_id,
            agent_id=user.id,
            attempt_number=attempt_number,
            disposition=disp.value,
            conversation_notes=conversation_notes,
            agent_agenda=agent_agenda,
            due_date_for_next=next_due,
            call_type="live",
            call_status="ended",
            telecaller_id=user.id,
            **extra_fields,
        )
        self.db.add(call)

        lead.call_attempt_count = attempt_number
        lead.due_date = next_due

        # Auto-bump from "fresh lead" to "contacted" on first manual call.
        # Brand-aware so Admitverse's `created` lead becomes `contacted`,
        # matching its admissions pipeline; FMC's `lead` becomes `called`.
        if slug == "admitverse" and lead.current_stage == LeadStage.CREATED.value:
            lead.current_stage = LeadStage.CONTACTED.value
        elif lead.current_stage == LeadStage.LEAD.value:
            lead.current_stage = LeadStage.CALLED.value

        if disp == CallDisposition.DNP:
            if attempt_number == 5:
                notif = Notification(
                    company_id=self.company_id,
                    user_id=user.id,
                    type=NotificationType.DNP_WARNING,
                    title="DNP Warning",
                    message=f"Lead '{lead.full_name}' has 5 DNP attempts. One more will auto-close as Lost.",
                    lead_id=lead_id,
                )
                self.db.add(notif)
                logger.warning("DNP warning for lead %s (attempt 5)", lead_id)

            elif attempt_number >= self.settings.max_call_attempts:
                lead.current_stage = LeadStage.LOST
                lead.lost_time = now_utc()
                lead.lost_reason = f"Auto-lost: {self.settings.max_call_attempts} DNP attempts"
                lead.due_date = None

                notif = Notification(
                    company_id=self.company_id,
                    user_id=user.id,
                    type=NotificationType.DNP_AUTO_LOST,
                    title="Lead Auto-Lost (DNP)",
                    message=f"Lead '{lead.full_name}' auto-moved to Lost after {self.settings.max_call_attempts} DNP attempts.",
                    lead_id=lead_id,
                )
                self.db.add(notif)
                logger.info("Lead %s auto-lost after %d DNP attempts", lead_id, attempt_number)

        if disp == CallDisposition.CONNECTED:
            lead.current_stage = LeadStage.CONNECTED
            if not lead.connected_time:
                lead.connected_time = now_utc()

        # If this call moved the stage forward (or auto-closed as Lost),
        # close any stale callback tasks for this lead so the telecaller
        # isn't left with overdue items they've already resolved. Normalise
        # both sides to strings — assignments above use a mix of enum
        # values and enum members.
        from app.services.stage_machine import auto_complete_stale_call_tasks
        new_stage_value = (
            lead.current_stage.value
            if hasattr(lead.current_stage, "value")
            else lead.current_stage
        )
        before_value = (
            stage_before.value
            if hasattr(stage_before, "value")
            else stage_before
        )
        if new_stage_value != before_value:
            await auto_complete_stale_call_tasks(
                self.db,
                lead_id=lead.id,
                company_id=self.company_id,
                new_stage=new_stage_value,
            )

        # Auto-create a follow-up task assigned to the telecaller so the
        # callback shows up on their Tasks page. Without this the
        # `due_date_for_next` they entered only updated lead.due_date and
        # the call_attempt row — nothing surfaced in the Tasks list, so
        # the telecaller had to remember the lead from memory or trawl
        # the leads page filtered by date. Skip if the lead got
        # auto-closed as Lost (no point queuing more work on it) or if
        # the call was a wrong-number (no callback intended).
        skip_task_dispositions = {CallDisposition.WRONG_NUMBER}
        lead_just_closed = lead.current_stage == LeadStage.LOST
        will_create_task = (
            next_due
            and disp not in skip_task_dispositions
            and not lead_just_closed
        )
        logger.info(
            "LOG_CALL_TASK_DECISION lead=%s disposition=%s attempt=%d "
            "next_due=%s lead_stage=%s will_create_task=%s",
            lead_id, disp.value, attempt_number, next_due,
            lead.current_stage, will_create_task,
        )
        if will_create_task:
            task_title_map = {
                CallDisposition.DNP: f"Callback (DNP attempt {attempt_number}): {lead.full_name}",
                CallDisposition.BUSY: f"Callback (line busy): {lead.full_name}",
                CallDisposition.SWITCHED_OFF: f"Callback (phone off): {lead.full_name}",
                CallDisposition.CALLBACK: f"Scheduled callback: {lead.full_name}",
                CallDisposition.CONNECTED: f"Follow-up: {lead.full_name}",
            }
            task_title = task_title_map.get(
                disp, f"Follow-up call: {lead.full_name}"
            )
            description_parts = []
            if conversation_notes:
                description_parts.append(f"Last call notes: {conversation_notes}")
            if agent_agenda:
                description_parts.append(f"Agenda: {agent_agenda}")
            description = "\n\n".join(description_parts) or None

            self.db.add(Task(
                company_id=self.company_id,
                lead_id=lead_id,
                assigned_to=user.id,
                created_by=user.id,
                task_type=TaskType.CALL.value,
                title=task_title,
                description=description,
                status=TaskStatus.PENDING.value,
                due_date=next_due,
            ))
            logger.info(
                "LOG_CALL_TASK_CREATED lead=%s assigned_to=%s due=%s title=%r",
                lead_id, user.id, next_due, task_title,
            )

        await self.db.commit()
        await self.db.refresh(call)
        return call

    async def get_calls_for_lead(self, lead_id: uuid.UUID, user: Profile) -> list[CallAttempt]:
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
        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized")

        result = await self.db.execute(
            select(CallAttempt)
            .where(CallAttempt.lead_id == lead_id, CallAttempt.company_id == self.company_id)
            .order_by(CallAttempt.created_at.desc())
        )
        return result.scalars().all()

    # ── New telephony functions ───────────────────────────────────────

    async def create_call_record(
        self, telecaller_id: uuid.UUID, data: dict,
    ) -> CallAttempt:
        """Create a new call record (for Bolna AI or live calls)."""
        lead_id = data["lead_id"]

        # Verify lead exists in company (and isn't soft-deleted)
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

        # Verify AI agent if provided
        ai_agent_id = data.get("ai_agent_id")
        if ai_agent_id:
            result = await self.db.execute(
                select(AIAgent).where(
                    AIAgent.id == ai_agent_id,
                    AIAgent.company_id == self.company_id,
                    AIAgent.is_active == True,
                )
            )
            if not result.scalar_one_or_none():
                raise BadRequestError("AI Agent not found or inactive")

        call = CallAttempt(
            company_id=self.company_id,
            lead_id=lead_id,
            agent_id=telecaller_id,
            telecaller_id=telecaller_id,
            ai_agent_id=ai_agent_id,
            call_type=data.get("call_type", "ai"),
            call_status="pending",
            attempt_number=lead.call_attempt_count + 1,
            disposition="dnp",  # default until call completes
            conversation_notes="",
            agent_agenda="",
        )
        self.db.add(call)
        await self.db.commit()
        await self.db.refresh(call)
        return call

    async def update_call_status(
        self, call_id: uuid.UUID, data: dict,
    ) -> CallAttempt:
        """Update call status (from webhooks or manual)."""
        call = await self._get_call(call_id)

        for field in ("call_status", "bolna_call_id", "started_at", "ended_at",
                      "cost", "call_duration_seconds"):
            if field in data and data[field] is not None:
                setattr(call, field, data[field])

        await self.db.commit()
        await self.db.refresh(call)
        return call

    async def save_call_post_data(
        self, call_id: uuid.UUID, data: dict,
    ) -> CallAttempt:
        """Save post-call AI data (transcript, summary, sentiment, cost)."""
        call = await self._get_call(call_id)

        for field in ("transcript", "summary", "sentiment", "sentiment_score",
                       "cost", "call_duration_seconds", "call_recording_url"):
            if field in data and data[field] is not None:
                setattr(call, field, data[field])

        await self.db.commit()
        await self.db.refresh(call)
        return call

    async def get_all_calls(
        self,
        user: Profile,
        skip: int = 0,
        limit: int = 50,
        search: str | None = None,
        telecaller_id: uuid.UUID | None = None,
        call_status: str | None = None,
        call_type: str | None = None,
        sentiment: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[CallAttempt]:
        """List all calls for the company with optional filters."""
        query = (
            select(CallAttempt)
            .where(CallAttempt.company_id == self.company_id)
            .order_by(CallAttempt.created_at.desc())
        )

        # Telecaller sees only own calls
        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(CallAttempt.telecaller_id == user.id)
        elif telecaller_id:
            query = query.where(CallAttempt.telecaller_id == telecaller_id)

        if search:
            from sqlalchemy import or_
            pattern = f"%{search}%"
            query = query.where(
                or_(
                    CallAttempt.transcript.ilike(pattern),
                    CallAttempt.phone_number.ilike(pattern),
                    CallAttempt.summary.ilike(pattern),
                )
            )
        if call_status:
            query = query.where(CallAttempt.call_status == call_status)
        if call_type:
            query = query.where(CallAttempt.call_type == call_type)
        if sentiment:
            query = query.where(CallAttempt.sentiment == sentiment)
        if date_from:
            query = query.where(func.date(CallAttempt.created_at) >= date_from)
        if date_to:
            query = query.where(func.date(CallAttempt.created_at) <= date_to)

        query = query.options(
            selectinload(CallAttempt.lead),
            selectinload(CallAttempt.ai_agent),
        )
        query = query.offset(skip).limit(limit)
        result = await self.db.execute(query)
        calls = result.scalars().all()

        # Map joined fields so Pydantic can serialize them
        for call in calls:
            call.lead_name = call.lead.full_name if call.lead else None
            call.lead_phone = call.lead.phone if call.lead else None
            call.lead_stage = call.lead.current_stage if call.lead else None
            call.agent_name = call.ai_agent.name if call.ai_agent else None
        return calls

    async def get_call(self, call_id: uuid.UUID, user: Profile) -> dict:
        """Get single call with lead and agent info."""
        call = await self._get_call(call_id)

        # Telecaller can only see own calls
        if user.role in RESTRICTED_VIEW_ROLES and call.telecaller_id != user.id:
            raise ForbiddenError("Not authorized to view this call")

        # Load lead and agent names
        lead_name = None
        lead_phone = None
        agent_name = None

        result = await self.db.execute(select(Lead).where(Lead.id == call.lead_id))
        lead = result.scalar_one_or_none()
        if lead:
            lead_name = lead.full_name
            lead_phone = lead.phone

        result = await self.db.execute(select(Profile).where(Profile.id == call.agent_id))
        agent = result.scalar_one_or_none()
        if agent:
            agent_name = agent.full_name

        # Build response dict that matches CallAttemptWithLead
        call_dict = {c.key: getattr(call, c.key) for c in call.__table__.columns}
        call_dict["lead_name"] = lead_name
        call_dict["lead_phone"] = lead_phone
        call_dict["agent_name"] = agent_name
        return call_dict

    async def get_call_stats(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        telecaller_id: uuid.UUID | None = None,
    ) -> dict:
        """Get call statistics for admin/manager dashboard."""
        base = select(CallAttempt).where(CallAttempt.company_id == self.company_id)
        filters = [CallAttempt.company_id == self.company_id]

        if telecaller_id:
            filters.append(CallAttempt.telecaller_id == telecaller_id)
        if date_from:
            filters.append(func.date(CallAttempt.created_at) >= date_from)
        if date_to:
            filters.append(func.date(CallAttempt.created_at) <= date_to)

        # Main stats in one query.
        #
        # call_status is a *lifecycle* column: initiated → ringing → connected
        # → ended. After hangup it becomes 'ended' and stays there. Counting
        # rows where call_status == 'connected' therefore returns only the
        # tiny set of calls happening at this exact moment, not "successfully
        # connected calls ever". The reliable signal that a call actually
        # connected is started_at being non-null — set when the caller picks
        # up. Failed dispatches and unanswered rings never set it.
        row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count().filter(CallAttempt.started_at.isnot(None)).label("connected"),
                func.count().filter(CallAttempt.call_status == "failed").label("failed"),
                func.count().filter(CallAttempt.call_status == "no_answer").label("no_answer"),
                func.avg(CallAttempt.call_duration_seconds).label("avg_duration"),
                func.sum(CallAttempt.cost).label("total_cost"),
            ).where(*filters)
        )).one()

        # Sentiment breakdown
        sentiment_rows = (await self.db.execute(
            select(CallAttempt.sentiment, func.count())
            .where(*filters, CallAttempt.sentiment.isnot(None))
            .group_by(CallAttempt.sentiment)
        )).all()
        sentiment_breakdown = {s: c for s, c in sentiment_rows}

        # Calls by type
        type_rows = (await self.db.execute(
            select(CallAttempt.call_type, func.count())
            .where(*filters)
            .group_by(CallAttempt.call_type)
        )).all()
        calls_by_type = {t: c for t, c in type_rows}

        # Calls by day
        day_rows = (await self.db.execute(
            select(
                cast(CallAttempt.created_at, Date).label("day"),
                func.count().label("count"),
            )
            .where(*filters)
            .group_by(cast(CallAttempt.created_at, Date))
            .order_by(cast(CallAttempt.created_at, Date))
        )).all()
        calls_by_day = [{"date": str(r.day), "count": r.count} for r in day_rows]

        return {
            "total_calls": row.total or 0,
            "connected_calls": row.connected or 0,
            "failed_calls": row.failed or 0,
            "no_answer_calls": row.no_answer or 0,
            "avg_duration_seconds": round(float(row.avg_duration or 0), 1),
            "total_cost": round(float(row.total_cost or 0), 2),
            "sentiment_breakdown": {
                "positive": sentiment_breakdown.get("positive", 0),
                "neutral": sentiment_breakdown.get("neutral", 0),
                "negative": sentiment_breakdown.get("negative", 0),
            },
            "calls_by_type": {
                "ai": calls_by_type.get("ai", 0),
                "live": calls_by_type.get("live", 0),
            },
            "calls_by_day": calls_by_day,
        }

    # ── Internal helpers ──────────────────────────────────────────────

    async def _get_lead(self, lead_id: uuid.UUID) -> Lead:
        """Get lead by ID, scoped to company (excludes soft-deleted)."""
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
        return lead

    async def _get_call(self, call_id: uuid.UUID) -> CallAttempt:
        """Get call by ID, scoped to company."""
        result = await self.db.execute(
            select(CallAttempt).where(
                CallAttempt.id == call_id,
                CallAttempt.company_id == self.company_id,
            )
        )
        call = result.scalar_one_or_none()
        if not call:
            raise NotFoundError("Call not found")
        return call
