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
