from __future__ import annotations

import csv
import io
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, UploadFile, File
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user, get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.models.lead import Lead
from app.models.campaign_lead import CampaignLead
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


@router.get("/csv-template")
async def download_csv_template():
    """Download CSV template for campaign lead upload."""
    content = (
        "name,phone,email,city,notes\n"
        "Rahul Sharma,+919876543210,rahul@example.com,Mumbai,Interested in MBA\n"
        "Priya Gupta,9876543211,priya@example.com,Delhi,MBBS inquiry\n"
    )
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=campaign_leads_template.csv"},
    )


@router.post("/{campaign_id}/upload-csv")
async def upload_leads_csv(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload CSV to create leads and assign them to the campaign.

    Required columns: name, phone
    Optional columns: email, city, state, notes
    Skips duplicates by phone number (within company).
    """
    from sqlalchemy import func

    if not file.filename or not file.filename.endswith(".csv"):
        return {"success": False, "error": "Only .csv files accepted"}

    service = CampaignService(db, company_id)
    campaign = await service.get(campaign_id)
    if campaign.status in ("active", "completed", "stopped"):
        return {"success": False, "error": f"Cannot add leads to {campaign.status} campaign"}

    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    except Exception as e:
        return {"success": False, "error": f"Invalid CSV: {e}"}

    if "name" not in headers or "phone" not in headers:
        return {"success": False, "error": "CSV must have 'name' and 'phone' columns"}

    stats = {
        "total_rows": 0, "new_leads_created": 0, "existing_leads_added": 0,
        "duplicates_skipped": 0, "invalid_rows": 0, "errors": [],
    }

    for row_num, raw_row in enumerate(reader, start=2):
        stats["total_rows"] += 1
        row = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items()}

        name = row.get("name", "")
        phone_raw = row.get("phone", "")
        if not name or not phone_raw:
            stats["invalid_rows"] += 1
            stats["errors"].append({"row": row_num, "error": "Missing name or phone"})
            continue

        phone = re.sub(r"[^\d+]", "", phone_raw)
        if len(phone) < 10:
            stats["invalid_rows"] += 1
            stats["errors"].append({"row": row_num, "error": f"Invalid phone: {phone_raw}"})
            continue
        if not phone.startswith("+"):
            phone = "+91" + phone if len(phone) == 10 else "+" + phone

        result = await db.execute(
            select(Lead).where(Lead.company_id == company_id, Lead.phone == phone, Lead.is_deleted == False)
        )
        lead = result.scalar_one_or_none()

        if lead:
            stats["existing_leads_added"] += 1
        else:
            email = row.get("email") or None
            if email and email.lower() == "nan":
                email = None
            lead = Lead(
                company_id=company_id, full_name=name, phone=phone, email=email,
                city=row.get("city") or None, state=row.get("state") or None,
                notes=row.get("notes") or None, current_stage="lead",
            )
            db.add(lead)
            await db.flush()
            stats["new_leads_created"] += 1

        existing_cl = await db.execute(
            select(CampaignLead.id).where(
                CampaignLead.campaign_id == campaign_id, CampaignLead.lead_id == lead.id,
            )
        )
        if existing_cl.scalar_one_or_none():
            stats["duplicates_skipped"] += 1
            continue

        db.add(CampaignLead(
            campaign_id=campaign_id, lead_id=lead.id, company_id=company_id, status="pending",
        ))

    total_added = stats["new_leads_created"] + stats["existing_leads_added"] - stats["duplicates_skipped"]
    campaign.total_leads = (campaign.total_leads or 0) + total_added
    await db.commit()

    return {"success": True, "message": f"Processed {stats['total_rows']} rows", **stats}
