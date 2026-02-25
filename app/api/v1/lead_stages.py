from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.profile import Profile
from app.services.stage_machine import StageMachine
from app.schemas.stage import StageTransitionRequest, StageLogOut
from app.schemas.lead import LeadOut

router = APIRouter(tags=["Lead Stages"])


@router.post("/leads/{lead_id}/stage", response_model=LeadOut)
async def transition_stage(
    lead_id: uuid.UUID,
    body: StageTransitionRequest,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    machine = StageMachine(db)
    return await machine.transition(
        lead_id=lead_id,
        to_stage=body.to_stage,
        user=current_user,
        conversation_notes=body.conversation_notes,
        agent_agenda=body.agent_agenda,
        due_date=body.due_date,
        lost_reason=body.lost_reason,
    )


@router.get("/leads/{lead_id}/stage-history", response_model=list[StageLogOut])
async def get_stage_history(
    lead_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    machine = StageMachine(db)
    return await machine.get_stage_history(lead_id, current_user)
