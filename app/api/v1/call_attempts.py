import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.profile import Profile
from app.services.call_service import CallService
from app.schemas.call import CallAttemptCreate, CallAttemptOut

router = APIRouter(tags=["Call Attempts"])


@router.post("/leads/{lead_id}/calls", response_model=CallAttemptOut, status_code=201)
async def log_call(
    lead_id: uuid.UUID,
    body: CallAttemptCreate,
    current_user: Profile = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = CallService(db)
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
