from __future__ import annotations

import asyncio
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
from app.models.call_attempt import CallAttempt
from app.models.campaign_lead import CampaignLead
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

        # Normalize phone to +91 format so dedup catches "7004428198" vs
        # "+917004428198" vs "7004 428 198" as the same number.
        if data.get("phone"):
            from app.utils.csv_parser import normalize_phone
            data["phone"] = normalize_phone(data["phone"])

        # Duplicate check on phone and email — same rule the CSV importer
        # applies. Without this, the Add Lead form was creating duplicates
        # (e.g. "amit"/7004428198 vs "Amit"/7004428198 living side-by-side
        # in different stages). Per-tenant scoped (company_id) and skips
        # soft-deleted rows.
        if data.get("phone"):
            existing = (await self.db.execute(
                select(Lead.id, Lead.full_name).where(
                    Lead.company_id == self.company_id,
                    Lead.phone == data["phone"],
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if existing:
                raise BadRequestError(
                    f"A lead with phone {data['phone']} already exists "
                    f"({existing.full_name})."
                )
        if data.get("email"):
            existing = (await self.db.execute(
                select(Lead.id, Lead.full_name).where(
                    Lead.company_id == self.company_id,
                    Lead.email == data["email"],
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if existing:
                raise BadRequestError(
                    f"A lead with email {data['email']} already exists "
                    f"({existing.full_name})."
                )

        slug_result = await self.db.execute(select(Company.slug).where(Company.id == self.company_id))
        initial_stage = get_initial_stage_for_brand(slug_result.scalar_one_or_none())
        data.setdefault("current_stage", initial_stage.value)

        # Mirror loan_amount → loan_amount_lakh (numeric, in lakhs) so the
        # Kanban budget filter can compare numbers without parsing text
        # in the query. Display column stays untouched.
        if data.get("loan_amount") is not None:
            from app.utils.loan_parser import parse_loan_amount
            data["loan_amount_lakh"] = parse_loan_amount(data["loan_amount"])

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
        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id and lead.pre_counsellor_id != user.id:
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

        # Validate bank_name against the canonical FMC bank list. Same
        # rationale as lost_reason — free text was producing case/spelling
        # variants that broke reporting (sbi / SBI / Unicred / UniCred).
        if "bank_name" in data and data["bank_name"]:
            from app.core.constants import FMC_BANKS
            if data["bank_name"] not in FMC_BANKS:
                raise BadRequestError(
                    f"bank_name must be one of the canonical FMC banks "
                    f"(got '{data['bank_name']}'). See GET /leads/banks."
                )

        # Mirror loan_amount → loan_amount_lakh on update too, same reason
        # as create_lead. If loan_amount is being explicitly cleared
        # (set to None or empty string), wipe the numeric mirror as well.
        if "loan_amount" in data:
            from app.utils.loan_parser import parse_loan_amount
            data["loan_amount_lakh"] = parse_loan_amount(data["loan_amount"])

        # Filter submitted_docs to known checklist keys + dedupe. Without
        # this, FE bugs or stale clients could push junk values into the
        # array (e.g., trailing whitespace, duplicate keys, or a key
        # we removed from the checklist later).
        if "submitted_docs" in data and data["submitted_docs"] is not None:
            from app.core.constants import FMC_DOC_KEYS
            cleaned = []
            seen = set()
            for k in data["submitted_docs"]:
                k = (k or "").strip().lower()
                if k and k in FMC_DOC_KEYS and k not in seen:
                    cleaned.append(k)
                    seen.add(k)
            data["submitted_docs"] = cleaned
            # Auto-sync the counter so existing widgets keep working.
            data["docs_submitted"] = len(cleaned)

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
        campaign_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        query = select(Lead).where(Lead.company_id == self.company_id, Lead.is_deleted == False).order_by(Lead.created_at.desc())

        if user.role in RESTRICTED_VIEW_ROLES:
            # Restricted viewers see leads where they're either the Counsellor
            # or the Pre Counsellor (FMC two-step model).
            query = query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            # Admin/manager filtering by "agent" — match either role on FMC.
            query = query.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))

        if stage:
            query = query.where(Lead.current_stage == stage)
        if source_id:
            query = query.where(Lead.lead_source_id == source_id)
        if csv_import_id:
            query = query.where(Lead.csv_import_id == csv_import_id)
        if campaign_id:
            # JOIN with campaign_leads — every lead enrolled in a campaign
            # has a campaign_leads row. distinct() guards the rare case a
            # lead got enrolled twice (shouldn't happen given the unique
            # constraint, but defensive against historical dirty data).
            query = query.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id).distinct()
        if tags:
            query = query.where(Lead.tags.overlap(tags))
        if date_from:
            query = query.where(func.date(Lead.created_at) >= date_from)
        if date_to:
            query = query.where(func.date(Lead.created_at) <= date_to)

        page_data = await paginate(self.db, query, page, page_size)
        # Same enrichment the Kanban /by-stage endpoint applies. Without
        # this, GET /leads?... returns assigned_agent_name=null and
        # task_count=0 even when the lead has data — which makes the
        # FE Kanban (still using /leads list) render empty rows for
        # the agent + badges. 5 batched aggregate queries; bounded by
        # page_size (default 25) so cost stays small.
        await self._enrich_cards(page_data["items"])
        return page_data

    def _apply_kanban_filters(
        self,
        query,
        *,
        q: str | None = None,
        source_id: uuid.UUID | None = None,
        loan_min: float | None = None,
        loan_max: float | None = None,
        bank_name: str | None = None,
        bank_status: str | None = None,
        target_country: str | None = None,
        target_intake: str | None = None,
        tags: list[str] | None = None,
        created_from=None,
        created_to=None,
        due_from=None,
        due_to=None,
        dnp_min: int | None = None,
        dnp_max: int | None = None,
        important_only: bool = False,
    ):
        """Apply Kanban filter set to a query. The visibility gate
        (assigned_agent_id / pre_counsellor_id ANDed in the caller) is
        deliberately NOT touched here — these filters compose on top.
        Same helper feeds both the items query and the counts query so
        the column counters stay consistent with the cards rendered.
        """
        if q:
            term = f"%{q.strip()}%"
            query = query.where(or_(
                Lead.full_name.ilike(term),
                Lead.phone.ilike(term),
                Lead.email.ilike(term),
            ))
        if source_id is not None:
            query = query.where(Lead.lead_source_id == source_id)
        if loan_min is not None:
            query = query.where(Lead.loan_amount_lakh >= loan_min)
        if loan_max is not None:
            query = query.where(Lead.loan_amount_lakh <= loan_max)
        if bank_name:
            query = query.where(Lead.bank_name == bank_name)
        if bank_status:
            query = query.where(Lead.bank_status == bank_status)
        if target_country:
            # preferred_countries is text[] — `any` checks membership.
            query = query.where(Lead.preferred_countries.any(target_country))
        if target_intake:
            query = query.where(Lead.target_intake == target_intake)
        if tags:
            # tags is text[] — `overlap` is "any tag in the filter matches",
            # which is the standard "OR-of-tags" UX. Use `contains` if you
            # ever want "AND-of-tags" instead. Explicit TEXT[] cast or
            # Postgres complains "text[] && varchar[] — no operator".
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import ARRAY
            from sqlalchemy import Text
            query = query.where(Lead.tags.overlap(cast(tags, ARRAY(Text))))
        if created_from is not None:
            query = query.where(Lead.created_at >= created_from)
        if created_to is not None:
            query = query.where(Lead.created_at <= created_to)
        if due_from is not None:
            query = query.where(Lead.due_date >= due_from)
        if due_to is not None:
            query = query.where(Lead.due_date <= due_to)
        if dnp_min is not None:
            query = query.where(Lead.dnp_count >= dnp_min)
        if dnp_max is not None:
            query = query.where(Lead.dnp_count <= dnp_max)
        if important_only:
            query = query.where(Lead.is_important == True)  # noqa: E712
        return query

    async def list_leads_by_stage(
        self,
        user: Profile,
        agent_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        per_stage_limit: int = 50,
        # Filter set added May 2026 for the FMC pipeline page. All
        # optional; FE drops them when not in use. Filters apply to BOTH
        # the items query and the count query so column counts stay in
        # sync with the rendered cards.
        q: str | None = None,
        source_id: uuid.UUID | None = None,
        loan_min: float | None = None,
        loan_max: float | None = None,
        bank_name: str | None = None,
        bank_status: str | None = None,
        target_country: str | None = None,
        target_intake: str | None = None,
        tags: list[str] | None = None,
        created_from=None,
        created_to=None,
        due_from=None,
        due_to=None,
        dnp_min: int | None = None,
        dnp_max: int | None = None,
        important_only: bool = False,
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
            base = base.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            base = base.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))
        if campaign_id:
            # Kanban scoped to a single campaign. Window function still
            # partitions by stage and caps at per_stage_limit — so the FE
            # shows the most-recent N leads from THIS campaign per column.
            base = base.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id)

        # Apply the Kanban filter set on top of the visibility + scope WHEREs.
        base = self._apply_kanban_filters(
            base,
            q=q, source_id=source_id,
            loan_min=loan_min, loan_max=loan_max,
            bank_name=bank_name, bank_status=bank_status,
            target_country=target_country, target_intake=target_intake,
            tags=tags,
            created_from=created_from, created_to=created_to,
            due_from=due_from, due_to=due_to,
            dnp_min=dnp_min, dnp_max=dnp_max,
            important_only=important_only,
        )

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
            count_query = count_query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            count_query = count_query.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))
        if campaign_id:
            count_query = count_query.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id)
        # Same filter helper feeds the count query so the column headers
        # always reflect the rendered card set. Drift here = "Qualified ·
        # 23" header with only 4 cards inside, which is the bug we're
        # explicitly preventing.
        count_query = self._apply_kanban_filters(
            count_query,
            q=q, source_id=source_id,
            loan_min=loan_min, loan_max=loan_max,
            bank_name=bank_name, bank_status=bank_status,
            target_country=target_country, target_intake=target_intake,
            tags=tags,
            created_from=created_from, created_to=created_to,
            due_from=due_from, due_to=due_to,
            dnp_min=dnp_min, dnp_max=dnp_max,
            important_only=important_only,
        )
        count_query = count_query.group_by(Lead.current_stage)
        count_rows = (await self.db.execute(count_query)).all()
        counts_by_stage = {stage: cnt for stage, cnt in count_rows}
        total = sum(counts_by_stage.values())

        # Enrichment for the FMC-enhanced tile. Five extra batched
        # queries — same constant cost regardless of how many leads
        # are on screen, so a 600-card Kanban load stays at ~7 SQL
        # round trips instead of devolving into N+1.
        await self._enrich_cards(rows)

        return {
            "items_by_stage": items_by_stage,
            "counts_by_stage": counts_by_stage,
            "total": total,
        }

    async def _enrich_cards(self, leads: list[Lead]) -> None:
        """Decorate each Lead with the activity rollups + agent display
        name the enhanced FMC tile renders. Sets transient attributes
        — Pydantic's from_attributes mode picks them up when building
        LeadCardOut. SQLAlchemy doesn't persist them.

        Five batched queries:
          1. assigned_agent_id  → agent name + role
          2. lead_id            → pending+overdue task count
          3. lead_id            → manual call (call_type='live') count
          4. lead_id            → stage-log-with-remark count
          5. lead_id            → has_active_ai_campaign (set membership)
        """
        if not leads:
            return

        lead_ids = [l.id for l in leads]
        # Union assigned agents + pre-counsellors into one profile lookup so
        # the FMC tile can render both names without an extra round trip.
        profile_ids = list(
            {l.assigned_agent_id for l in leads if l.assigned_agent_id}
            | {l.pre_counsellor_id for l in leads if getattr(l, "pre_counsellor_id", None)}
        )

        # OPTIMIZATION: combine the 4 count queries (tasks / live-calls /
        # stage-log-notes / lead-banks) into ONE UNION ALL query plus
        # AI-call signals into another, so the Kanban refresh costs
        # 5 round-trips total instead of 9. Sequential on self.db (one
        # connection avoids pgbouncer session-mode limits in production).
        from app.models.lead_remark import LeadRemark
        from app.models.lead_bank import LeadBank
        from sqlalchemy import literal, union_all

        # 1. Agent name + role lookup (small, fast — only ~5-30 profile IDs)
        agent_map: dict[uuid.UUID, tuple[str, str]] = {}
        if profile_ids:
            rows = (await self.db.execute(
                select(Profile.id, Profile.full_name, Profile.role)
                .where(Profile.id.in_(profile_ids))
            )).all()
            agent_map = {r.id: (r.full_name, r.role) for r in rows}

        # 2. Unified counts query — 4 aggregations in one round-trip.
        # 'kind' discriminator splits the buckets in Python.
        task_q = (
            select(literal("task").label("kind"), Task.lead_id, func.count().label("n"))
            .where(
                Task.company_id == self.company_id,
                Task.lead_id.in_(lead_ids),
                Task.status.in_([TaskStatus.PENDING.value, TaskStatus.OVERDUE.value]),
            )
            .group_by(Task.lead_id)
        )
        call_q = (
            select(literal("call").label("kind"), CallAttempt.lead_id, func.count().label("n"))
            .where(
                CallAttempt.company_id == self.company_id,
                CallAttempt.lead_id.in_(lead_ids),
                CallAttempt.call_type == "live",
            )
            .group_by(CallAttempt.lead_id)
        )
        notes_q = (
            select(literal("notes").label("kind"), LeadStageLog.lead_id, func.count().label("n"))
            .where(
                LeadStageLog.company_id == self.company_id,
                LeadStageLog.lead_id.in_(lead_ids),
                LeadStageLog.conversation_notes.isnot(None),
                func.length(LeadStageLog.conversation_notes) > 0,
            )
            .group_by(LeadStageLog.lead_id)
        )
        union_counts = union_all(task_q, call_q, notes_q)
        count_rows = (await self.db.execute(union_counts)).all()
        task_count_map: dict[uuid.UUID, int] = {}
        call_count_map: dict[uuid.UUID, int] = {}
        notes_count_map: dict[uuid.UUID, int] = {}
        for kind, lid, n in count_rows:
            if kind == "task": task_count_map[lid] = n
            elif kind == "call": call_count_map[lid] = n
            elif kind == "notes": notes_count_map[lid] = n

        # 3. Latest remark per lead (chronological feed)
        latest_remarks = (await self.db.execute(
            select(
                LeadRemark.lead_id, LeadRemark.body, LeadRemark.created_at,
                LeadRemark.author_id, LeadRemark.author_role,
                Profile.full_name.label("author_name"),
            )
            .outerjoin(Profile, Profile.id == LeadRemark.author_id)
            .where(
                LeadRemark.company_id == self.company_id,
                LeadRemark.lead_id.in_(lead_ids),
            )
            .order_by(LeadRemark.lead_id, LeadRemark.created_at.desc())
            .distinct(LeadRemark.lead_id)
        )).all()

        # 4. Latest stage-log note per lead (merged with remarks below)
        latest_stagelog_notes = (await self.db.execute(
            select(
                LeadStageLog.lead_id, LeadStageLog.conversation_notes,
                LeadStageLog.created_at, LeadStageLog.changed_by,
                Profile.full_name.label("author_name"),
                Profile.role.label("author_role"),
            )
            .outerjoin(Profile, Profile.id == LeadStageLog.changed_by)
            .where(
                LeadStageLog.company_id == self.company_id,
                LeadStageLog.lead_id.in_(lead_ids),
                LeadStageLog.conversation_notes.isnot(None),
                func.length(LeadStageLog.conversation_notes) > 0,
            )
            .order_by(LeadStageLog.lead_id, LeadStageLog.created_at.desc())
            .distinct(LeadStageLog.lead_id)
        )).all()

        # 5. All bank entries (count derived in Python — saves a round-trip
        # vs the previous count + entries pair).
        all_banks = (await self.db.execute(
            select(LeadBank)
            .where(LeadBank.company_id == self.company_id, LeadBank.lead_id.in_(lead_ids))
        )).scalars().all()

        # 6. AI signals (active campaign + ai_campaign call history) combined
        # via UNION so it's one round-trip instead of two. Result rows are
        # just lead_ids; we drop them into a set.
        active_q = (
            select(literal("camp").label("kind"), CampaignLead.lead_id).distinct()
            .where(
                CampaignLead.company_id == self.company_id,
                CampaignLead.lead_id.in_(lead_ids),
                CampaignLead.status.in_(["pending", "queued", "calling"]),
            )
        )
        ai_q = (
            select(literal("ai").label("kind"), CallAttempt.lead_id).distinct()
            .where(
                CallAttempt.company_id == self.company_id,
                CallAttempt.lead_id.in_(lead_ids),
                CallAttempt.call_type.in_(["ai", "ai_campaign"]),
            )
        )
        ai_signal_rows = (await self.db.execute(union_all(active_q, ai_q))).all()
        active_rows = [(r[1],) for r in ai_signal_rows if r[0] == "camp"]
        ai_call_rows = [(r[1],) for r in ai_signal_rows if r[0] == "ai"]

        # Merge: take whichever is newer per lead
        latest_note_map: dict[uuid.UUID, dict] = {}
        for r in latest_remarks:
            latest_note_map[r.lead_id] = {
                "body": r.body,
                "author_name": r.author_name,
                "author_role": r.author_role or "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "_created_at_raw": r.created_at,
            }
        for s in latest_stagelog_notes:
            existing = latest_note_map.get(s.lead_id)
            if not existing or (s.created_at and existing["_created_at_raw"] and s.created_at > existing["_created_at_raw"]):
                latest_note_map[s.lead_id] = {
                    "body": s.conversation_notes,
                    "author_name": s.author_name,
                    "author_role": s.author_role or "",
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "_created_at_raw": s.created_at,
                }
        # Strip the helper key before serialisation
        for v in latest_note_map.values():
            v.pop("_created_at_raw", None)

        # Bank rollups computed in Python from the all_banks rowset
        # fetched above (saves one round-trip vs the previous separate
        # count + entries queries). Order = status priority desc, then
        # created_at asc as tie-break (stable as new banks are added).
        priority = self._BANK_STATUS_PRIORITY  # local alias
        banks_by_lead: dict[uuid.UUID, list] = {}
        for b in all_banks:
            banks_by_lead.setdefault(b.lead_id, []).append(b)
        bank_count_map = {lid: len(v) for lid, v in banks_by_lead.items()}
        top_banks_map: dict[uuid.UUID, list[dict]] = {}
        for lid, entries in banks_by_lead.items():
            entries.sort(key=lambda e: (-priority.get(e.bank_status, 0), e.created_at))
            top_banks_map[lid] = [
                {
                    "id": str(e.id),
                    "bank_name": e.bank_name,
                    "bank_status": e.bank_status,
                }
                for e in entries[:2]
            ]

        # AI-call watermark: lead has an active campaign row OR an ai/ai_campaign
        # call_attempt. Without the second arm, the watermark vanished as soon as
        # a campaign finished even though the lead clearly had been AI-contacted.
        active_campaign_set = {r[0] for r in active_rows} | {r[0] for r in ai_call_rows if r[0]}

        # Decorate each Lead instance with the rollups. Setattr is fine
        # — these are not mapped columns; SQLAlchemy ignores them on
        # commit. Pydantic from_attributes reads them when serializing.
        for lead in leads:
            agent = agent_map.get(lead.assigned_agent_id) if lead.assigned_agent_id else None
            lead.assigned_agent_name = agent[0] if agent else None
            lead.assigned_agent_role = agent[1] if agent else None
            pre = agent_map.get(lead.pre_counsellor_id) if getattr(lead, "pre_counsellor_id", None) else None
            lead.pre_counsellor_name = pre[0] if pre else None
            lead.task_count = task_count_map.get(lead.id, 0)
            lead.call_count = call_count_map.get(lead.id, 0)
            lead.notes_count = notes_count_map.get(lead.id, 0)
            lead.bank_count = bank_count_map.get(lead.id, 0)
            lead.top_banks = top_banks_map.get(lead.id, [])
            lead.latest_note = latest_note_map.get(lead.id)
            lead.has_active_ai_campaign = lead.id in active_campaign_set

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
            query = query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))

        return await paginate(self.db, query, page, page_size)

    # ─── Multi-bank tracking ────────────────────────────────────────────
    # Status priority for auto-syncing lead.bank_name / lead.bank_status
    # to the "best" entry across the lead's banks.
    _BANK_STATUS_PRIORITY = {
        "disbursed": 7, "pf_paid": 6, "sanctioned": 5, "loan_login": 4,
        "under_review": 3, "docs_reviewed": 2, "applied": 1,
    }
    _BANK_VALID_STATUSES = set(_BANK_STATUS_PRIORITY.keys())

    async def _resync_primary_bank(self, lead: Lead) -> None:
        """After any add/update/delete on lead_banks, refresh lead.bank_name
        and lead.bank_status to point at the highest-priority entry. Falls
        back to NULL if the lead has no entries.
        """
        from app.models.lead_bank import LeadBank
        rows = (await self.db.execute(
            select(LeadBank).where(LeadBank.lead_id == lead.id)
        )).scalars().all()
        if not rows:
            lead.bank_name = None
            lead.bank_status = None
            return
        best = max(rows, key=lambda r: (self._BANK_STATUS_PRIORITY.get(r.bank_status, 0), r.updated_at))
        lead.bank_name = best.bank_name
        lead.bank_status = best.bank_status

    async def list_banks(self, lead_id: uuid.UUID, user: Profile) -> list:
        """Return all bank entries for a lead, ordered by created_at desc."""
        from app.models.lead_bank import LeadBank
        await self.get_lead(lead_id, user)
        rows = (await self.db.execute(
            select(LeadBank)
            .where(LeadBank.lead_id == lead_id, LeadBank.company_id == self.company_id)
            .order_by(LeadBank.created_at.desc())
        )).scalars().all()
        return list(rows)

    async def add_bank(self, lead_id: uuid.UUID, bank_name: str, bank_status: str, notes: str | None, user: Profile):
        """Add a bank entry to a lead. Bank name must be in the canonical
        FMC list; status must be a valid bank_status enum value; a lead
        can't have the same bank twice (DB unique constraint backstops
        the service check)."""
        from app.models.lead_bank import LeadBank
        from app.core.constants import FMC_BANKS
        if bank_name not in FMC_BANKS:
            raise BadRequestError(
                f"bank_name must be one of the canonical FMC banks (got '{bank_name}'). See GET /leads/banks."
            )
        if bank_status not in self._BANK_VALID_STATUSES:
            raise BadRequestError(
                f"bank_status must be one of {sorted(self._BANK_VALID_STATUSES)} (got '{bank_status}')."
            )

        lead = await self.get_lead(lead_id, user)

        # Pre-check for dup (cleaner error than catching the IntegrityError)
        existing = (await self.db.execute(
            select(LeadBank.id).where(
                LeadBank.lead_id == lead_id,
                LeadBank.bank_name == bank_name,
            )
        )).scalar_one_or_none()
        if existing:
            raise BadRequestError(
                f"This lead already has an entry for '{bank_name}'. "
                f"Use PATCH /leads/{{id}}/banks/{{entry_id}} to update its status."
            )

        entry = LeadBank(
            company_id=self.company_id,
            lead_id=lead_id,
            bank_name=bank_name,
            bank_status=bank_status,
            notes=notes,
        )
        self.db.add(entry)
        await self.db.flush()
        await self._resync_primary_bank(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def update_bank_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, bank_status: str | None, notes: str | None, user: Profile):
        from app.models.lead_bank import LeadBank
        from app.utils.date_helpers import now_utc
        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadBank).where(
                LeadBank.id == entry_id,
                LeadBank.lead_id == lead_id,
                LeadBank.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Bank entry not found")
        if bank_status is not None:
            if bank_status not in self._BANK_VALID_STATUSES:
                raise BadRequestError(
                    f"bank_status must be one of {sorted(self._BANK_VALID_STATUSES)} (got '{bank_status}')."
                )
            entry.bank_status = bank_status
        if notes is not None:
            entry.notes = notes
        entry.updated_at = now_utc()
        await self.db.flush()
        await self._resync_primary_bank(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def delete_bank_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, user: Profile) -> None:
        from app.models.lead_bank import LeadBank
        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadBank).where(
                LeadBank.id == entry_id,
                LeadBank.lead_id == lead_id,
                LeadBank.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Bank entry not found")
        await self.db.delete(entry)
        await self.db.flush()
        await self._resync_primary_bank(lead)
        await self.db.commit()

    async def add_remark(self, lead_id: uuid.UUID, body: str, user: Profile) -> dict:
        """Add a free-form remark to a lead. Access gated by get_lead
        (which enforces the assigned-agent / pre-counsellor / admin rules).
        Returns a dict matching LeadRemarkOut shape, with enriched author_name.
        """
        from app.models.lead_remark import LeadRemark
        # get_lead enforces permission — re-use it.
        await self.get_lead(lead_id, user)

        remark = LeadRemark(
            company_id=self.company_id,
            lead_id=lead_id,
            author_id=user.id,
            author_role=user.role,
            body=body,
        )
        self.db.add(remark)
        await self.db.flush()
        await self.db.commit()
        return {
            "id": remark.id,
            "lead_id": remark.lead_id,
            "author_id": remark.author_id,
            "author_name": user.full_name,
            "author_role": remark.author_role,
            "body": remark.body,
            "created_at": remark.created_at,
        }

    async def list_remarks(self, lead_id: uuid.UUID, user: Profile) -> list[dict]:
        """List all remarks on a lead, newest first. Access gated by
        get_lead so a restricted user can't read remarks on leads they
        don't own. Author names are enriched with one batched profile
        lookup.
        """
        from app.models.lead_remark import LeadRemark
        await self.get_lead(lead_id, user)

        rows = (await self.db.execute(
            select(LeadRemark)
            .where(
                LeadRemark.lead_id == lead_id,
                LeadRemark.company_id == self.company_id,
            )
            .order_by(LeadRemark.created_at.desc())
        )).scalars().all()

        author_ids = list({r.author_id for r in rows if r.author_id})
        names: dict[uuid.UUID, str] = {}
        if author_ids:
            name_rows = (await self.db.execute(
                select(Profile.id, Profile.full_name).where(Profile.id.in_(author_ids))
            )).all()
            names = {row.id: row.full_name for row in name_rows}

        return [{
            "id": r.id,
            "lead_id": r.lead_id,
            "author_id": r.author_id,
            "author_name": names.get(r.author_id) if r.author_id else None,
            "author_role": r.author_role,
            "body": r.body,
            "created_at": r.created_at,
        } for r in rows]

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
