from __future__ import annotations

import uuid
from pydantic import BaseModel


class DashboardReport(BaseModel):
    total_leads: int = 0
    new_leads_today: int = 0
    leads_by_stage: dict[str, int] = {}
    total_agents: int = 0
    active_agents: int = 0
    tasks_pending: int = 0
    tasks_overdue: int = 0
    tasks_completed_today: int = 0
    conversion_rate: float = 0.0


class PipelineReport(BaseModel):
    stages: list[dict] = []
    total: int = 0


class AgentPerformance(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    total_leads: int = 0
    leads_by_stage: dict[str, int] = {}
    total_calls: int = 0
    dnp_calls: int = 0
    connected_calls: int = 0
    total_tasks: int = 0
    completed_tasks: int = 0
    overdue_tasks: int = 0
    conversion_rate: float = 0.0


class SourcePerformance(BaseModel):
    source_id: uuid.UUID | None = None
    source_name: str
    total_leads: int = 0
    won: int = 0
    lost: int = 0
    conversion_rate: float = 0.0


class TrendData(BaseModel):
    date: str
    new_leads: int = 0
    won: int = 0
    lost: int = 0
    calls_made: int = 0


class UserPipelineStats(BaseModel):
    """Per-user pipeline breakdown for the User Performance report.

    `user_id` is None on the virtual "AI" row (leads touched by an AI
    campaign / AI call), so the FE can render it as a special tile.
    `by_stage` keys are LeadStage values present for this owner; missing
    stages mean the user has zero leads in that stage.
    """
    user_id: uuid.UUID | None = None
    user_name: str
    user_role: str  # 'admin' | 'manager' | 'pre_counsellor' | 'ai'
    total_leads: int = 0
    by_stage: dict[str, int] = {}


class CompanyPipelineTotals(BaseModel):
    """Company-wide aggregate, included alongside the per-user rows so
    the FE can render an accurate "Total in CRM" stat without summing
    rows (which double-counts leads owned by both a Counsellor and a
    Pre-Counsellor).
    """
    total_leads: int = 0
    by_stage: dict[str, int] = {}


class UserPipelineStatsReport(BaseModel):
    rows: list[UserPipelineStats]
    company_totals: CompanyPipelineTotals
