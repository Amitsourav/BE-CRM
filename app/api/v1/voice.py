import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

import httpx
import phonenumbers
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import AsyncSessionLocal, get_db
from app.dependencies import get_current_user
from app.core.rate_limit import limiter
from app.models.ai_agent import AIAgent
from app.models.call_attempt import CallAttempt
from app.models.profile import Profile
from app.services.voice_engine import (
    call_state_manager,
    plivo_handler,
    voice_pipeline,
)
from app.services.voice_engine.audio_utils import (
    decode_plivo_audio,
    encode_for_plivo,
    is_silence_mulaw,
    mulaw_to_wav,
    wav_to_mulaw,
)
from app.services.voice_engine.stream_token import (
    generate_stream_token,
    verify_stream_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Voice"])


# ─────────────────────────────────────────────
# SCHEMAS
# ─────────────────────────────────────────────


class OutboundCallRequest(BaseModel):
    lead_id: uuid.UUID
    agent_id: uuid.UUID
    phone_number: str
    lead_name: Optional[str] = "there"


def normalize_e164(raw: str, default_region: str = "IN") -> Optional[str]:
    """Normalize any messy phone string into E.164 (+countrycode + digits).

    Handles spaces, dashes, parens, country-code-less mobile numbers.
    Returns None if the number can't be parsed/validated.
    """
    if not raw:
        return None
    # Fast-path: already a clean E.164
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())
    try:
        parsed = phonenumbers.parse(cleaned, default_region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
    except phonenumbers.NumberParseException:
        return None


# Resolve forward references caused by `from __future__ import annotations`
OutboundCallRequest.model_rebuild()


# ─────────────────────────────────────────────
# WEBHOOK SIGNATURE VERIFICATION
# ─────────────────────────────────────────────


def verify_plivo_webhook(request: Request) -> bool:
    """Verify request came from Plivo. Skip in development.

    NOTE: For signature checks to be enforced in production, the Railway
    env var APP_ENV must be set to "production" (not "development").
    """
    settings = get_settings()
    if settings.app_env == "development":
        return True

    signature = request.headers.get("X-Plivo-Signature-V2", "")
    if not signature:
        return False

    return plivo_handler.verify_signature(
        url=str(request.url),
        params=dict(request.query_params),
        signature=signature,
    )


# ─────────────────────────────────────────────
# POST /voice/outbound
# ─────────────────────────────────────────────


@router.post("/outbound")
@limiter.limit("10/minute;100/hour")
async def initiate_outbound_call(
    request: Request,
    body: OutboundCallRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Start an outbound AI call to a lead."""
    # Normalize phone number to E.164 — frontend may send raw DB values
    # like "9876543210" or "+91 98765 43210" which Plivo rejects.
    e164 = normalize_e164(body.phone_number, default_region="IN")
    if not e164:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid phone number: {body.phone_number!r}. Expected E.164 format (e.g. +919876543210).",
        )
    # Mutate body so downstream uses the normalized number
    body.phone_number = e164

    result = await db.execute(
        select(AIAgent).where(
            AIAgent.id == body.agent_id,
            AIAgent.company_id == current_user.company_id,
            AIAgent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    call_id = uuid.uuid4()

    call = CallAttempt(
        id=call_id,
        company_id=current_user.company_id,
        lead_id=body.lead_id,
        agent_id=current_user.id,
        telecaller_id=current_user.id,
        ai_agent_id=body.agent_id,
        phone_number=body.phone_number,
        attempt_number=1,
        disposition="connected",
        conversation_notes="",
        agent_agenda="",
        call_type="ai",
        call_status="initiated",
        started_at=datetime.utcnow(),
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)

    # Pre-generate welcome audio BEFORE dialing so the first WS frame can
    # be sent instantly on call answer (saves 2-4s of perceived delay).
    # Failure here is non-fatal — WS handler will regenerate on demand.
    try:
        welcome_audio = await asyncio.wait_for(
            voice_pipeline.generate_welcome_audio(
                agent=agent,
                lead_name=body.lead_name or "there",
            ),
            timeout=5.0,
        )
    except Exception as e:
        logger.warning("pre-gen welcome audio failed: %s", e)
        welcome_audio = b""

    call_state_manager.create(
        call_id=str(call_id),
        agent_id=str(body.agent_id),
        lead_id=str(body.lead_id),
        company_id=str(current_user.company_id),
        lead_name=body.lead_name or "there",
        welcome_audio=welcome_audio,
    )

    try:
        plivo_response = await plivo_handler.make_call(
            to_number=body.phone_number,
            call_id=str(call_id),
            agent_id=str(body.agent_id),
            lead_id=str(body.lead_id),
            lead_name=body.lead_name or "there",
            time_limit=getattr(agent, "call_timeout_seconds", None) or 600,
            ring_timeout=30,
        )
    except Exception as e:
        # Catches anything that escapes plivo_handler.make_call (it normally
        # catches its own exceptions, but defense in depth — never let an
        # unhandled error become a 500 to the frontend)
        logger.error("plivo make_call raised: %s", e)
        plivo_response = {"success": False, "error": str(e)}

    if not plivo_response.get("success"):
        call.call_status = "failed"
        await db.commit()
        call_state_manager.remove(str(call_id))
        # 400 — caller-facing error (bad number, Plivo rejected, no balance,
        # signature failure). Frontend can show the message to the user
        # instead of a generic "server error".
        raise HTTPException(
            status_code=400,
            detail=f"Call failed: {plivo_response.get('error') or 'unknown error'}",
        )

    # Plivo has already accepted the call — commit failure here must NOT
    # fail the request. The phone is already ringing at this point.
    call.external_call_id = plivo_response.get("plivo_call_uuid", "")
    call.call_status = "ringing"
    try:
        await db.commit()
    except Exception as e:
        logger.warning(
            "outbound: post-dial status commit failed (call %s is already ringing): %s",
            call_id,
            e,
        )

    return {
        "success": True,
        "call_id": str(call_id),
        "status": "ringing",
        "message": "Call initiated successfully",
    }


# ─────────────────────────────────────────────
# POST /voice/answer  (Plivo webhook)
# ─────────────────────────────────────────────


# Small in-memory agent cache (30s TTL) to avoid repeat Supabase lookups
_AGENT_CACHE: "dict[str, tuple]" = {}
_AGENT_CACHE_TTL = 30.0


async def _lookup_agent_cached(agent_id: str) -> Optional[AIAgent]:
    """Load agent with 3s hard timeout + 30s in-memory cache.

    Returns None if the lookup times out or fails — caller decides fallback.
    """
    import time
    entry = _AGENT_CACHE.get(agent_id)
    if entry and entry[1] > time.time():
        return entry[0]
    try:
        async with AsyncSessionLocal() as db:
            result = await asyncio.wait_for(
                db.execute(select(AIAgent).where(AIAgent.id == uuid.UUID(agent_id))),
                timeout=3.0,
            )
            agent = result.scalar_one_or_none()
            if agent:
                _AGENT_CACHE[agent_id] = (agent, time.time() + _AGENT_CACHE_TTL)
            return agent
    except Exception as e:
        logger.warning("agent lookup failed for %s: %s", agent_id, e)
        return None


@router.post("/answer")
async def handle_answer(
    request: Request,
    background_tasks: BackgroundTasks,
    call_id: str = Query(...),
    agent_id: str = Query(...),
    lead_id: str = Query(...),
    lead_name: str = Query("there"),
):
    """Plivo answer_url webhook.

    Logs the full request so we can see exactly what Plivo sends.
    Looks up agent with 3s timeout — if lookup fails or agent is null,
    returns a <Speak> fallback so Plivo has something to play instead
    of silence.
    """
    client_host = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "")

    # ── Fix 4: Print full webhook body ──
    try:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
    except Exception as e:
        body_str = f"<failed to read body: {e}>"

    logger.info(
        "ANSWER_WEBHOOK_IN call_id=%s agent_id=%s lead_id=%s lead_name=%r "
        "from=%s ua=%s query=%s body=%r",
        call_id, agent_id, lead_id, lead_name,
        client_host, ua, dict(request.query_params), body_str,
    )

    if not verify_plivo_webhook(request):
        logger.warning("ANSWER_WEBHOOK_REJECTED bad signature call_id=%s", call_id)
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    # ── Fix 2: Look up agent (cached + timeout-bound) ──
    agent = await _lookup_agent_cached(agent_id)
    if agent:
        logger.info(
            "ANSWER_WEBHOOK_AGENT call_id=%s loaded agent_id=%s name=%r "
            "llm_model=%s tts_provider=%s tts_voice=%s has_prompt=%s",
            call_id, agent.id, agent.name,
            agent.llm_model, agent.tts_provider, agent.tts_voice,
            bool(agent.system_prompt),
        )
    else:
        logger.error(
            "ANSWER_WEBHOOK_AGENT_MISSING call_id=%s agent_id=%s — "
            "returning <Speak> fallback",
            call_id, agent_id,
        )

    # Fire-and-forget DB write for call_attempts.call_status = 'connected'
    background_tasks.add_task(_update_call_status_background, call_id, "connected")

    # ── Fix 3: Fallback <Speak> if agent lookup failed ──
    if not agent:
        fallback_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Speak voice="Polly.Aditi">Agent not configured. Please contact support.</Speak>'
            '<Hangup/>'
            '</Response>'
        )
        logger.info("ANSWER_WEBHOOK_OUT call_id=%s fallback_xml_bytes=%d",
                    call_id, len(fallback_xml))
        return Response(content=fallback_xml, media_type="application/xml")

    stream_token = generate_stream_token(call_id)
    xml = plivo_handler.generate_answer_xml(
        call_id=call_id,
        welcome_message="",  # unused — WS handler plays the real welcome audio
        stream_token=stream_token,
    )
    logger.info(
        "ANSWER_WEBHOOK_OUT call_id=%s xml_bytes=%d stream_token=%s...",
        call_id, len(xml), stream_token[:16],
    )
    return Response(content=xml, media_type="application/xml")


async def _update_call_status_background(call_id: str, status: str):
    """Fire-and-forget status update — never blocks Plivo's answer flow."""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CallAttempt).where(CallAttempt.id == uuid.UUID(call_id))
            )
            call = result.scalar_one_or_none()
            if call:
                call.call_status = status
                await db.commit()
    except Exception as e:
        logger.warning("background call status update failed: %s", e)


# ─────────────────────────────────────────────
# WebSocket /voice/stream/{call_id}
# ─────────────────────────────────────────────


# Silence/turn-taking thresholds (Plivo media frames are ~20ms each)
# Tuned for streaming STT pipeline — lower latency than batch-STT defaults
SILENCE_THRESHOLD = 15       # 15 frames ≈ 300ms of trailing silence
MIN_BUFFER_SIZE = 3200       # ~400ms of mulaw @ 8kHz before we'll process
MIN_SPEECH_FRAMES = 6        # require ≥120ms of non-silence before turn ends


async def _reset_speaking_flag(state, duration_seconds: float):
    """Reset is_agent_speaking after the playback duration elapses."""
    try:
        await asyncio.sleep(max(0.1, duration_seconds))
    finally:
        state.is_agent_speaking = False


async def _send_audio_response(websocket: WebSocket, state, wav_bytes: bytes):
    """Convert WAV → mulaw → base64, send playAudio frame, set speaking flag."""
    if not wav_bytes:
        return
    mulaw_response = wav_to_mulaw(wav_bytes)
    if not mulaw_response:
        return
    b64_audio = encode_for_plivo(mulaw_response)
    await websocket.send_text(
        json.dumps(
            {
                "event": "playAudio",
                "media": {
                    "contentType": "audio/x-mulaw",
                    "sampleRate": "8000",
                    "payload": b64_audio,
                },
            }
        )
    )
    duration = len(mulaw_response) / 8000.0
    state.is_agent_speaking = True
    asyncio.create_task(_reset_speaking_flag(state, duration))


@router.websocket("/stream/{call_id}")
async def voice_stream(
    websocket: WebSocket,
    call_id: str,
    token: Optional[str] = Query(None),
):
    """Plivo bidirectional Stream — JSON text frames with base64 mulaw.

    Auth: HMAC token in ?token=... query param. Validated before accept().
    Dev bypass: when app_env=development and token missing, allow connection
    so local testing without Plivo works.
    """
    settings = get_settings()
    dev_bypass = settings.app_env == "development" and not token
    if not dev_bypass and not verify_stream_token(call_id, token or ""):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    state = call_state_manager.get(call_id)
    if not state:
        await websocket.close()
        return

    agent = None
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AIAgent).where(AIAgent.id == uuid.UUID(state.agent_id))
            )
            agent = result.scalar_one_or_none()
    except Exception as e:
        logger.error("stream: agent fetch failed: %s", e)

    if not agent:
        await websocket.close()
        return

    # Log the FULL agent config being used for this call so we can verify
    # in Railway logs that dashboard changes actually affect call behavior.
    logger.info(
        "AGENT_CONFIG call_id=%s agent_id=%s name=%r | "
        "llm_model=%s temp=%s max_tokens=%s | "
        "stt_provider=%s stt_model=%s | "
        "tts_provider=%s tts_model=%s tts_voice=%s tts_speed=%s | "
        "tts_en=%s/%s/%s tts_hi=%s/%s/%s | "
        "endpointing_ms=%s words_before_interrupt=%s max_response_words=%s | "
        "silence_detection_seconds=%s hangup_on_silence=%s | "
        "welcome=%r has_system_prompt=%s",
        call_id, agent.id, agent.name,
        agent.llm_model, agent.llm_temperature, agent.llm_max_tokens,
        agent.stt_provider, agent.stt_model,
        agent.tts_provider, agent.tts_model, agent.tts_voice, agent.tts_speed,
        agent.tts_provider_english, agent.tts_model_english, agent.tts_voice_english,
        agent.tts_provider_hindi, agent.tts_model_hindi, agent.tts_voice_hindi,
        agent.endpointing_ms, agent.words_before_interrupt, agent.max_response_words,
        agent.silence_detection_seconds, agent.hangup_on_silence_seconds,
        agent.welcome_message, bool(agent.system_prompt),
    )

    # Derive silence threshold from agent.endpointing_ms (frames @ 20ms each).
    # Clamp to reasonable range so a misconfigured agent can't break turn-taking.
    silence_threshold = max(5, min(50, (agent.endpointing_ms or 300) // 20))
    min_speech_frames = max(3, MIN_SPEECH_FRAMES)

    mulaw_buffer = bytearray()
    silence_frames = 0
    speech_frames = 0
    was_agent_speaking = False

    try:
        while True:
            message = await asyncio.wait_for(websocket.receive(), timeout=30.0)

            if "text" not in message or message["text"] is None:
                continue

            try:
                data = json.loads(message["text"])
            except json.JSONDecodeError:
                continue

            event = data.get("event", "")

            if event == "start":
                # Mark connected in DB
                try:
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            select(CallAttempt).where(
                                CallAttempt.id == uuid.UUID(call_id)
                            )
                        )
                        call = result.scalar_one_or_none()
                        if call:
                            call.call_status = "connected"
                            await db.commit()
                except Exception as e:
                    logger.warning("stream start: status update failed: %s", e)

                # Play welcome audio via configured TTS as the first frame.
                # Prefer the pre-generated audio from /voice/outbound (instant).
                # Only generate now if pre-gen failed — avoids 2-4s delay.
                try:
                    welcome_wav = state.welcome_audio
                    if not welcome_wav:
                        welcome_wav = await voice_pipeline.generate_welcome_audio(
                            agent=agent, lead_name=state.lead_name
                        )
                    if welcome_wav:
                        await _send_audio_response(websocket, state, welcome_wav)
                    else:
                        logger.warning("welcome audio empty for call %s", call_id)
                except Exception as e:
                    logger.error("welcome audio failed: %s", e)
                continue

            if event == "stop":
                break

            if event != "media":
                continue

            # Echo prevention — drop user-side chunks while agent is talking
            if state.is_agent_speaking:
                was_agent_speaking = True
                continue

            # Just transitioned out of agent-speaking — reset turn counters
            # so the buffered silence during playback doesn't trigger an
            # immediate spurious turn end
            if was_agent_speaking:
                mulaw_buffer = bytearray()
                silence_frames = 0
                speech_frames = 0
                was_agent_speaking = False

            payload = data.get("media", {}).get("payload", "")
            if not payload:
                continue

            mulaw_chunk = decode_plivo_audio(payload)
            mulaw_buffer.extend(mulaw_chunk)

            if is_silence_mulaw(mulaw_chunk):
                silence_frames += 1
            else:
                silence_frames = 0
                speech_frames += 1

            # Only trigger pipeline once we've heard real speech AND
            # the user has been silent long enough to indicate end-of-turn
            if (
                silence_frames >= silence_threshold
                and speech_frames >= min_speech_frames
                and len(mulaw_buffer) >= MIN_BUFFER_SIZE
            ):
                wav_audio = mulaw_to_wav(bytes(mulaw_buffer))
                mulaw_buffer = bytearray()
                silence_frames = 0
                speech_frames = 0

                result = await voice_pipeline.process_audio(
                    call_id=call_id,
                    audio_bytes=wav_audio,
                    agent=agent,
                )

                if result.get("audio_response"):
                    await _send_audio_response(
                        websocket, state, result["audio_response"]
                    )

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.info("WS %s timed out (no media for 30s)", call_id)
    except RuntimeError as e:
        # Starlette raises RuntimeError when receiving after disconnect
        logger.info("WS %s already disconnected: %s", call_id, e)
    except Exception as e:
        logger.exception("WS %s unexpected error: %s", call_id, e)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ─────────────────────────────────────────────
# POST /voice/hangup  (Plivo webhook)
# ─────────────────────────────────────────────


@router.post("/hangup")
async def handle_hangup(
    request: Request,
    background_tasks: BackgroundTasks,
    call_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    if not verify_plivo_webhook(request):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    state = call_state_manager.get(call_id)

    try:
        result = await db.execute(
            select(CallAttempt).where(CallAttempt.id == uuid.UUID(call_id))
        )
        call = result.scalar_one_or_none()

        if call:
            call.call_status = "ended"
            call.ended_at = datetime.utcnow()

            if state:
                call.transcript = state.get_full_transcript()
                call.call_duration_seconds = state.get_duration_seconds()

            await db.commit()
    except Exception as e:
        logger.error("hangup: db update failed: %s", e)

    if state and state.transcript_segments:
        background_tasks.add_task(
            _save_summary_background,
            call_id=call_id,
            transcript=state.get_full_transcript(),
        )

    if state:
        call_state_manager.remove(call_id)

    return Response(
        content="<?xml version='1.0'?><Response/>",
        media_type="application/xml",
    )


async def _save_summary_background(call_id: str, transcript: str):
    """Generate summary then persist — runs after Plivo gets its response."""
    summary = await _generate_summary(transcript)
    if not summary:
        return
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CallAttempt).where(CallAttempt.id == uuid.UUID(call_id))
            )
            call = result.scalar_one_or_none()
            if call:
                call.summary = summary
                await db.commit()
    except Exception as e:
        logger.error("background summary save failed: %s", e)


