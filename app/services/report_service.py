from __future__ import annotations

import uuid
from datetime import timedelta
from collections import defaultdict
from sqlalchemy import select, func, case, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.profile import Profile
from app.models.call_attempt import CallAttempt
from app.models.task import Task
from app.models.lead_source import LeadSource
from app.models.company import Company
from app.models.lead_stage_log import LeadStageLog
from app.core.constants import (
    LeadStage, TaskStatus, CallDisposition, UserRole,
    ADMITVERSE_STAGES, FMC_STAGES, RESTRICTED_VIEW_ROLES,
)
from app.core.exceptions import ForbiddenError, ForbiddenError as _Forbidden
from app.utils.date_helpers import now_utc, now_ist, start_of_today, end_of_today, IST


# Hardcoded daily call targets per role (v1). Once Profile gets a
# `daily_call_target` column, the per-user value will override these.
_DEFAULT_CALL_TARGET = {
    UserRole.PRE_COUNSELLOR: 50,
    UserRole.MANAGER: 30,
    UserRole.ADMIN: None,  # admins don't have a target
}


# Stages that count as "won" in the conversion-rate denominator. Each brand
# has its own happy state — FMC closes deals at WON, Admitverse closes at
# ENROLLED. Without this map every Admitverse report would show 0% forever
# because no Admitverse lead ever reaches `won`.
# NOTE: the live FMC tenant's slug is "default" (name "FundMyCampus"), so
# the fallback below — not the explicit keys — is what FMC actually hits.
# Keep DISBURSED as the fallback: every non-Admitverse tenant is FMC-style
# and closes at DISBURSED, matching get_terminal_stages_for_brand().
_BRAND_WON_STAGE = {
    "fmc": LeadStage.DISBURSED,
    "fundmycampus": LeadStage.DISBURSED,
    "default": LeadStage.DISBURSED,
    "admitverse": LeadStage.ENROLLED,
}


