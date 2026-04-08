from __future__ import annotations

import uuid
import logging
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user, get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.call_service import CallService
from app.services.ai_agent_service import AIAgentService
from app.services.bolna_service import bolna_service
from app.schemas.call import (
    CallAttemptCreate, CallAttemptOut,
    CallInitiate, CallStatusUpdate, CallPostData,
    CallAttemptWithLead,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Calls"])


# ── Existing endpoint (manual call logging — keep working) ─────────

@router.post("/leads/{lead_id}/calls", response_model=CallAttemptOut, status_code=201)
async def log_call(
    lead_id: uuid.UUID,
    body: CallAttemptCreate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CallService(db, company_id)
    extra = {}
    if body.call_provider:
        extra["call_provider"] = body.call_provider
    if body.call_recording_url:
        extra["call_recording_url"] = body.call_recording_url
    if body.external_call_id:
        extra["external_call_id"] = body.external_call_id
    if body.call_duration_seconds is not None:
        extra["call_duration_seconds"] = body.call_duration_seconds

    return await service.log_call(
        lead_id=lead_id,
        user=current_user,
        disposition=body.disposition,
        conversation_notes=body.conversation_notes,
        agent_agenda=body.agent_agenda,
        due_date_for_next=body.due_date_for_next,
        **extra,
    )


# ── AI Call Initiation ─────────────────────────────────────────────

@router.post("/calls/initiate", response_model=CallAttemptOut, status_code=201)
async def initiate_call(
    body: CallInitiate,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Initiate an outbound AI call via Bolna."""
    call_service = CallService(db, company_id)
    agent_service = AIAgentService(db, company_id)

    # 1. Get AI agent (provided or company default)
    if body.ai_agent_id:
        agent = await agent_service.get_agent(body.ai_agent_id)
    else:
        agent = await agent_service.get_default_agent()
    if not agent:
        raise HTTPException(status_code=400, detail="No AI agent configured. Create one first.")

    # 2. Create call record in DB (status=initiated)
    call = await call_service.create_call_record(
        telecaller_id=current_user.id,
        data={
            "lead_id": body.lead_id,
            "ai_agent_id": agent.id,
            "call_type": body.call_type or "ai",
        },
    )
    logger.info("[CALL] Created call record %s for lead %s", call.id, body.lead_id)

    # 3. Get lead for phone number
    lead = await call_service._get_lead(body.lead_id)
    phone = body.phone_number or lead.phone
    if not phone:
        await call_service.update_call_status(call.id, {"call_status": "failed"})
        raise HTTPException(status_code=400, detail="Lead has no phone number")

    # 4. Call Bolna API
    try:
        bolna_response = await bolna_service.initiate_call(agent, lead, call.id)
        provider_call_id = bolna_response.get("call_id") or bolna_response.get("id")

        # 5. Update call record with provider ID + ringing status
        call = await call_service.update_call_status(call.id, {
            "call_status": "ringing",
            "bolna_call_id": provider_call_id,
        })
        logger.info("[CALL] Call %s now ringing, provider_id=%s", call.id, provider_call_id)

    except Exception as e:
        # Bolna failed — mark call as failed
        await call_service.update_call_status(call.id, {"call_status": "failed"})
        logger.error("[CALL] Bolna initiation failed for call %s: %s", call.id, e)
        raise HTTPException(status_code=500, detail=f"Failed to initiate call: {e}")

    return call


# ── List / Get / Update calls ─────────────────────────────────────

@router.get("/calls", response_model=list[CallAttemptOut])
async def list_calls(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    telecaller_id: uuid.UUID | None = Query(None),
    call_status: str | None = Query(None),
    call_type: str | None = Query(None),
    sentiment: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    service = CallService(db, company_id)
    return await service.get_all_calls(
        user=current_user,
        skip=skip, limit=limit,
        telecaller_id=telecaller_id,
        call_status=call_status,
        call_type=call_type,
        sentiment=sentiment,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/calls/{call_id}", response_model=CallAttemptWithLead)
async def get_call(
    call_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CallService(db, company_id)
    return await service.get_call(call_id, current_user)


@router.get("/calls/{call_id}/status")
async def get_call_status(
    call_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Quick status poll endpoint for frontend."""
    service = CallService(db, company_id)
    call = await service._get_call(call_id)
    return {
        "call_id": call.id,
        "status": call.call_status,
        "duration": call.call_duration_seconds,
        "started_at": call.started_at,
    }


@router.patch("/calls/{call_id}/status", response_model=CallAttemptOut)
async def update_call_status(
    call_id: uuid.UUID,
    body: CallStatusUpdate,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CallService(db, company_id)
    return await service.update_call_status(call_id, body.model_dump(exclude_unset=True))


@router.patch("/calls/{call_id}/post-data", response_model=CallAttemptOut)
async def save_call_post_data(
    call_id: uuid.UUID,
    body: CallPostData,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CallService(db, company_id)
    return await service.save_call_post_data(call_id, body.model_dump(exclude_unset=True))