# ─────────────────────────────────────────────
# GET /voice/active-calls
# ─────────────────────────────────────────────


@router.get("/active-calls")
async def get_active_calls(
    current_user: Profile = Depends(get_current_user),
):
    return {
        "active_calls": call_state_manager.get_all_active(
            company_id=str(current_user.company_id)
        )
    }


# ─────────────────────────────────────────────
# GET /voice/call/{call_id}/status
# ─────────────────────────────────────────────


@router.get("/call/{call_id}/status")
async def get_call_status(
    call_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    # Validate UUID format BEFORE hitting the DB — bad input → 400 not 500
    try:
        call_uuid = uuid.UUID(call_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid call_id: {call_id!r}")

    # In-memory state is fast and never fails. Check it first for live calls —
    # this means live-call polling doesn't hit Supabase at all, and is instant.
    state = call_state_manager.get(call_id)
    if state:
        return {
            "call_id": call_id,
            "status": "connected" if state.total_turns > 0 else "ringing",
            "duration": state.get_duration_seconds(),
            "turns": state.total_turns,
            "language": state.current_language,
            "is_live": True,
        }

    # No in-memory state → look up in DB with a hard timeout so Supabase
    # slowness can never cause a 500. Return 404 on any failure.
    try:
        result = await asyncio.wait_for(
            db.execute(
                select(CallAttempt).where(
                    CallAttempt.id == call_uuid,
                    CallAttempt.company_id == current_user.company_id,
                )
            ),
            timeout=5.0,
        )
        call = result.scalar_one_or_none()
    except asyncio.TimeoutError:
        logger.warning("call status DB lookup timed out for %s", call_id)
        raise HTTPException(status_code=404, detail="Call not found")
    except Exception as e:
        logger.warning("call status DB lookup failed for %s: %s", call_id, e)
        raise HTTPException(status_code=404, detail="Call not found")

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    return {
        "call_id": call_id,
        "status": call.call_status,
        "duration": call.call_duration_seconds,
        "turns": 0,
        "language": "en",
        "is_live": False,
    }


# ─────────────────────────────────────────────
# HELPER — AI summary
# ─────────────────────────────────────────────


async def _generate_summary(transcript: str) -> str:
    if not transcript:
        return ""

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a call summarizer. Summarize this sales "
                                "call transcript in 3-4 sentences. Include: "
                                "1) What the lead wants 2) Key info shared "
                                "3) Next steps agreed. Be concise and factual."
                            ),
                        },
                        {"role": "user", "content": transcript},
                    ],
                    "max_tokens": 200,
                    "temperature": 0.3,
                },
            )
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("summary generation failed: %s", e)

    return ""
