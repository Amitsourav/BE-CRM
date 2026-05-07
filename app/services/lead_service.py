from __future__ import annotations

import uuid
import logging
from datetime import date
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.profile import Profile
from app.models.lead_stage_log import LeadStageLog
from app.models.task import Task
from app.models.company import Company
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.core.constants import (
    UserRole, LeadStage, RESTRICTED_VIEW_ROLES,
    TaskType, TaskStatus,
    get_initial_stage_for_brand,
)
from app.utils.pagination import paginate
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


class LeadService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def _ensure_callback_task(
        self,
        lead: Lead,
        due_date,
        actor_id: uuid.UUID,
    ) -> bool:
        """Auto-create a follow-up Task when a lead's callback date is set.

        Telecallers were setting `lead.due_date` directly via PUT /leads/{id}
        (the lead-edit form) and call_service.log_call wasn't getting hit, so
        no task surfaced on their Tasks page. This helper materialises the
        task on any path that sets due_date.

        Idempotent — if a pending CALL task already exists for this lead at
        the same due_date, do nothing. The actor_id is used as a fallback
        assignee when the lead has no assigned agent yet.
        Returns True when a task was created.
        """
        if not due_date:
            return False
        existing = await self.db.execute(
            select(Task.id).where(
                Task.lead_id == lead.id,
                Task.company_id == self.company_id,
                Task.task_type == TaskType.CALL.value,
                Task.due_date == due_date,
                Task.status.in_([
                    TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value,
                    TaskStatus.OVERDUE.value,
                ]),
            )
        )
        if existing.scalar_one_or_none():
            return False

        assignee = lead.assigned_agent_id or actor_id
        title = f"Callback: {lead.full_name}"
        self.db.add(Task(
            company_id=self.company_id,
            lead_id=lead.id,
            assigned_to=assignee,
            created_by=actor_id,
            task_type=TaskType.CALL.value,
            title=title,
            description=None,
            status=TaskStatus.PENDING.value,
            due_date=due_date,
        ))
        return True

    async def create_lead(self, data: dict, created_by: uuid.UUID) -> Lead:
        data["company_id"] = self.company_id
        slug_result = await self.db.execute(select(Company.slug).where(Company.id == self.company_id))
        initial_stage = get_initial_stage_for_brand(slug_result.scalar_one_or_none())
        data.setdefault("current_stage", initial_stage.value)
        lead = Lead(**data, created_by=created_by)
        self.db.add(lead)
        await self.db.flush()

        # Create initial stage log
        stage_log = LeadStageLog(
            company_id=self.company_id,
            lead_id=lead.id,
            from_stage=None,
            to_stage=initial_stage.value,
            changed_by=created_by,
        )
        self.db.add(stage_log)

        # If the lead is created with a due_date already set, auto-queue a
        # callback task so it shows on the assignee's Tasks page.
        if lead.due_date:
            await self._ensure_callback_task(lead, lead.due_date, created_by)

        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def get_lead(self, lead_id: uuid.UUID, user: Profile) -> Lead:
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized to view this lead")
        return lead

    async def update_lead(self, lead_id: uuid.UUID, data: dict, user: Profile) -> Lead:
        lead = await self.get_lead(lead_id, user)
        prev_due_date = lead.due_date
        prev_stage = lead.current_stage

        # If current_stage is being changed, route through StageMachine
        # so transition rules, lost_reason gating, notes requirements,
        # AND the LeadStageLog timeline entry all happen. Skipping this
        # was the bug that let the FE drag-drop into Lost without a
        # remark, no timeline trace, and no validation.
        new_stage = data.pop("current_stage", None)
        transition_notes = data.pop("conversation_notes", None)
        transition_agenda = data.pop("agent_agenda", None)
        transition_lost_reason = data.pop("lost_reason", None)
        transition_due_date = data.get("due_date")  # peek; let normal path also apply it

        if new_stage and new_stage != prev_stage:
            from app.services.stage_machine import StageMachine
            machine = StageMachine(self.db, self.company_id)
            await machine.transition(
                lead_id=lead.id,
                to_stage=new_stage,
                user=user,
                conversation_notes=transition_notes,
                agent_agenda=transition_agenda,
                due_date=transition_due_date,
                lost_reason=transition_lost_reason,
            )
            # StageMachine.transition() commits internally — re-fetch so
            # we apply the rest of the user's edits to the latest row.
            lead = await self.get_lead(lead_id, user)
            prev_due_date = lead.due_date  # avoid double-creating the callback task

        for key, value in data.items():
            setattr(lead, key, value)

        # If due_date was set or changed in this update (and not already
        # handled by the transition above), queue a callback task. This
        # is the path telecallers use ("Edit Lead" → schedule next call)
        # without changing the stage.
        new_due_date = lead.due_date
        if new_due_date and new_due_date != prev_due_date:
            await self._ensure_callback_task(lead, new_due_date, user.id)

        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def delete_lead(self, lead_id: uuid.UUID) -> None:
        """Soft delete — sets is_deleted=True and deleted_at timestamp."""
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        lead.is_deleted = True
        lead.deleted_at = now_utc()
        await self.db.commit()

    async def list_leads(
        self,
        user: Profile,
        page: int = 1,
        page_size: int = 25,
        stage: str | None = None,
        agent_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        csv_import_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        query = select(Lead).where(Lead.company_id == self.company_id, Lead.is_deleted == False).order_by(Lead.created_at.desc())

        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(Lead.assigned_agent_id == user.id)
        elif agent_id:
            query = query.where(Lead.assigned_agent_id == agent_id)

        if stage:
            query = query.where(Lead.current_stage == stage)
        if source_id:
            query = query.where(Lead.lead_source_id == source_id)
        if csv_import_id:
            query = query.where(Lead.csv_import_id == csv_import_id)
        if tags:
            query = query.where(Lead.tags.overlap(tags))
        if date_from:
            query = query.where(func.date(Lead.created_at) >= date_from)
        if date_to:
            query = query.where(func.date(Lead.created_at) <= date_to)

        return await paginate(self.db, query, page, page_size)

    async def list_leads_by_stage(
        self,
        user: Profile,
        agent_id: uuid.UUID | None = None,
        per_stage_limit: int = 50,
    ) -> dict:
        """Fetch leads grouped by stage in one round trip.

        The Kanban board previously fired one /leads request per stage
        column — 19 round trips for Admitverse, each carrying a separate
        COUNT and SELECT. This walks the table once, partitions by
        current_stage on the frontend, and caps each stage at
        per_stage_limit so we don't ship thousands of cards for a long-tail
        stage. A second tiny query collects total counts so the Kanban can
        show "+N more" if a column is truncated.
        """
        # Per-stage row cap via a window function: rank rows within their
        # stage by created_at desc and keep the top N. One scan, one
        # round trip.
        from sqlalchemy import literal_column, asc, desc
        from sqlalchemy.sql import over

        rn = func.row_number().over(
            partition_by=Lead.current_stage,
            order_by=Lead.created_at.desc(),
        ).label("rn")

        base = select(Lead, rn).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            base = base.where(Lead.assigned_agent_id == user.id)
        elif agent_id:
            base = base.where(Lead.assigned_agent_id == agent_id)

        sub = base.subquery()
        result = await self.db.execute(
            select(Lead).join(sub, Lead.id == sub.c.id).where(sub.c.rn <= per_stage_limit)
        )
        rows = result.scalars().all()

        items_by_stage: dict[str, list[Lead]] = {}
        for lead in rows:
            items_by_stage.setdefault(lead.current_stage, []).append(lead)

        # Total counts per stage (for "+N more" labels). One query.
        count_query = select(Lead.current_stage, func.count()).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            count_query = count_query.where(Lead.assigned_agent_id == user.id)
        elif agent_id:
            count_query = count_query.where(Lead.assigned_agent_id == agent_id)
        count_query = count_query.group_by(Lead.current_stage)
        count_rows = (await self.db.execute(count_query)).all()
        counts_by_stage = {stage: cnt for stage, cnt in count_rows}
        total = sum(counts_by_stage.values())

        return {
            "items_by_stage": items_by_stage,
            "counts_by_stage": counts_by_stage,
            "total": total,
        }

    async def search_leads(self, q: str, user: Profile, page: int = 1, page_size: int = 25) -> dict:
        query = select(Lead).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,
            or_(
                Lead.full_name.ilike(f"%{q}%"),
                Lead.email.ilike(f"%{q}%"),
                Lead.phone.ilike(f"%{q}%"),
            )
        ).order_by(Lead.created_at.desc())

        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(Lead.assigned_agent_id == user.id)

        return await paginate(self.db, query, page, page_size)

    async def assign_lead(self, lead_id: uuid.UUID, agent_id: uuid.UUID) -> Lead:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        result = await self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.company_id == self.company_id, Lead.is_deleted == False)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")

        lead.assigned_agent_id = agent_id
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def set_important(self, lead_id: uuid.UUID, value: bool, user: Profile) -> Lead:
        """Toggle the is_important flag on a lead.

        Doesn't move the lead between Kanban columns — Important is a
        flag, not a stage. Same access rules as other lead writes:
        admin/manager can star anything they can see; telecallers only
        their own assigned leads.
        """
        lead = await self.get_lead(lead_id, user)
        lead.is_important = bool(value)
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def distribute_by_range(
        self,
        ranges: list[dict],
        unassigned_only: bool = True,
        stage: str | None = None,
        order_by: str = "created_at_desc",
    ) -> dict:
        """Distribute leads to agents by row position.

        Walks the leads (filtered and ordered as requested) and assigns
        rows [from_pos..to_pos] of each range to the corresponding
        agent_id. Row positions are 1-indexed against the filtered list,
        not the DB id.

        Each range dict: {"from_pos": int, "to_pos": int, "agent_id": UUID}

        Validates:
        - All agent_ids exist in this company and are active
        - Ranges are well-formed (from_pos <= to_pos)
        - Ranges don't overlap (so a single lead never lands in two
          buckets — keep things deterministic)

        Returns: {
            "total_assigned": int,
            "eligible_count": int,
            "ranges": [{from_pos, to_pos, agent_id, agent_name, assigned_count}]
        }
        """
        if not ranges:
            raise BadRequestError("ranges cannot be empty")

        # ── 1. Validate range shape and overlaps ──
        sorted_ranges = sorted(ranges, key=lambda r: r["from_pos"])
        prev_to = 0
        for r in sorted_ranges:
            if r["from_pos"] > r["to_pos"]:
                raise BadRequestError(
                    f"Invalid range: from={r['from_pos']} > to={r['to_pos']}"
                )
            if r["from_pos"] <= prev_to:
                raise BadRequestError(
                    f"Range from={r['from_pos']} overlaps a previous range "
                    f"(ended at {prev_to}). Ranges must be disjoint."
                )
            prev_to = r["to_pos"]

        # ── 2. Validate every agent exists in this company and is active ──
        agent_ids = {r["agent_id"] for r in ranges}
        agent_rows = (await self.db.execute(
            select(Profile.id, Profile.full_name).where(
                Profile.id.in_(agent_ids),
                Profile.company_id == self.company_id,
                Profile.is_active == True,  # noqa: E712
            )
        )).all()
        agent_name_by_id = {row.id: row.full_name for row in agent_rows}
        missing = agent_ids - set(agent_name_by_id.keys())
        if missing:
            raise BadRequestError(
                f"Unknown / inactive agent ids: {sorted(str(x) for x in missing)}"
            )

        # ── 3. Fetch eligible lead ids in the requested order ──
        q = select(Lead.id).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if unassigned_only:
            q = q.where(Lead.assigned_agent_id.is_(None))
        if stage:
            q = q.where(Lead.current_stage == stage)
        if order_by == "created_at_asc":
            q = q.order_by(Lead.created_at.asc())
        else:
            q = q.order_by(Lead.created_at.desc())

        result = await self.db.execute(q)
        all_ids: list[uuid.UUID] = [row[0] for row in result.fetchall()]
        eligible_count = len(all_ids)

        # ── 4. Apply each range as one UPDATE ──
        results = []
        total_assigned = 0
        for r in ranges:
            from_pos = r["from_pos"]
            to_pos = r["to_pos"]
            # Convert 1-indexed inclusive to 0-indexed slice.
            slice_ids = all_ids[from_pos - 1: to_pos]
            assigned = 0
            if slice_ids:
                stmt = (
                    update(Lead)
                    .where(
                        Lead.id.in_(slice_ids),
                        Lead.company_id == self.company_id,
                        Lead.is_deleted == False,  # noqa: E712
                    )
                    .values(assigned_agent_id=r["agent_id"])
                )
                upd = await self.db.execute(stmt)
                assigned = upd.rowcount or 0
                total_assigned += assigned
            results.append({
                "from_pos": from_pos,
                "to_pos": to_pos,
                "agent_id": r["agent_id"],
                "agent_name": agent_name_by_id.get(r["agent_id"]),
                "assigned_count": assigned,
            })

        await self.db.commit()
        return {
            "total_assigned": total_assigned,
            "eligible_count": eligible_count,
            "ranges": results,
        }

    async def bulk_assign(self, lead_ids: list[uuid.UUID], agent_id: uuid.UUID) -> int:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        stmt = (
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.company_id == self.company_id, Lead.is_deleted == False)
            .values(assigned_agent_id=agent_id)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount

    async def get_timeline(self, lead_id: uuid.UUID, user: Profile) -> list[LeadStageLog]:
        await self.get_lead(lead_id, user)  # Auth check
        result = await self.db.execute(
            select(LeadStageLog)
            .where(LeadStageLog.lead_id == lead_id, LeadStageLog.company_id == self.company_id)
            .order_by(LeadStageLog.created_at.desc())
        )
        return result.scalars().all()
