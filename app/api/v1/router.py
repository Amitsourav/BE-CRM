from fastapi import APIRouter
from app.api.v1 import (
    auth, users, leads, lead_stages, call_attempts,
    tasks, notifications, csv_import, webhooks, reports,
    companies, agents, voice, activity_logs, campaigns,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(companies.router)
api_router.include_router(users.router)
api_router.include_router(leads.router)
api_router.include_router(lead_stages.router)
api_router.include_router(call_attempts.router)
api_router.include_router(tasks.router)
api_router.include_router(notifications.router)
api_router.include_router(csv_import.router)
api_router.include_router(webhooks.router)
api_router.include_router(webhooks.internal_router)
api_router.include_router(reports.router)
api_router.include_router(agents.router, prefix="/agents", tags=["AI Agents"])
api_router.include_router(voice.router, prefix="/voice", tags=["Voice"])
api_router.include_router(activity_logs.router)
api_router.include_router(campaigns.router)
