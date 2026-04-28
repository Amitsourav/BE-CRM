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
    return {"success": True, "id": str(campaign.id), "campaign_id": str(campaign.id), "message": "Campaign created"}


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
    # Progress is "fraction of unique leads we got in front of", not "attempts /
    # leads". calls_made counts every dial including retries, so a campaign
    # with max_retries=3 can have calls_made = 3 * total_leads and the old
    # ratio went past 100% (the 115% screenshot). Cap at 100% and approximate
    # progress as connected / total — connected is monotonic and unique-lead.
    items = []
    for c in campaigns:
        attempts = c.calls_made or 0
        connected = c.calls_connected or 0
        total_leads_c = c.total_leads or 0
        progress_pct = round(min(connected / total_leads_c * 100, 100.0), 1) if total_leads_c > 0 else 0
        items.append({
            "id": str(c.id),
            "name": c.name,
            "description": c.description,
            "status": c.status,
            "ai_agent_id": str(c.ai_agent_id),
            "agent_name": c.agent.name if c.agent else None,
            "total_leads": total_leads_c,
            "calls_made": attempts,
            "attempts": attempts,
            "calls_connected": connected,
            "calls_failed": c.calls_failed or 0,
            "created_at": c.created_at,
            "started_at": c.started_at,
            "progress_pct": progress_pct,
        })
    return {
        "items": items,
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
    total = stats.get("total_leads", 0)

    # Estimated cost: agent per-min rate × avg 3 min/call × total leads
    estimated_cost = 0.0
    try:
        from app.services.pricing_service import calculate_agent_pricing, INR_RATE
        if campaign.agent:
            pricing = calculate_agent_pricing(campaign.agent)
            avg_call_minutes = 3.0
            estimated_cost = round(pricing["total_usd"] * avg_call_minutes * total * INR_RATE, 2)
    except Exception:
        pass

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
        # Top-level stats (frontend reads these for the overview cards).
        # See list_campaigns above for why progress is connected/total, capped.
        "total_leads": total,
        "calls_made": campaign.calls_made or 0,
        "attempts": campaign.calls_made or 0,
        "calls_connected": campaign.calls_connected or 0,
        "calls_failed": campaign.calls_failed or 0,
        "total_cost": campaign.total_cost_usd or 0,
        "estimated_cost_inr": estimated_cost,
        "progress_pct": (
            round(min((campaign.calls_connected or 0) / total * 100, 100.0), 1)
            if total > 0 else 0
        ),
        # Detailed breakdown
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
                "lead_stage": cl.lead.current_stage if cl.lead else None,
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


# Column name aliases — maps common CSV header variations to our field names
_COL_ALIASES = {
    "name": ["name", "full_name", "full name", "lead name", "lead_name", "student name", "student_name", "contact name"],
    "phone": ["phone", "phone_number", "phone number", "mobile", "mobile_number", "mobile number", "contact", "number", "cell", "telephone"],
    "email": ["email", "email_address", "email address", "e-mail", "mail"],
    "city": ["city", "location", "place"],
    "state": ["state", "province", "region"],
    "notes": ["notes", "note", "comments", "comment", "remarks", "description"],
}


def _map_columns(raw_headers: list[str]) -> dict[str, str]:
    """Map CSV column headers to our field names using aliases.

    Returns {our_field: csv_header} for matched columns.
    """
    mapping = {}
    normalized = {h.strip().lower(): h for h in raw_headers}
    for field, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


@router.post("/{campaign_id}/upload-csv")
async def upload_leads_csv(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload CSV to create leads and assign them to the campaign.

    Required columns: name + phone (accepts common variations like
    'Full Name', 'Phone Number', 'Mobile', etc.)
    Optional: email, city, state, notes
    Skips duplicates by phone number (within company).
    """
    import logging
    from sqlalchemy import func
    log = logging.getLogger(__name__)

    if not file.filename or not file.filename.endswith(".csv"):
        return {"success": False, "error": "Only .csv files accepted"}

    service = CampaignService(db, company_id)
    campaign = await service.get(campaign_id)
    if campaign.status in ("active", "completed", "stopped"):
        return {"success": False, "error": f"Cannot add leads to {campaign.status} campaign"}

    # Parse CSV
    try:
        raw = await file.read()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        raw_headers = list(reader.fieldnames or [])
    except Exception as e:
        return {"success": False, "error": f"Invalid CSV: {e}"}

    if not raw_headers:
        return {"success": False, "error": "CSV file is empty or has no headers"}

    # Map columns flexibly
    col_map = _map_columns(raw_headers)
    log.info("CSV_UPLOAD campaign=%s file=%s headers=%s mapped=%s", campaign_id, file.filename, raw_headers, col_map)

    # Phone is required; name is optional. WhatsApp / Facebook ad exports
    # often give us only phone numbers. The AI agent now asks for the name
    # during the call and writes it back to the lead, so empty-name rows
    # are perfectly acceptable as long as we have a phone.
    if "phone" not in col_map:
        return {
            "success": False,
            "error": f"CSV must have a phone column. Found headers: {raw_headers}. "
                     f"Accepted phone columns: {_COL_ALIASES['phone']}.",
        }

    name_col = col_map.get("name")  # may be missing
    phone_col = col_map["phone"]
    email_col = col_map.get("email")
    city_col = col_map.get("city")
    state_col = col_map.get("state")
    notes_col = col_map.get("notes")

    stats = {
        "total_rows": 0, "new_leads_created": 0, "existing_leads_added": 0,
        "duplicates_skipped": 0, "invalid_rows": 0, "errors": [],
    }

    # ── STEP 1: Parse all rows into clean records ──
    parsed = []  # list of {name, phone, email, city, state, notes}
    for row_num, row in enumerate(reader, start=2):
        stats["total_rows"] += 1
        # Name is optional — we use a placeholder if missing so the agent
        # asks for the name during the call and saves it back via the
        # post-call pipeline.
        name = (row.get(name_col) or "").strip() if name_col else ""
        phone_raw = (row.get(phone_col) or "").strip()
        if not phone_raw:
            stats["invalid_rows"] += 1
            stats["errors"].append({"row": row_num, "error": "Missing phone"})
            continue

        phone = re.sub(r"[^\d+]", "", phone_raw)
        if len(phone) < 10:
            stats["invalid_rows"] += 1
            stats["errors"].append({"row": row_num, "error": f"Invalid phone: {phone_raw}"})
            continue
        if not phone.startswith("+"):
            phone = "+91" + phone if len(phone) == 10 else "+" + phone

        # leads.full_name is NOT NULL — fall back to "Lead" placeholder.
        # The voice pipeline's _is_real_name() treats this as 'no name'
        # and the agent's no-name welcome triggers ("May I know your name?").
        if not name:
            name = "Lead"

        email = (row.get(email_col) or "").strip() if email_col else None
        if email and email.lower() in ("nan", "none", ""):
            email = None

        parsed.append({
            "name": name, "phone": phone, "email": email or None,
            "city": ((row.get(city_col) or "").strip() or None) if city_col else None,
            "state": ((row.get(state_col) or "").strip() or None) if state_col else None,
            "notes": ((row.get(notes_col) or "").strip() or None) if notes_col else None,
        })

    if not parsed:
        return {"success": True, "message": "No valid rows", **stats}

    # ── STEP 2: Batch lookup existing leads by phone (1 query) ──
    all_phones = list({r["phone"] for r in parsed})
    result = await db.execute(
        select(Lead).where(Lead.company_id == company_id, Lead.phone.in_(all_phones), Lead.is_deleted == False)
    )
    existing_leads = {lead.phone: lead for lead in result.scalars().all()}

    # ── STEP 3: Create missing leads in batch ──
    new_leads = []
    for r in parsed:
        if r["phone"] not in existing_leads:
            lead = Lead(
                company_id=company_id, full_name=r["name"], phone=r["phone"],
                email=r["email"], city=r["city"], state=r["state"],
                notes=r["notes"], current_stage="lead",
            )
            db.add(lead)
            new_leads.append(lead)

    if new_leads:
        await db.flush()  # single flush for all new leads
        for lead in new_leads:
            existing_leads[lead.phone] = lead
    stats["new_leads_created"] = len(new_leads)

    # ── STEP 4: Batch lookup existing campaign_leads (1 query) ──
    all_lead_ids = [existing_leads[r["phone"]].id for r in parsed if r["phone"] in existing_leads]
    result = await db.execute(
        select(CampaignLead.lead_id).where(
            CampaignLead.campaign_id == campaign_id, CampaignLead.lead_id.in_(all_lead_ids),
        )
    )
    already_in_campaign = {row[0] for row in result.all()}

    # ── STEP 5: Create campaign_leads in batch ──
    for r in parsed:
        lead = existing_leads.get(r["phone"])
        if not lead:
            continue
        if lead.id in already_in_campaign:
            stats["duplicates_skipped"] += 1
            continue
        db.add(CampaignLead(
            campaign_id=campaign_id, lead_id=lead.id, company_id=company_id, status="pending",
        ))
        already_in_campaign.add(lead.id)  # prevent intra-CSV dupes
        if lead.phone in {nl.phone for nl in new_leads}:
            pass  # already counted
        else:
            stats["existing_leads_added"] += 1

    total_added = stats["new_leads_created"] + stats["existing_leads_added"]
    campaign.total_leads = (campaign.total_leads or 0) + total_added
    await db.commit()

    log.info(
        "CSV_UPLOAD_DONE campaign=%s rows=%d new=%d existing=%d dupes=%d invalid=%d",
        campaign_id, stats["total_rows"], stats["new_leads_created"],
        stats["existing_leads_added"], stats["duplicates_skipped"], stats["invalid_rows"],
    )

    return {"success": True, "message": f"Processed {stats['total_rows']} rows", **stats}
