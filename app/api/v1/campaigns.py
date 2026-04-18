from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user, get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.campaign_service import CampaignService
from app.schemas.campaign import CampaignCreate, CampaignUpdate, AssignLeadsRequest

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


@router.post("")
async def create_campaign(
    data: CampaignCreate,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaign = await service.create(user_id=current_user.id, data=data)
    return {"success": True, "campaign_id": str(campaign.id), "message": "Campaign created"}


@router.get("")
async def list_campaigns(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaigns, total = await service.list(status=status, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": str(c.id),
                "name": c.name,
                "description": c.description,
                "status": c.status,
                "ai_agent_id": str(c.ai_agent_id),
                "agent_name": c.agent.name if c.agent else None,
                "total_leads": c.total_leads,
                "calls_made": c.calls_made,
                "calls_connected": c.calls_connected,
                "calls_failed": c.calls_failed,
                "created_at": c.created_at,
                "started_at": c.started_at,
                "progress_pct": round(c.calls_made / c.total_leads * 100, 1) if c.total_leads > 0 else 0,
            }
            for c in campaigns
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaign = await service.get(campaign_id)
    stats = await service.get_stats(campaign_id)
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "description": campaign.description,
        "status": campaign.status,
        "ai_agent_id": str(campaign.ai_agent_id),
        "agent_name": campaign.agent.name if campaign.agent else None,
        "daily_start_time": str(campaign.daily_start_time),
        "daily_end_time": str(campaign.daily_end_time),
        "skip_weekends": campaign.skip_weekends,
        "timezone": campaign.timezone,
        "max_retries": campaign.max_retries,
        "retry_gap_hours": campaign.retry_gap_hours,
        "max_concurrent_calls": campaign.max_concurrent_calls,
        "stats": stats,
        "created_at": campaign.created_at,
        "started_at": campaign.started_at,
        "completed_at": campaign.completed_at,
    }


@router.put("/{campaign_id}")
async def update_campaign(
    campaign_id: uuid.UUID,
    data: CampaignUpdate,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    await service.update(campaign_id, data)
    return {"success": True}


@router.delete("/{campaign_id}")
async def delete_campaign(
    campaign_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    await service.delete(campaign_id)
    return {"success": True}


@router.post("/{campaign_id}/assign-leads")
async def assign_leads(
    campaign_id: uuid.UUID,
    data: AssignLeadsRequest,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    added = await service.assign_leads(campaign_id, data.lead_ids)
    return {"success": True, "leads_added": added}


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaign = await service.start(campaign_id)
    return {"success": True, "status": campaign.status}


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaign = await service.pause(campaign_id)
    return {"success": True, "status": campaign.status}


@router.post("/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    campaign = await service.stop(campaign_id)
    return {"success": True, "status": campaign.status}


@router.get("/{campaign_id}/leads")
async def get_campaign_leads(
    campaign_id: uuid.UUID,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CampaignService(db, company_id)
    items, total = await service.get_leads(campaign_id, status=status, page=page, page_size=page_size)
    return {
        "items": [
            {
                "id": str(cl.id),
                "lead_id": str(cl.lead_id),
                "lead_name": cl.lead.full_name if cl.lead else None,
                "lead_phone": cl.lead.phone if cl.lead else None,
                "status": cl.status,
                "attempt_count": cl.attempt_count,
                "last_attempt_at": cl.last_attempt_at,
                "next_retry_at": cl.next_retry_at,
                "last_call_status": cl.last_call_status,
            }
            for cl in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
