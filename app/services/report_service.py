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
from app.core.constants import LeadStage, TaskStatus, CallDisposition, UserRole
from app.utils.date_helpers import now_utc, start_of_today, end_of_today


class ReportService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    # ── Dashboard: 9 queries → 3 ──────────────────────────────────────

    async def dashboard(self) -> dict:
        today_start = start_of_today()

        # Query 1: All lead stats in one query (total, new today, by stage)
        lead_rows = (await self.db.execute(
            select(
                Lead.current_stage,
                func.count().label("cnt"),
                func.count(case((Lead.created_at >= today_start, 1))).label("new_today"),
            ).where(Lead.company_id == self.company_id).group_by(Lead.current_stage)
        )).all()

        leads_by_stage = {}
        total = 0
        new_today = 0
        for row in lead_rows:
            leads_by_stage[row.current_stage] = row.cnt
            total += row.cnt
            new_today += row.new_today

        # Query 2: All task stats in one query
        task_row = (await self.db.execute(
            select(
                func.count(case((Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]), 1))).label("pending"),
                func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
                func.count(case((
                    (Task.status == TaskStatus.COMPLETED) & (Task.completed_at >= today_start), 1
                ))).label("completed_today"),
            ).where(Task.company_id == self.company_id)
        )).one()

        # Query 3: Agent counts in one query
        agent_row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((Profile.is_active == True, 1))).label("active"),
            ).where(Profile.role == UserRole.TELECALLER, Profile.company_id == self.company_id)
        )).one()

        won = leads_by_stage.get(LeadStage.WON, 0)
        conversion_rate = (won / total * 100) if total > 0 else 0.0

        return {
            "total_leads": total,
            "new_leads_today": new_today,
            "leads_by_stage": leads_by_stage,
            "total_agents": agent_row.total,
            "active_agents": agent_row.active,
            "tasks_pending": task_row.pending,
            "tasks_overdue": task_row.overdue,
            "tasks_completed_today": task_row.completed_today,
            "conversion_rate": round(conversion_rate, 2),
        }

    # ── Pipeline (already efficient) ───────────────────────────────────

    async def pipeline(self) -> dict:
        stage_counts = (await self.db.execute(
            select(Lead.current_stage, func.count())
            .where(Lead.company_id == self.company_id)
            .group_by(Lead.current_stage)
        )).all()

        total = sum(count for _, count in stage_counts)
        stages = []
        for stage in LeadStage:
            count = next((c for s, c in stage_counts if s == stage.value), 0)
            stages.append({
                "stage": stage.value,
                "count": count,
                "percentage": round((count / total * 100) if total > 0 else 0, 2),
            })

        return {"stages": stages, "total": total}

    # ── Agents Summary: 81 queries → 4 ────────────────────────────────

    async def agents_summary(self) -> list[dict]:
        result = await self.db.execute(
            select(Profile).where(Profile.role == UserRole.TELECALLER, Profile.company_id == self.company_id).order_by(Profile.full_name)
        )
        agents = result.scalars().all()
        if not agents:
            return []

        agent_ids = [a.id for a in agents]

        # Batch query 1: Leads by stage per agent
        lead_rows = (await self.db.execute(
            select(
                Lead.assigned_agent_id,
                Lead.current_stage,
                func.count().label("cnt"),
            )
            .where(Lead.assigned_agent_id.in_(agent_ids))
            .group_by(Lead.assigned_agent_id, Lead.current_stage)
        )).all()

        # Build lookup: {agent_id: {stage: count}}
        lead_map: dict[uuid.UUID, dict] = defaultdict(dict)
        for row in lead_rows:
            lead_map[row.assigned_agent_id][row.current_stage] = row.cnt

        # Batch query 2: Call stats per agent
        call_rows = (await self.db.execute(
            select(
                CallAttempt.agent_id,
                func.count().label("total"),
                func.count(case((CallAttempt.disposition == CallDisposition.DNP, 1))).label("dnp"),
                func.count(case((CallAttempt.disposition == CallDisposition.CONNECTED, 1))).label("connected"),
            )
            .where(CallAttempt.agent_id.in_(agent_ids))
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
            .where(Task.assigned_to.in_(agent_ids))
            .group_by(Task.assigned_to)
        )).all()

        task_map = {}
        for row in task_rows:
            task_map[row.assigned_to] = {
                "total": row.total, "completed": row.completed, "overdue": row.overdue,
            }

        # Assemble results
        summaries = []
        for agent in agents:
            leads_by_stage = lead_map.get(agent.id, {})
            total_leads = sum(leads_by_stage.values())
            calls = call_map.get(agent.id, {"total": 0, "dnp": 0, "connected": 0})
            tasks = task_map.get(agent.id, {"total": 0, "completed": 0, "overdue": 0})

            won = leads_by_stage.get(LeadStage.WON, 0)
            conversion_rate = (won / total_leads * 100) if total_leads > 0 else 0.0

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

    async def agent_detail(self, agent_id: uuid.UUID) -> dict:
        result = await self.db.execute(
            select(Profile).where(Profile.id == agent_id, Profile.company_id == self.company_id)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Agent not found")

        # For a single agent, use the same batch approach with 1 agent
        stage_counts = (await self.db.execute(
            select(Lead.current_stage, func.count())
            .where(Lead.assigned_agent_id == agent.id)
            .group_by(Lead.current_stage)
        )).all()
        leads_by_stage = {stage: count for stage, count in stage_counts}
        total_leads = sum(leads_by_stage.values())

        call_row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((CallAttempt.disposition == CallDisposition.DNP, 1))).label("dnp"),
                func.count(case((CallAttempt.disposition == CallDisposition.CONNECTED, 1))).label("connected"),
            ).where(CallAttempt.agent_id == agent.id)
        )).one()

        task_row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((Task.status == TaskStatus.COMPLETED, 1))).label("completed"),
                func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
            ).where(Task.assigned_to == agent.id)
        )).one()

        won = leads_by_stage.get(LeadStage.WON, 0)
        conversion_rate = (won / total_leads * 100) if total_leads > 0 else 0.0

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

    async def sources(self) -> list[dict]:
        rows = (await self.db.execute(
            select(
                LeadSource.id,
                LeadSource.name,
                func.count(Lead.id).label("total"),
                func.count(case((Lead.current_stage == LeadStage.WON, Lead.id))).label("won"),
                func.count(case((Lead.current_stage == LeadStage.LOST, Lead.id))).label("lost"),
            )
            .outerjoin(Lead, Lead.lead_source_id == LeadSource.id)
            .where(LeadSource.company_id == self.company_id)
            .group_by(LeadSource.id, LeadSource.name)
            .order_by(LeadSource.name)
        )).all()

        stats = []
        for row in rows:
            stats.append({
                "source_id": row.id,
                "source_name": row.name,
                "total_leads": row.total,
                "won": row.won,
                "lost": row.lost,
                "conversion_rate": round((row.won / row.total * 100) if row.total > 0 else 0, 2),
            })
        return stats

    # ── Task Compliance: 4 queries → 1 ────────────────────────────────

    async def task_compliance(self) -> dict:
        row = (await self.db.execute(
            select(
                func.count().label("total"),
                func.count(case((Task.status == TaskStatus.COMPLETED, 1))).label("completed"),
                func.count(case((Task.status == TaskStatus.OVERDUE, 1))).label("overdue"),
                func.count(case((Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]), 1))).label("pending"),
            ).where(Task.company_id == self.company_id)
        )).one()

        return {
            "total_tasks": row.total,
            "completed": row.completed,
            "overdue": row.overdue,
            "pending": row.pending,
            "compliance_rate": round((row.completed / row.total * 100) if row.total > 0 else 0, 2),
        }

    # ── Trends: 120 queries → 4 ───────────────────────────────────────

    async def trends(self, days: int = 30) -> list[dict]:
        now = now_utc()
        start_date = (now - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Query 1: New leads by date
        new_rows = (await self.db.execute(
            select(
                cast(Lead.created_at, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(Lead.company_id == self.company_id, Lead.created_at >= start_date)
            .group_by(cast(Lead.created_at, Date))
        )).all()

        # Query 2: Won leads by date
        won_rows = (await self.db.execute(
            select(
                cast(Lead.won_time, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(Lead.company_id == self.company_id, Lead.won_time >= start_date)
            .group_by(cast(Lead.won_time, Date))
        )).all()

        # Query 3: Lost leads by date
        lost_rows = (await self.db.execute(
            select(
                cast(Lead.lost_time, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(Lead.company_id == self.company_id, Lead.lost_time >= start_date)
            .group_by(cast(Lead.lost_time, Date))
        )).all()

        # Query 4: Calls by date
        call_rows = (await self.db.execute(
            select(
                cast(CallAttempt.created_at, Date).label("day"),
                func.count().label("cnt"),
            )
            .where(CallAttempt.company_id == self.company_id, CallAttempt.created_at >= start_date)
            .group_by(cast(CallAttempt.created_at, Date))
        )).all()

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
