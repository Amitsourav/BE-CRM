from __future__ import annotations

import uuid
import httpx
import logging
from fastapi import APIRouter, Depends, Query, HTTPException, Body
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user, get_current_admin, get_current_manager
from app.core.tenant import get_current_company_id
from app.core.exceptions import NotFoundError, BadRequestError
from app.models.profile import Profile
from app.services.ai_agent_service import AIAgentService
from app.services.language_detector import detect_language, get_language_instruction
from app.services.pricing_service import calculate_agent_pricing
from app.schemas.ai_agent import AIAgentCreate, AIAgentUpdate, AIAgentOut, PROVIDER_OPTIONS
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["AI Agents"])


FIELD_TO_TAB = {
    # identity
    "name": "identity", "role": "identity", "tone": "identity",
    "is_active": "identity", "is_default": "identity",
    # prompt
    "system_prompt": "prompt", "welcome_message": "prompt",
    "final_message_en": "prompt", "final_message_hi": "prompt",
    "silence_message_en": "prompt", "silence_message_hi": "prompt",
    # voice
    "llm_provider": "voice", "llm_model": "voice",
    "llm_temperature": "voice", "llm_max_tokens": "voice",
    "stt_provider": "voice", "stt_model": "voice", "stt_keywords": "voice",
    "tts_provider": "voice", "tts_model": "voice", "tts_voice": "voice",
    "tts_gender": "voice", "tts_speed": "voice",
    "tts_buffer_size": "voice", "tts_stability": "voice",
    "tts_similarity_boost": "voice",
    "primary_language": "voice", "secondary_language": "voice",
    "auto_language_switch": "voice", "language_style": "voice",
    # behavior
    "endpointing_ms": "behavior", "linear_delay_ms": "behavior",
    "words_before_interrupt": "behavior", "max_response_words": "behavior",
    "precise_transcript": "behavior",
    "noise_cancellation": "behavior", "noise_cancellation_level": "behavior",
    "ambient_noise": "behavior", "silence_detection_seconds": "behavior",
    # telephony
    "telephony_provider": "telephony", "phone_number": "telephony",
    "call_timeout_seconds": "telephony",
    "hangup_on_silence_seconds": "telephony",
    "call_start_time": "telephony", "call_end_time": "telephony",
    "restrict_call_hours": "telephony", "voicemail_detection": "telephony",
    # webhook
    "webhook_url": "webhook",
}


def format_validation_error(exc: ValidationError) -> HTTPException:
    errors = []
    for err in exc.errors():
        loc = err.get("loc", ())
        field = loc[-1] if loc else "unknown"
        errors.append({
            "field": str(field),
            "tab": FIELD_TO_TAB.get(str(field), "identity"),
            "message": err.get("msg", "Invalid value"),
        })
    return HTTPException(
        status_code=422,
        detail={"detail": "Validation error", "errors": errors},
    )


def agent_to_response(agent) -> dict:
    """Convert agent model to response dict with pricing included."""
    data = AIAgentOut.model_validate(agent).model_dump()
    data["pricing"] = calculate_agent_pricing(agent)
    return data


@router.get("/options")
async def get_options():
    """Returns all provider/voice/language options for frontend dropdowns."""
    return PROVIDER_OPTIONS


@router.post("", response_model=AIAgentOut, status_code=201)
async def create_agent(
    body: dict = Body(...),
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = AIAgentCreate.model_validate(body)
    except ValidationError as exc:
        raise format_validation_error(exc)
    service = AIAgentService(db, company_id)
    payload = data.model_dump()
    payload["created_by"] = admin.id
    agent = await service.create_agent(payload)
    return agent_to_response(agent)


@router.get("", response_model=list[AIAgentOut])
async def list_agents(
    is_active: bool | None = Query(None),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    agents = await service.get_agents(is_active=is_active)
    return [agent_to_response(a) for a in agents]


@router.get("/default", response_model=AIAgentOut)
async def get_default_agent(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    agent = await service.get_default_agent()
    if not agent:
        raise NotFoundError("No default agent set for this company")
    return agent_to_response(agent)


@router.get("/{agent_id}", response_model=AIAgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    agent = await service.get_agent(agent_id)
    return agent_to_response(agent)


@router.put("/{agent_id}", response_model=AIAgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: dict = Body(...),
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = AIAgentUpdate.model_validate(body)
    except ValidationError as exc:
        raise format_validation_error(exc)
    service = AIAgentService(db, company_id)
    agent = await service.update_agent(
        agent_id, data.model_dump(exclude_unset=True)
    )
    return agent_to_response(agent)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    await service.delete_agent(agent_id)
    return {"message": "Agent deleted successfully"}


@router.post("/{agent_id}/set-default", response_model=AIAgentOut)
async def set_default_agent(
    agent_id: uuid.UUID,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    agent = await service.set_default(agent_id)
    return agent_to_response(agent)


@router.post("/{agent_id}/clone", response_model=AIAgentOut, status_code=201)
async def clone_agent(
    agent_id: uuid.UUID,
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = AIAgentService(db, company_id)
    agent = await service.clone_agent(agent_id, created_by=admin.id)
    return agent_to_response(agent)


class TestChatRequest(BaseModel):
    message: str
    history: list = []


@router.post("/{agent_id}/test-chat")
async def test_chat(
    agent_id: uuid.UUID,
    body: TestChatRequest,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Test agent conversation without making a real call."""
    service = AIAgentService(db, company_id)
    agent = await service.get_agent(agent_id)

    settings = get_settings()
    if not settings.openrouter_api_key:
        raise BadRequestError("OpenRouter API key not configured")

    detected_lang = detect_language(body.message)
    lang_instruction = get_language_instruction(detected_lang)
    enhanced_message = f"{lang_instruction}\n\nUser: {body.message}"

    messages = [
        {"role": "system", "content": agent.system_prompt},
        *body.history,
        {"role": "user", "content": enhanced_message},
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": agent.llm_model,
                    "temperature": agent.llm_temperature,
                    "max_tokens": agent.llm_max_tokens,
                    "messages": messages,
                },
            )
    except httpx.RequestError as exc:
        logger.error("OpenRouter network error: %s", exc)
        raise BadRequestError("Could not reach LLM provider")

    if resp.status_code != 200:
        logger.error("OpenRouter error: %s %s", resp.status_code, resp.text)
        raise BadRequestError("LLM request failed")

    try:
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("OpenRouter response parse error: %s", exc)
        raise BadRequestError("Invalid LLM response")
    return {
        "response": reply,
        "history": [
            *body.history,
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": reply},
        ],
    }