class ReportService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id
        self._slug: str | None = None

    async def _get_slug(self) -> str | None:
        if self._slug is not None:
            return self._slug
        result = await self.db.execute(
            select(Company.slug).where(Company.id == self.company_id)
        )
        self._slug = result.scalar_one_or_none()
        return self._slug

    async def _won_stage(self) -> LeadStage:
        slug = (await self._get_slug() or "").lower()
        # Fallback DISBURSED (not the legacy WON): any non-Admitverse tenant
        # — including slug "default" — is FMC-style and closes at DISBURSED.
        return _BRAND_WON_STAGE.get(slug, LeadStage.DISBURSED)

    @staticmethod
    def _conversion_rate(leads_by_stage: dict, won_stage: LeadStage) -> float:
        """Wins / closed deals (won + lost). Same denominator across brands —
        only the numerator's stage label changes per brand.
        """
        won = leads_by_stage.get(won_stage.value, 0) or leads_by_stage.get(won_stage, 0)
        lost = leads_by_stage.get(LeadStage.LOST.value, 0) or leads_by_stage.get(LeadStage.LOST, 0)
        closed = won + lost
        return (won / closed * 100) if closed > 0 else 0.0

    @staticmethod
    def _restricted_user_id(user) -> uuid.UUID | None:
        """Return the user.id if their role should restrict report data
        to only-their-own, else None (admins see everything).

        Used to scope dashboard / pipeline / sources / trends / task
        compliance for managers and telecallers in the isolated-portfolio
        model. None disables the filter.
        """
        if user is None:
            return None
        if user.role in RESTRICTED_VIEW_ROLES:
            return user.id
        return None

    # ── Dashboard: 9 queries → 3 ──────────────────────────────────────

    async def dashboard(self, user=None) -> dict:
        today_start = start_of_today()
        restricted_id = self._restricted_user_id(user)

        # Query 1: All lead stats in one query (total, new today, by stage).
        # Soft-deleted leads must not inflate dashboard totals.
        lead_q = select(
            Lead.current_stage,
            func.count().label("cnt"),
            func.count(case((Lead.created_at >= today_start, 1))).label("new_today"),
        ).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if restricted_id is not None:
            lead_q = lead_q.where(Lead.assigned_agent_id == restricted_id)
        lead_q = lead_q.group_by(Lead.current_stage)
        lead_rows = (await self.db.execute(lead_q)).all()

        leads_by_stage = {}
        total = 0
        new_today = 0
        for row in lead_rows:
            leads_by_stage[row.current_stage] = row.cnt
            total += row.cnt
            new_today += row.new_today

        # Query 2: All task stats in one query
        task_q = select(
            func.count(case((Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]), 1))).label("pending"),
            func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
            func.count(case((
                (Task.status == TaskStatus.COMPLETED) & (Task.completed_at >= today_start), 1
            ))).label("completed_today"),
        ).where(Task.company_id == self.company_id)
        if restricted_id is not None:
            task_q = task_q.where(Task.assigned_to == restricted_id)
        task_row = (await self.db.execute(task_q)).one()

        # Query 3: Agent counts. For admins this is the company-wide
        # telecaller count; for restricted users (manager / telecaller in
        # the isolated-portfolio model) it doesn't really apply — they
        # only see their own data — so we report 1/1 (themselves).
        if restricted_id is not None:
            agent_total = 1
            agent_active = 1 if user.is_active else 0
        else:
            agent_row = (await self.db.execute(
                select(
                    func.count().label("total"),
                    func.count(case((Profile.is_active == True, 1))).label("active"),
                ).where(Profile.role == UserRole.PRE_COUNSELLOR, Profile.company_id == self.company_id)
            )).one()
            agent_total = agent_row.total
            agent_active = agent_row.active

        # Win-rate of closed deals, not "wins as a fraction of every lead in the
        # pipeline". The latter looked permanently near-zero because a healthy
        # pipeline is mostly open leads.
        won_stage = await self._won_stage()
        conversion_rate = self._conversion_rate(leads_by_stage, won_stage)

        return {
            "total_leads": total,
            "new_leads_today": new_today,
            "leads_by_stage": leads_by_stage,
            "total_agents": agent_total,
            "active_agents": agent_active,
            "tasks_pending": task_row.pending,
            "tasks_overdue": task_row.overdue,
            "tasks_completed_today": task_row.completed_today,
            "conversion_rate": round(conversion_rate, 2),
        }

    # ── Pipeline (already efficient) ───────────────────────────────────

    async def pipeline(self, user=None) -> dict:
        restricted_id = self._restricted_user_id(user)
        q = select(Lead.current_stage, func.count()).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if restricted_id is not None:
            q = q.where(Lead.assigned_agent_id == restricted_id)
        stage_counts = (await self.db.execute(q.group_by(Lead.current_stage))).all()

        total = sum(count for _, count in stage_counts)

        # Show only stages that belong to this brand's pipeline. Without
        # this filter, FMC users would see Admitverse-only stages with 0
        # counts and vice-versa — clutter that makes the funnel look broken.
        slug = (await self._get_slug() or "").lower()
        if slug == "admitverse":
            relevant = ADMITVERSE_STAGES
        else:
            # FMC's May 2026 revamp replaced the legacy 6-stage funnel
            # (lead/called/connected/qualified_lead/won) with FMC_STAGES.
            # Use the live list so the funnel reflects the real pipeline.
            relevant = FMC_STAGES

        stages = []
        for stage in relevant:
            count = next((c for s, c in stage_counts if s == stage.value), 0)
            stages.append({
                "stage": stage.value,
                "count": count,
                "percentage": round((count / total * 100) if total > 0 else 0, 2),
            })

        return {"stages": stages, "total": total}

    # ── Agents Summary: 81 queries → 4 ────────────────────────────────

    async def agents_summary(self, user=None) -> list[dict]:
        # Restricted users (manager / telecaller) only see their own row in
        # the team summary. Admins see every telecaller in the company.
        restricted_id = self._restricted_user_id(user)
        if restricted_id is not None:
            result = await self.db.execute(
                select(Profile).where(
                    Profile.id == restricted_id,
                    Profile.company_id == self.company_id,
                )
            )
        else:
            result = await self.db.execute(
                select(Profile).where(
                    Profile.role == UserRole.PRE_COUNSELLOR,
                    Profile.company_id == self.company_id,
                ).order_by(Profile.full_name)
            )
        agents = result.scalars().all()
        if not agents:
            return []

        agent_ids = [a.id for a in agents]

        # Batch query 1: Leads by stage per agent (skip soft-deleted).
        lead_rows = (await self.db.execute(
            select(
                Lead.assigned_agent_id,
                Lead.current_stage,
                func.count().label("cnt"),
            )
            .where(
                Lead.assigned_agent_id.in_(agent_ids),
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
            )
            .group_by(Lead.assigned_agent_id, Lead.current_stage)
        )).all()

        # Build lookup: {agent_id: {stage: count}}
        lead_map: dict[uuid.UUID, dict] = defaultdict(dict)
        for row in lead_rows:
            lead_map[row.assigned_agent_id][row.current_stage] = row.cnt

        # Batch query 2: Call stats per agent.
        # call_status is the authoritative outcome (set by webhooks). disposition
        # is hardcoded to "connected" by the campaign worker for AI calls, so
        # using it for connected/dnp would silently inflate AI campaigns and
        # hide DNP entirely. Use started_at (caller picked up) for connected.
        call_rows = (await self.db.execute(
            select(
                CallAttempt.agent_id,
                func.count().label("total"),
                func.count(case((CallAttempt.disposition == CallDisposition.DNP, 1))).label("dnp"),
                func.count(case((CallAttempt.started_at.isnot(None), 1))).label("connected"),
            )
            .where(
                CallAttempt.agent_id.in_(agent_ids),
                CallAttempt.company_id == self.company_id,
            )
            .group_by(CallAttempt.agent_id)
        )).all()

        call_map = {}
        for row in call_rows:
            call_map[row.agent_id] = {
                "total": row.total, "dnp": row.dnp, "connected": row.connected,
            }

        # Batch query 3: Task stats per agent
        task_rows = (await self.db.execute(
            select(
                Task.assigned_to,
                func.count().label("total"),
                func.count(case((Task.status == TaskStatus.COMPLETED, 1))).label("completed"),
                func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
            )
            .where(
                Task.assigned_to.in_(agent_ids),
                Task.company_id == self.company_id,
            )
            .group_by(Task.assigned_to)
        )).all()

        task_map = {}
        for row in task_rows:
            task_map[row.assigned_to] = {
                "total": row.total, "completed": row.completed, "overdue": row.overdue,
            }

        won_stage = await self._won_stage()

        # Assemble results
        summaries = []
        for agent in agents:
            leads_by_stage = lead_map.get(agent.id, {})
            total_leads = sum(leads_by_stage.values())
            calls = call_map.get(agent.id, {"total": 0, "dnp": 0, "connected": 0})
            tasks = task_map.get(agent.id, {"total": 0, "completed": 0, "overdue": 0})

            # Win-rate among closed deals (won + lost), not pipeline-wide.
            conversion_rate = self._conversion_rate(leads_by_stage, won_stage)

            summaries.append({
                "agent_id": agent.id,
                "agent_name": agent.full_name,
                "total_leads": total_leads,
                "leads_by_stage": leads_by_stage,
                "total_calls": calls["total"],
                "dnp_calls": calls["dnp"],
                "connected_calls": calls["connected"],
                "total_tasks": tasks["total"],
                "completed_tasks": tasks["completed"],
                "overdue_tasks": tasks["overdue"],
                "conversion_rate": round(conversion_rate, 2),
            })
        return summaries

    # ── Agent Detail (single agent — keep individual queries) ──────────

    async def agent_detail(self, agent_id: uuid.UUID, user=None) -> dict:
        # Restricted users (manager / telecaller) can only view their own
        # agent stats — viewing other users' performance breaks the
        # isolated-portfolio model.
        restricted_id = self._restricted_user_id(user)
        if restricted_id is not None and agent_id != restricted_id:
            raise ForbiddenError("You can only view your own performance")
        result = await self.db.execute(
            select(Profile).where(Profile.id == agent_id, Profile.company_id == self.company_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Agent not found")

        # For a single agent, use the same batch approach with 1 agent.
        # Tenant filter is required even though agent.company_id == self.company_id
        # — joined tables (leads, calls, tasks) must enforce tenant isolation
        # independently in case agent_id is ever reused across tenants.
        stage_counts = (await self.db.execute(
            select(Lead.current_stage, func.count())
            .where(
                Lead.assigned_agent_id == agent.id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
            )
            .group_by(Lead.current_stage)
        )).all()
        leads_by_stage = {stage: count for stage, count in stage_counts}
        total_leads = sum(leads_by_stage.values())

        # Same call_status / started_at fix as agents_summary above.
        call_row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((CallAttempt.disposition == CallDisposition.DNP, 1))).label("dnp"),
                func.count(case((CallAttempt.started_at.isnot(None), 1))).label("connected"),
            ).where(
                CallAttempt.agent_id == agent.id,
                CallAttempt.company_id == self.company_id,
            )
        )).one()

        task_row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((Task.status == TaskStatus.COMPLETED, 1))).label("completed"),
                func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
            ).where(
                Task.assigned_to == agent.id,
                Task.company_id == self.company_id,
            )
        )).one()

        # Win-rate among closed deals.
        won_stage = await self._won_stage()
        conversion_rate = self._conversion_rate(leads_by_stage, won_stage)

        return {
            "agent_id": agent.id,
            "agent_name": agent.full_name,
            "total_leads": total_leads,
            "leads_by_stage": leads_by_stage,
            "total_calls": call_row.total,
            "dnp_calls": call_row.dnp,
            "connected_calls": call_row.connected,
            "total_tasks": task_row.total,
            "completed_tasks": task_row.completed,
            "overdue_tasks": task_row.overdue,
            "conversion_rate": round(conversion_rate, 2),
        }

    # ── Sources: 61 queries → 1 ───────────────────────────────────────

    async def sources(self, user=None) -> list[dict]:
        # Move tenant + soft-delete filters into the JOIN's ON clause so
        # sources with zero matching leads still appear (they keep total=0,
        # not vanish). Putting these filters in WHERE would convert the
        # outer join into an inner join and hide unused sources.
        won_stage = await self._won_stage()
        restricted_id = self._restricted_user_id(user)
        join_cond = (
            (Lead.lead_source_id == LeadSource.id)
            & (Lead.company_id == self.company_id)
            & (Lead.is_deleted == False)  # noqa: E712
        )
        if restricted_id is not None:
            join_cond = join_cond & (Lead.assigned_agent_id == restricted_id)
        rows = (await self.db.execute(
            select(
                LeadSource.id,
                LeadSource.name,
                func.count(Lead.id).label("total"),
                func.count(case((Lead.current_stage == won_stage.value, Lead.id))).label("won"),
                func.count(case((Lead.current_stage == LeadStage.LOST.value, Lead.id))).label("lost"),
            )
            .outerjoin(Lead, join_cond)
            .where(LeadSource.company_id == self.company_id)
            .group_by(LeadSource.id, LeadSource.name)
            .order_by(LeadSource.name)
        )).all()

        stats = []
        for row in rows:
            # Conversion = wins / closed deals from this source.
            closed = (row.won or 0) + (row.lost or 0)
            conv = (row.won / closed * 100) if closed > 0 else 0.0
            stats.append({
                "source_id": row.id,
                "source_name": row.name,
                "total_leads": row.total or 0,
                "won": row.won or 0,
                "lost": row.lost or 0,
                "conversion_rate": round(conv, 2),
            })
        return stats

    # ── Task Compliance: 4 queries → 1 ────────────────────────────────

    async def task_compliance(self, user=None) -> dict:
        restricted_id = self._restricted_user_id(user)
        q = select(
            func.count().label("total"),
            func.count(case((Task.status == TaskStatus.COMPLETED, 1))).label("completed"),
            func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
            func.count(case((Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]), 1))).label("pending"),
        ).where(Task.company_id == self.company_id)
        if restricted_id is not None:
            q = q.where(Task.assigned_to == restricted_id)
        row = (await self.db.execute(q)).one()

        return {
            "total_tasks": row.total,
            "completed": row.completed,
            "overdue": row.overdue,
            "pending": row.pending,
            "compliance_rate": round((row.completed / row.total * 100) if row.total > 0 else 0, 2),
        }

    # ── Trends: 120 queries → 4 ───────────────────────────────────────

    async def trends(self, days: int = 30, user=None) -> list[dict]:
        now = now_utc()
        start_date = (now - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        restricted_id = self._restricted_user_id(user)

        # Query 1: New leads by date (skip soft-deleted)
        new_q = select(
            cast(Lead.created_at, Date).label("day"),
            func.count().label("cnt"),
        ).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
            Lead.created_at >= start_date,
        )
        if restricted_id is not None:
            new_q = new_q.where(Lead.assigned_agent_id == restricted_id)
        new_rows = (await self.db.execute(new_q.group_by(cast(Lead.created_at, Date)))).all()

        # Query 2: Won leads by date (skip soft-deleted)
        won_q = select(
            cast(Lead.won_time, Date).label("day"),
            func.count().label("cnt"),
        ).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
            Lead.won_time >= start_date,
        )
        if restricted_id is not None:
            won_q = won_q.where(Lead.assigned_agent_id == restricted_id)
        won_rows = (await self.db.execute(won_q.group_by(cast(Lead.won_time, Date)))).all()

        # Query 3: Lost leads by date (skip soft-deleted)
        lost_q = select(
            cast(Lead.lost_time, Date).label("day"),
            func.count().label("cnt"),
        ).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
            Lead.lost_time >= start_date,
        )
        if restricted_id is not None:
            lost_q = lost_q.where(Lead.assigned_agent_id == restricted_id)
        lost_rows = (await self.db.execute(lost_q.group_by(cast(Lead.lost_time, Date)))).all()

        # Query 4: Calls by date — for restricted users, only their own calls
        call_q = select(
            cast(CallAttempt.created_at, Date).label("day"),
            func.count().label("cnt"),
        ).where(
            CallAttempt.company_id == self.company_id,
            CallAttempt.created_at >= start_date,
        )
        if restricted_id is not None:
            call_q = call_q.where(CallAttempt.agent_id == restricted_id)
        call_rows = (await self.db.execute(call_q.group_by(cast(CallAttempt.created_at, Date)))).all()

        # Build lookup dicts
        new_map = {str(r.day): r.cnt for r in new_rows}
        won_map = {str(r.day): r.cnt for r in won_rows}
        lost_map = {str(r.day): r.cnt for r in lost_rows}
        call_map = {str(r.day): r.cnt for r in call_rows}

        # Assemble results for each day
        results = []
        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            results.append({
                "date": day_str,
                "new_leads": new_map.get(day_str, 0),
                "won": won_map.get(day_str, 0),
                "lost": lost_map.get(day_str, 0),
                "calls_made": call_map.get(day_str, 0),
            })

        return results

    # ── Daily activity report ─────────────────────────────────────────
    # "How much did each user do today?" — a per-user breakdown of
    # calls, lead movements, tasks, and leads created. Calendar day in
    # IST (00:00 to 24:00 IST). AI campaign calls are excluded from the
    # call counts because they're dispatched by the bot, not the user
    # — counting them would inflate telecaller stats.

    @staticmethod
    def _ist_day_bounds(date_str: str) -> tuple:
        """Convert a YYYY-MM-DD IST date into a (start_utc, end_utc) range
        the database can filter on. The DB stores timestamps in UTC; the
        user thinks in IST; this is the only place the conversion lives."""
        from datetime import date as _date_cls, datetime as _dt, timezone as _tz
        d = _date_cls.fromisoformat(date_str)
        start_ist = _dt(d.year, d.month, d.day, 0, 0, 0, tzinfo=IST)
        end_ist = start_ist + timedelta(days=1)
        return start_ist.astimezone(_tz.utc), end_ist.astimezone(_tz.utc)

    async def _compute_user_day_metrics(
        self, user_id: uuid.UUID, start_utc, end_utc,
    ) -> dict:
        """One user, one IST day → all metrics. Used by both the daily
        report and the 30-day range. Five DB queries total per call."""
        # Calls (manual only — AI/campaign calls are excluded so the
        # telecaller's stats aren't padded by the bot).
        # call_type values in this DB:
        #   "live"        — manual call logged by telecaller (counted)
        #   "ai"          — AI call from voice/outbound (excluded)
        #   "ai_campaign" — campaign worker dial (excluded)
        # Falling back to attempt timestamps means a half-logged manual
        # call still gets counted; an AI call without a transcript
        # doesn't.
        # Window on created_at, not started_at: manual calls don't
        # populate started_at (only AI calls do), so filtering on
        # started_at would drop every telecaller's manual call.
        # created_at is the server-default insert timestamp — always set.
        call_q = select(
            func.count().label("made"),
            func.count(case(
                (
                    (CallAttempt.transcript.isnot(None))
                    | (CallAttempt.call_duration_seconds > 10),
                    1,
                )
            )).label("connected"),
            func.coalesce(func.sum(CallAttempt.call_duration_seconds), 0).label("duration_seconds"),
        ).where(
            CallAttempt.company_id == self.company_id,
            CallAttempt.telecaller_id == user_id,
            CallAttempt.call_type == "live",
            CallAttempt.created_at >= start_utc,
            CallAttempt.created_at < end_utc,
        )
        call_row = (await self.db.execute(call_q)).one()

        # Leads created by this user (manual entry — CSV imports are
        # bulk and the system records the importer, but those leads
        # aren't really "created by hand").
        leads_created_q = select(func.count()).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
            Lead.created_by == user_id,
            Lead.created_at >= start_utc,
            Lead.created_at < end_utc,
        )
        leads_created = (await self.db.execute(leads_created_q)).scalar() or 0

        # Pipeline movements (every stage transition done by this user
        # in the day, grouped by destination stage).
        moves_q = select(
            LeadStageLog.to_stage,
            func.count().label("cnt"),
        ).where(
            LeadStageLog.company_id == self.company_id,
            LeadStageLog.changed_by == user_id,
            LeadStageLog.created_at >= start_utc,
            LeadStageLog.created_at < end_utc,
        ).group_by(LeadStageLog.to_stage)
        moves_rows = (await self.db.execute(moves_q)).all()
        transitions_by_stage = {r.to_stage: r.cnt for r in moves_rows}
        transitions_total = sum(transitions_by_stage.values())

        # Convenience counts derived from the breakdown.
        won_stage = await self._won_stage()
        leads_won = transitions_by_stage.get(won_stage.value, 0)
        leads_lost = transitions_by_stage.get(LeadStage.LOST.value, 0)

        # Tasks created (this user authored) and completed (this user
        # owned and completed today). Two separate semantics on purpose:
        # creating tasks for others vs. clearing your own queue.
        tasks_created_q = select(func.count()).where(
            Task.company_id == self.company_id,
            Task.created_by == user_id,
            Task.created_at >= start_utc,
            Task.created_at < end_utc,
        )
        tasks_created = (await self.db.execute(tasks_created_q)).scalar() or 0

        tasks_completed_q = select(func.count()).where(
            Task.company_id == self.company_id,
            Task.assigned_to == user_id,
            Task.status == TaskStatus.COMPLETED.value,
            Task.completed_at >= start_utc,
            Task.completed_at < end_utc,
        )
        tasks_completed = (await self.db.execute(tasks_completed_q)).scalar() or 0

        # Calls IMPLIED — per-day proxy for call activity when there's no
        # dialer wired. One distinct lead touched by this user today =
        # one call. Same lead touched 5 times (multiple stage moves +
        # task updates) still counts as 1 for the day. AI-driven stage
        # transitions are excluded — they'd inflate stats with bot work.
        #
        # A "touch" today is any of:
        #   v1   — a stage transition by this user (LeadStageLog row)
        #   v1.5 — a DNP task completed by this user
        #   v1.5 — a DNP task whose due_date was changed by this user
        #          (proxy: Task.updated_at after Task.created_at on a DNP lead)
        # All three union into a distinct lead-id set.

        # 1. Distinct leads with stage transitions today (excl AI auto)
        ai_marker = "Auto-transition by post-call pipeline%"
        stage_leads_q = select(LeadStageLog.lead_id).distinct().where(
            LeadStageLog.company_id == self.company_id,
            LeadStageLog.changed_by == user_id,
            LeadStageLog.created_at >= start_utc,
            LeadStageLog.created_at < end_utc,
            (LeadStageLog.conversation_notes.is_(None))
            | (~LeadStageLog.conversation_notes.like(ai_marker)),
        )
        stage_leads = {r[0] for r in (await self.db.execute(stage_leads_q)).all()}

        # DNP stage(s) differ per brand: FMC uses the single `dnp` stage,
        # Admitverse splits it into pre/post-qualified. Without this the AV
        # "implied calls" proxy never counts any DNP follow-up work.
        slug = (await self._get_slug() or "").lower()
        if slug == "admitverse":
            dnp_stages = [
                LeadStage.DNP_PRE_QUALIFIED.value,
                LeadStage.DNP_POST_QUALIFIED.value,
            ]
        else:
            dnp_stages = [LeadStage.DNP.value]

        # 2. Distinct leads where this user completed a task on a DNP-stage
        # lead today.
        dnp_complete_q = select(Task.lead_id).distinct().select_from(Task).join(
            Lead, Lead.id == Task.lead_id
        ).where(
            Task.company_id == self.company_id,
            Task.assigned_to == user_id,
            Task.status == TaskStatus.COMPLETED.value,
            Task.completed_at >= start_utc,
            Task.completed_at < end_utc,
            Lead.current_stage.in_(dnp_stages),
        )
        dnp_complete_leads = {r[0] for r in (await self.db.execute(dnp_complete_q)).all() if r[0]}

        # 3. Distinct leads where this user updated a task on a DNP-stage
        # lead today (proxy for "DNP callback date changed"). 5-second
        # buffer skips the row's own creation timestamp from being
        # treated as an update.
        from datetime import timedelta as _td
        dnp_update_q = select(Task.lead_id).distinct().select_from(Task).join(
            Lead, Lead.id == Task.lead_id
        ).where(
            Task.company_id == self.company_id,
            Task.assigned_to == user_id,
            Task.updated_at.isnot(None),
            Task.updated_at >= start_utc,
            Task.updated_at < end_utc,
            Lead.current_stage.in_(dnp_stages),
        )
        dnp_update_leads = {r[0] for r in (await self.db.execute(dnp_update_q)).all() if r[0]}

        calls_implied = len(stage_leads | dnp_complete_leads | dnp_update_leads)

        return {
            "calls_made": call_row.made or 0,
            "calls_connected": call_row.connected or 0,
            "call_duration_minutes": round((call_row.duration_seconds or 0) / 60, 1),
            "calls_implied": calls_implied,
            "leads_created": leads_created,
            "transitions_total": transitions_total,
            "transitions_by_stage": transitions_by_stage,
            "leads_won": leads_won,
            "leads_lost": leads_lost,
            "tasks_created": tasks_created,
            "tasks_completed": tasks_completed,
        }

    async def _resolve_target_user(self, requesting_user, target_user_id) -> Profile:
        """Permission gate for daily-report queries.

        Telecaller / manager (v1) → can only query themselves.
        Admin → can query any user in the company.

        Manager → "own + team" view will be added once Profile gets a
        manager_id FK linking telecallers to their manager. For now
        managers see only their own day, same as telecallers.
        """
        if target_user_id is None or target_user_id == requesting_user.id:
            return requesting_user

        if requesting_user.role != UserRole.ADMIN:
            raise ForbiddenError("Only admin can query other users' reports")

        result = await self.db.execute(
            select(Profile).where(
                Profile.id == target_user_id,
                Profile.company_id == self.company_id,
            )
        )
        target = result.scalar_one_or_none()
        if not target:
            raise ForbiddenError("User not found in this company")
        return target

    async def daily_activity(
        self, *, requesting_user: Profile, target_user_id: uuid.UUID | None = None,
        date_str: str | None = None,
    ) -> dict:
        """Per-user daily activity report with comparison to yesterday.

        date_str is YYYY-MM-DD in IST. None = today (IST).
        target_user_id None or matching requesting_user.id → self-view.
        Non-self queries are admin-only until manager_id is wired.
        """
        target_user = await self._resolve_target_user(requesting_user, target_user_id)

        if date_str is None:
            date_str = now_ist().date().isoformat()

        start_utc, end_utc = self._ist_day_bounds(date_str)
        # Yesterday for delta — stay in IST so DST-style timezone bugs
        # can't make "yesterday" 23 or 25 hours.
        from datetime import date as _date_cls
        today = _date_cls.fromisoformat(date_str)
        yesterday = today - timedelta(days=1)
        prev_start, prev_end = self._ist_day_bounds(yesterday.isoformat())

        today_metrics = await self._compute_user_day_metrics(target_user.id, start_utc, end_utc)
        yesterday_metrics = await self._compute_user_day_metrics(target_user.id, prev_start, prev_end)

        # Delta — only on numeric scalar fields.
        deltas = {
            k: today_metrics[k] - yesterday_metrics[k]
            for k in today_metrics
            if isinstance(today_metrics[k], (int, float))
        }

        # Targets — hardcoded by role for v1.
        try:
            role_enum = UserRole(target_user.role)
        except ValueError:
            role_enum = None
        target_calls = _DEFAULT_CALL_TARGET.get(role_enum) if role_enum else None
        pct_of_target = None
        if target_calls and target_calls > 0:
            pct_of_target = round(today_metrics["calls_made"] / target_calls * 100, 1)

        return {
            "date": date_str,
            "user_id": str(target_user.id),
            "user_name": target_user.full_name,
            "user_role": target_user.role,
            "metrics": today_metrics,
            "yesterday_metrics": yesterday_metrics,
            "deltas": deltas,
            "target_call_count": target_calls,
            "percent_of_target": pct_of_target,
        }

    async def user_pipeline_stats(self) -> dict:
        """One row per user × pipeline stage for the User Performance report.

        Returns a list of rows shaped:
          {user_id, user_name, user_role, total_leads, by_stage{stage:count}}

        A lead counts toward a user if that user is EITHER the assigned
        Counsellor (assigned_agent_id) OR the Pre-Counsellor
        (pre_counsellor_id). A lead with the same user on both sides is
        counted once. A lead with two different users on each side
        contributes to BOTH users' rows — intentional, since both users
        have visibility / responsibility on that lead.

        Plus one virtual "AI Calls" row aggregating leads that were ever
        in an AI campaign, so admins can see how much pipeline the bot
        is generating relative to human counsellors.
        """
        from app.models.campaign_lead import CampaignLead
        from sqlalchemy import union_all

        # UNION of (assigned_agent_id, lead_id, stage) and
        # (pre_counsellor_id, lead_id, stage). count(DISTINCT lead_id)
        # in the outer aggregate dedups when the same user is on both
        # sides of the same lead.
        assigned_q = (
            select(
                Lead.assigned_agent_id.label("user_id"),
                Lead.id.label("lead_id"),
                Lead.current_stage.label("stage"),
            )
            .where(
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
                Lead.assigned_agent_id.isnot(None),
            )
        )
        pre_q = (
            select(
                Lead.pre_counsellor_id.label("user_id"),
                Lead.id.label("lead_id"),
                Lead.current_stage.label("stage"),
            )
            .where(
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
                Lead.pre_counsellor_id.isnot(None),
            )
        )
        owner_cte = union_all(assigned_q, pre_q).subquery("owners")
        agg = (
            select(
                owner_cte.c.user_id,
                owner_cte.c.stage,
                func.count(func.distinct(owner_cte.c.lead_id)).label("n"),
            )
            .group_by(owner_cte.c.user_id, owner_cte.c.stage)
        )
        rows = (await self.db.execute(agg)).all()

        per_user: dict[uuid.UUID, dict] = {}
        for user_id, stage, n in rows:
            entry = per_user.setdefault(user_id, {"total": 0, "by_stage": {}})
            entry["by_stage"][stage] = entry["by_stage"].get(stage, 0) + int(n)
            entry["total"] += int(n)

        # Resolve user names + roles in one batch query
        user_ids = list(per_user.keys())
        name_map: dict[uuid.UUID, tuple[str, str]] = {}
        if user_ids:
            profile_rows = (await self.db.execute(
                select(Profile.id, Profile.full_name, Profile.role)
                .where(Profile.id.in_(user_ids))
            )).all()
            name_map = {p.id: (p.full_name, p.role) for p in profile_rows}

        result: list[dict] = []
        for uid, entry in per_user.items():
            name, role = name_map.get(uid, ("(unknown)", "unknown"))
            result.append({
                "user_id": uid,
                "user_name": name,
                "user_role": role,
                "total_leads": entry["total"],
                "by_stage": entry["by_stage"],
            })
        # Sort: pre_counsellor → manager → admin → unknown.
        # Within each role, highest-total first.
        role_order = {"pre_counsellor": 0, "manager": 1, "admin": 2, "unknown": 9}
        result.sort(key=lambda r: (role_order.get(r["user_role"], 9), -r["total_leads"]))

        # Virtual "AI Calls" row: leads ever touched by a campaign,
        # grouped by current stage. Same shape so the FE renders it
        # alongside human rows.
        ai_rows = (await self.db.execute(
            select(Lead.current_stage, func.count(func.distinct(Lead.id)))
            .join(CampaignLead, CampaignLead.lead_id == Lead.id)
            .where(
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
                CampaignLead.company_id == self.company_id,
            )
            .group_by(Lead.current_stage)
        )).all()
        ai_by_stage: dict[str, int] = {s: int(n) for s, n in ai_rows}
        ai_total = sum(ai_by_stage.values())
        if ai_total:
            result.append({
                "user_id": None,
                "user_name": "AI Calls",
                "user_role": "ai",
                "total_leads": ai_total,
                "by_stage": ai_by_stage,
            })

        # Overdue task counts per user × lead-stage. Overdue means:
        # status is overdue, OR status is pending/in_progress AND
        # due_date has passed. The check_overdue_tasks() cron flips
        # pending→overdue but might lag, so we check both shapes here.
        now = now_utc()
        overdue_rows = (await self.db.execute(
            select(
                Task.assigned_to,
                Lead.current_stage,
                func.count().label("n"),
            )
            .join(Lead, Lead.id == Task.lead_id)
            .where(
                Task.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
                Task.assigned_to.isnot(None),
                Task.task_type == "call",
                # Open + past-due
                Task.status.in_([
                    TaskStatus.OVERDUE.value,
                    TaskStatus.PENDING.value,
                    TaskStatus.IN_PROGRESS.value,
                ]),
                Task.due_date < now,
            )
            .group_by(Task.assigned_to, Lead.current_stage)
        )).all()

        # Decorate the user rows with overdue totals + per-stage breakdown.
        overdue_per_user: dict[uuid.UUID, dict] = {}
        for uid, stage, n in overdue_rows:
            entry = overdue_per_user.setdefault(uid, {"total": 0, "by_stage": {}})
            entry["by_stage"][stage] = entry["by_stage"].get(stage, 0) + int(n)
            entry["total"] += int(n)
        for row in result:
            if row.get("user_id"):
                od = overdue_per_user.get(row["user_id"])
                if od:
                    row["overdue_task_count"] = od["total"]
                    row["overdue_by_stage"] = od["by_stage"]

        # Company-wide aggregate. Distinct count over the full leads
        # table so leads owned by both a Counsellor and Pre-Counsellor
        # don't double-count (which would happen if we just summed the
        # per-user rows above).
        company_rows = (await self.db.execute(
            select(Lead.current_stage, func.count())
            .where(
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
            )
            .group_by(Lead.current_stage)
        )).all()
        company_by_stage: dict[str, int] = {s: int(n) for s, n in company_rows}
        company_total_leads = sum(company_by_stage.values())

        return {
            "rows": result,
            "company_totals": {
                "total_leads": company_total_leads,
                "by_stage": company_by_stage,
            },
        }

    async def daily_activity_range(
        self, *, requesting_user: Profile, target_user_id: uuid.UUID | None = None,
        days: int = 30,
    ) -> list[dict]:
        """Last N days (default 30) of activity for a user. One row per
        day, oldest first. No deltas / targets — those only make sense
        for the focused single-day view. Capped at 90 days to keep the
        query count bounded (5 queries × N days per call).
        """
        target_user = await self._resolve_target_user(requesting_user, target_user_id)
        days = max(1, min(days, 90))

        today_ist = now_ist().date()
        results = []
        for i in range(days - 1, -1, -1):
            day = today_ist - timedelta(days=i)
            day_str = day.isoformat()
            start_utc, end_utc = self._ist_day_bounds(day_str)
            metrics = await self._compute_user_day_metrics(target_user.id, start_utc, end_utc)
            metrics["date"] = day_str
            results.append(metrics)
        return results
