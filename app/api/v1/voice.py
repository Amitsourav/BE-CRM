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
from app.core.tenant import get_current_company_id
from app.core.rate_limit import limiter
from app.models.ai_agent import AIAgent
from app.models.call_attempt import CallAttempt
from app.models.profile import Profile
from app.models.activity_log import ActivityLog
from app.models.lead_stage_log import LeadStageLog
from app.services.voice_engine.http_clients import get_openrouter_client
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
    nonce = request.headers.get("X-Plivo-Signature-V2-Nonce", "")
    if not signature:
        return False

    # Reconstruct the public URL — behind Railway's proxy, request.url
    # shows the internal address (http://0.0.0.0:8000/...) but Plivo
    # signed with the public URL. Use backend_url + path + query.
    public_url = f"{settings.backend_url}{request.url.path}"
    if request.url.query:
        public_url = f"{public_url}?{request.url.query}"

    try:
        return plivo_handler.verify_signature(
            url=public_url,
            signature=signature,
            nonce=nonce,
        )
    except Exception as e:
        logger.warning("Plivo signature check error: %s (url=%s)", e, public_url)
        return False


# ─────────────────────────────────────────────
# POST /voice/outbound
# ─────────────────────────────────────────────


@router.post("/outbound")
@limiter.limit("10/minute;100/hour")
async def initiate_outbound_call(
    request: Request,
    body: OutboundCallRequest = Body(...),
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

    # Pre-seed the /answer webhook's in-memory agent cache so the Plivo
    # answer_url callback can skip its ~700ms cross-region Supabase lookup.
    # Without this, the cache is cold on first touch → a call always pays
    # one extra Supabase round-trip between phone-answer and welcome audio.
    import time as _time
    _AGENT_CACHE[str(body.agent_id)] = (agent, _time.time() + _AGENT_CACHE_TTL)

    # Enforce call-hours window if the agent opts in. Interpreted as IST
    # (server-side clock) in 24h "HH:MM" format. Wrap-around (e.g. 22:00-06:00)
    # is supported by checking for "outside" the window when start > end.
    if getattr(agent, "restrict_call_hours", False):
        from datetime import datetime as _dt, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        now_hm = _dt.now(ist).strftime("%H:%M")
        start = agent.call_start_time or "09:00"
        end = agent.call_end_time or "19:00"
        in_window = (
            (start <= now_hm <= end) if start <= end
            else (now_hm >= start or now_hm <= end)
        )
        if not in_window:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Agent '{agent.name}' is restricted to {start}-{end} IST. "
                    f"Current time: {now_hm} IST."
                ),
            )

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

    # Create call state FIRST, then kick off welcome TTS in the background.
    # Phone ringing takes 5-8s — plenty of time for Sarvam TTS (~3-5s) to
    # finish while the phone is ringing. Do NOT block the outbound response.
    try:
        state = call_state_manager.create(
            call_id=str(call_id),
            agent_id=str(body.agent_id),
            lead_id=str(body.lead_id),
            company_id=str(current_user.company_id),
            lead_name=body.lead_name or "there",
            company_name=current_user.company_name,
        )
    except RuntimeError as e:
        call.call_status = "failed"
        await db.commit()
        raise HTTPException(status_code=429, detail=str(e))
    state.welcome_ready = asyncio.Event()
    # Cache the already-loaded agent on state so WS handler doesn't have
    # to re-fetch from Supabase (saves ~700ms per call on cross-region DB).
    state.agent = agent

    async def _pregen_welcome():
        import time
        t0 = time.time()
        try:
            wav = await voice_pipeline.generate_welcome_audio(
                agent=agent,
                lead_name=body.lead_name or "there",
                company_name=current_user.company_name,
            )
            state.welcome_audio = wav or b""
            # Pre-convert to mulaw+base64 so the WS start handler
            # can send it instantly without any conversion delay.
            if state.welcome_audio:
                mulaw = wav_to_mulaw(state.welcome_audio)
                if mulaw:
                    state.welcome_audio_b64 = encode_for_plivo(mulaw)
            logger.info(
                "WELCOME_PREGEN call_id=%s bytes=%d b64=%d elapsed=%.2fs",
                call_id, len(state.welcome_audio),
                len(state.welcome_audio_b64), time.time() - t0,
            )
        except Exception as e:
            logger.warning(
                "WELCOME_PREGEN_FAIL call_id=%s elapsed=%.2fs err=%s",
                call_id, time.time() - t0, e,
            )
        finally:
            state.welcome_ready.set()

    asyncio.create_task(_pregen_welcome())

    # Pre-warm the LLM in parallel with welcome pre-gen. Groq keeps a
    # model hot for ~5 minutes after use, then cold-starts the next
    # request (3-6s first-token latency). Phone rings for 5-8s before
    # the user picks up — we use that idle time to send a 1-token ping
    # that wakes the model up, so the real first turn is served warm.
    async def _warmup_llm():
        import time
        t0 = time.time()
        try:
            from app.services.voice_engine.llm_service import llm_service as _llm
            await _llm.warmup(model=agent.llm_model)
            logger.info(
                "LLM_WARMUP call_id=%s model=%s elapsed=%.2fs",
                call_id, agent.llm_model, time.time() - t0,
            )
        except Exception as e:
            logger.debug(
                "LLM_WARMUP_FAIL call_id=%s elapsed=%.2fs err=%s",
                call_id, time.time() - t0, e,
            )

    asyncio.create_task(_warmup_llm())

    # Warm the Sarvam STT connection in parallel. First /speech-to-text
    # call pays TLS+HTTP/2 setup (~200-500ms). A dummy request during
    # ring time establishes the connection so turn-1 STT is fast.
    async def _warmup_stt():
        import time
        t0 = time.time()
        try:
            from app.services.voice_engine.sarvam_stt import sarvam_stt as _stt
            await _stt.warmup(model=agent.stt_model or "saaras:v3")
            logger.info(
                "STT_WARMUP call_id=%s model=%s elapsed=%.2fs",
                call_id, agent.stt_model, time.time() - t0,
            )
        except Exception as e:
            logger.debug(
                "STT_WARMUP_FAIL call_id=%s elapsed=%.2fs err=%s",
                call_id, time.time() - t0, e,
            )

    asyncio.create_task(_warmup_stt())

    try:
        plivo_response = await plivo_handler.make_call(
            to_number=body.phone_number,
            call_id=str(call_id),
            agent_id=str(body.agent_id),
            lead_id=str(body.lead_id),
            lead_name=body.lead_name or "there",
            time_limit=getattr(agent, "call_timeout_seconds", None) or 600,
            ring_timeout=30,
            from_number=getattr(agent, "phone_number", None) or "",
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

    # Body read previously happened here for logging. Removed from the hot
    # path — reading + logging several KB of Plivo form data synchronously
    # was adding ~50-150ms before we could even start the agent lookup.
    # If you need to debug what Plivo sends, flip DEBUG_ANSWER_BODY below.
    logger.info(
        "ANSWER_WEBHOOK_IN call_id=%s agent_id=%s lead_id=%s lead_name=%r from=%s",
        call_id, agent_id, lead_id, lead_name, client_host,
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
MIN_BUFFER_SIZE = 1600       # ~200ms of mulaw @ 8kHz — lowered to catch
                             # short words like "yeah", "yupp", "han ji"
                             # (was 3200/400ms which cut off 300ms utterances)
MIN_SPEECH_FRAMES = 4        # require ≥80ms of non-silence before turn ends


async def _reset_speaking_flag(state, duration_seconds: float):
    """Reset is_agent_speaking after the playback duration elapses."""
    try:
        await asyncio.sleep(max(0.1, duration_seconds))
    finally:
        state.is_agent_speaking = False


async def _silence_watchdog(
    call_id: str,
    ts_ref: list,
    max_silence_seconds: int,
    websocket: WebSocket,
    state,
    agent,
):
    """Play silence prompt → if still silent, play final message → hangup.

    ts_ref is a single-element list [float] shared with the WS handler.
    WS handler writes ts_ref[0] = now on every inbound media frame.
    This watchdog reads ts_ref[0] to compute idle time, and also RESETS
    ts_ref[0] to 'now' while the agent is speaking — otherwise a long
    agent reply (>5s) would be counted as 'user silence' and the
    'are you still there?' prompt would get spoken on top of the
    agent's own reply.
    """
    prompted = False
    try:
        loop = asyncio.get_event_loop()
        from app.services.voice_engine import voice_pipeline
        while True:
            await asyncio.sleep(2.0)

            # Pause idle counter during agent speech — continuously
            # refresh the 'last seen' timestamp so idle time can't
            # accumulate while the agent is itself talking.
            if state.is_agent_speaking:
                ts_ref[0] = loop.time()
                prompted = False
                continue

            idle = loop.time() - ts_ref[0]

            # Halfway through the silence budget: nudge the user once
            if not prompted and idle >= (max_silence_seconds / 2):
                prompted = True
                lang = state.current_language or "en"
                msg = (
                    agent.silence_message_hi if lang == "hi"
                    else agent.silence_message_en
                )
                if msg:
                    try:
                        wav = await voice_pipeline._get_tts_audio(
                            text=msg, language=lang, agent=agent,
                        )
                        if wav:
                            await _send_audio_response(
                                websocket, state, wav,
                                buffer_size=agent.tts_buffer_size or 0,
                            )
                    except Exception as e:
                        logger.warning("silence_message send failed: %s", e)

            if idle >= max_silence_seconds:
                logger.info(
                    "SILENCE_HANGUP call_id=%s idle=%.1fs threshold=%ds",
                    call_id, idle, max_silence_seconds,
                )
                # Play final message before hanging up
                lang = state.current_language or "en"
                final = (
                    agent.final_message_hi if lang == "hi"
                    else agent.final_message_en
                )
                if final:
                    try:
                        wav = await voice_pipeline._get_tts_audio(
                            text=final, language=lang, agent=agent,
                        )
                        if wav:
                            await _send_audio_response(
                                websocket, state, wav,
                                buffer_size=agent.tts_buffer_size or 0,
                            )
                            # Give Plivo a moment to play it
                            await asyncio.sleep(min(6.0, len(wav) / 16000))
                    except Exception as e:
                        logger.warning("final_message send failed: %s", e)
                try:
                    from app.services.voice_engine import plivo_handler
                    await plivo_handler.hangup_call(call_id)
                except Exception as e:
                    logger.warning("silence watchdog hangup failed: %s", e)
                return
    except asyncio.CancelledError:
        pass


async def _send_audio_response(
    websocket: WebSocket,
    state,
    wav_bytes: bytes,
    buffer_size: int = 0,
    auto_reset: bool = True,
) -> float:
    """Convert WAV → mulaw → base64, send playAudio frame(s), set speaking flag.

    If buffer_size > 0, chunk the mulaw payload into buffer_size byte pieces
    and send sequential frames. Smaller buffers give Plivo faster playback
    start at the cost of more frames. 0 = send as one frame.

    If auto_reset is False, caller is responsible for resetting
    is_agent_speaking after the final chunk in a stream. This avoids
    prematurely clearing the echo-prevention flag when streaming multiple
    sentence audio blobs in sequence.

    Returns: playback duration in seconds (caller may accumulate across
    streaming chunks to schedule a single end-of-turn reset).
    """
    if not wav_bytes:
        return 0.0
    mulaw_response = wav_to_mulaw(wav_bytes)
    if not mulaw_response:
        return 0.0

    total_len = len(mulaw_response)
    if buffer_size and buffer_size > 0 and buffer_size < total_len:
        # Send in chunks; each frame is an independent playAudio event.
        # Plivo queues them in order on the call's playback buffer.
        for i in range(0, total_len, buffer_size):
            chunk = mulaw_response[i : i + buffer_size]
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "playAudio",
                        "media": {
                            "contentType": "audio/x-mulaw",
                            "sampleRate": "8000",
                            "payload": encode_for_plivo(chunk),
                        },
                    }
                )
            )
    else:
        await websocket.send_text(
            json.dumps(
                {
                    "event": "playAudio",
                    "media": {
                        "contentType": "audio/x-mulaw",
                        "sampleRate": "8000",
                        "payload": encode_for_plivo(mulaw_response),
                    },
                }
            )
        )

    duration = total_len / 8000.0
    state.is_agent_speaking = True
    if auto_reset:
        asyncio.create_task(_reset_speaking_flag(state, duration))
    return duration


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

    # Prefer the agent cached on state by /voice/outbound — saves one
    # cross-region Supabase round-trip (~700ms). Fall back to a fresh
    # DB lookup only if the state object somehow lost it (e.g. worker
    # restart between dial and answer).
    agent = state.agent
    if not agent:
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
    # Frames at 20ms each. 5 = 100ms floor, 100 = 2000ms ceiling.
    # Thinking/hesitant speakers need 800-1200ms to avoid premature
    # turn-end during natural pauses ("so I'm thinking for... ummm").
    silence_threshold = max(5, min(100, (agent.endpointing_ms or 300) // 20))
    min_speech_frames = max(3, MIN_SPEECH_FRAMES)

    # Barge-in threshold: detect real interruption, ignore noise.
    # 24 frames = ~480ms — closer to human cut-in time (300-500ms).
    # Previously 36 frames (~720ms) made interruptions feel slow and
    # short utterances ("ruko", "nahi") never crossed threshold. Slow-decay
    # on silence (added later) handles the false-trigger risk that earlier
    # made 24 too aggressive.
    barge_in_frames = max(12, (agent.words_before_interrupt or 3) * 8)

    # Last time we saw inbound media, for silence-watchdog.
    # Using a single-element list so the watchdog task can both read and
    # write it (Python closures can read an outer var, but reassigning
    # a float rebinds the local — list element mutation works across
    # closures cleanly).
    last_media_ts: list = [asyncio.get_event_loop().time()]
    hangup_silence_sec = max(5, agent.hangup_on_silence_seconds or 10)
    watchdog_task = asyncio.create_task(
        _silence_watchdog(
            call_id=call_id,
            ts_ref=last_media_ts,
            max_silence_seconds=hangup_silence_sec,
            websocket=websocket,
            state=state,
            agent=agent,
        )
    )

    mulaw_buffer = bytearray()
    silence_frames = 0
    speech_frames = 0
    speech_frames_during_playback = 0  # for barge-in
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
                # Mark connected in DB — fire-and-forget so the welcome
                # audio isn't blocked by a ~700ms cross-region Supabase
                # commit. Users hear the welcome immediately; DB catches
                # up in the background.
                asyncio.create_task(
                    _update_call_status_background(call_id, "connected")
                )

                # Play welcome audio. Use pre-encoded mulaw+base64 if
                # available (skips wav_to_mulaw+encode, saves ~100-200ms).
                import time as _time
                t_start = _time.time()
                try:
                    if state.welcome_ready:
                        try:
                            await asyncio.wait_for(
                                state.welcome_ready.wait(), timeout=2.0
                            )
                        except asyncio.TimeoutError:
                            pass

                    # Fast path: pre-encoded during ring time
                    if state.welcome_audio_b64:
                        await websocket.send_text(
                            json.dumps({
                                "event": "playAudio",
                                "media": {
                                    "contentType": "audio/x-mulaw",
                                    "sampleRate": "8000",
                                    "payload": state.welcome_audio_b64,
                                },
                            })
                        )
                        mulaw_len = len(state.welcome_audio_b64) * 3 // 4
                        duration = mulaw_len / 8000.0
                        state.is_agent_speaking = True
                        asyncio.create_task(
                            _reset_speaking_flag(state, duration)
                        )
                        source = "preencoded"
                        elapsed_ms = int((_time.time() - t_start) * 1000)
                        logger.info(
                            "WELCOME_PLAY call_id=%s source=%s wait_ms=%d",
                            call_id, source, elapsed_ms,
                        )
                    else:
                        # Slow path: generate fresh or use cached WAV
                        welcome_wav = state.welcome_audio
                        source = "cached"
                        if not welcome_wav:
                            source = "fresh"
                            welcome_wav = await voice_pipeline.generate_welcome_audio(
                                agent=agent,
                                lead_name=state.lead_name,
                                company_name=getattr(state, "company_name", None),
                            )
                        elapsed_ms = int((_time.time() - t_start) * 1000)
                        logger.info(
                            "WELCOME_PLAY call_id=%s source=%s bytes=%d wait_ms=%d",
                            call_id, source, len(welcome_wav or b""), elapsed_ms,
                        )
                        if welcome_wav:
                            await _send_audio_response(
                                websocket, state, welcome_wav,
                                buffer_size=agent.tts_buffer_size or 0,
                            )
                        else:
                            logger.warning("welcome audio empty for call %s", call_id)
                except Exception as e:
                    logger.error("welcome audio failed: %s", e)
                continue

            if event == "stop":
                break

            if event != "media":
                continue

            # Record media timestamp for silence watchdog (mutate the
            # list element so the watchdog task sees the new value)
            last_media_ts[0] = asyncio.get_event_loop().time()

            # Barge-in — while agent is speaking, track sustained user speech.
            # Once enough non-silence frames accumulate, stop agent playback.
            if state.is_agent_speaking:
                was_agent_speaking = True
                bi_payload = data.get("media", {}).get("payload", "")
                if bi_payload:
                    bi_chunk = decode_plivo_audio(bi_payload)
                    if not is_silence_mulaw(bi_chunk):
                        speech_frames_during_playback += 1
                        if speech_frames_during_playback >= barge_in_frames:

                            logger.info(
                                "BARGE_IN call_id=%s frames=%d threshold=%d — stopping agent",
                                call_id, speech_frames_during_playback, barge_in_frames,
                            )
                            try:
                                await websocket.send_text(
                                    json.dumps({"event": "clearAudio"})
                                )
                            except Exception as e:
                                logger.warning("clearAudio send failed: %s", e)
                            state.is_agent_speaking = False
                            speech_frames_during_playback = 0
                            mulaw_buffer = bytearray()
                            silence_frames = 0
                            speech_frames = 0
                            was_agent_speaking = False
                            # Start buffering this frame as the user's turn
                            mulaw_buffer.extend(bi_chunk)
                            speech_frames = 1
                    else:
                        # Decay slowly on silence instead of hard reset.
                        # Hard reset (=0) meant "ruk ... ruk" never accumulated
                        # enough frames because the gap between words reset the
                        # counter. Slow decay keeps most of the count so natural
                        # speech with pauses still triggers barge-in.
                        if speech_frames_during_playback > 0:
                            speech_frames_during_playback -= 1
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

                # Stream LLM → TTS sentence-by-sentence. Each sentence is
                # synthesized and sent as soon as it's ready, so the user
                # starts hearing the reply in ~1s instead of waiting for
                # the full LLM + TTS pipeline (~4s).
                total_duration = 0.0
                barged = False
                try:
                    async for out in voice_pipeline.process_audio_streaming(
                        call_id=call_id,
                        audio_bytes=wav_audio,
                        agent=agent,
                    ):
                        # BARGE-IN CHECK: if user interrupted during this
                        # turn's playback, stop sending more audio chunks.
                        # The barge-in handler in the media loop already
                        # reset is_agent_speaking — we just need to stop
                        # the pipeline from queuing more playAudio frames.
                        if not state.is_agent_speaking and total_duration > 0:
                            logger.info(
                                "BARGE_STOP call_id=%s — user interrupted, "
                                "stopping audio stream",
                                call_id,
                            )
                            barged = True
                            break
                        if out.get("done"):
                            # LLM signalled end-of-call via [END_CALL]
                            if out.get("end_call"):
                                # Wait for audio to finish, then hangup
                                async def _delayed_hangup(cid, delay):
                                    await asyncio.sleep(delay + 1.0)
                                    try:
                                        from app.services.voice_engine import plivo_handler
                                        await plivo_handler.hangup_call(cid)
                                        logger.info("END_CALL hangup call_id=%s", cid)
                                    except Exception as e:
                                        logger.warning("END_CALL hangup failed: %s", e)
                                asyncio.create_task(
                                    _delayed_hangup(call_id, total_duration)
                                )
                            continue
                        audio = out.get("audio")
                        if not audio:
                            continue
                        dur = await _send_audio_response(
                            websocket, state, audio,
                            buffer_size=agent.tts_buffer_size or 0,
                            auto_reset=False,
                        )
                        total_duration += dur
                except Exception as e:
                    logger.warning("streaming turn failed: %s", e)

                # Schedule one reset after the full stream finishes playing
                if total_duration > 0:
                    asyncio.create_task(
                        _reset_speaking_flag(state, total_duration)
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
            watchdog_task.cancel()
        except Exception:
            pass
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
    elif call and call.call_type == "ai_campaign":
        # Unanswered/short campaign calls: no transcript but still need to
        # update campaign_lead status. Without this, unanswered calls stay
        # in "calling" forever because _save_summary_background never runs.
        background_tasks.add_task(
            _handle_campaign_call_ended,
            call_id=call_id,
            call_duration=call.call_duration_seconds or 0,
        )

    if state:
        call_state_manager.remove(call_id)

    return Response(
        content="<?xml version='1.0'?><Response/>",
        media_type="application/xml",
    )


# ─────────────────────────────────────────────
# POST /voice/call/{call_id}/end  (frontend end-call button)
# ─────────────────────────────────────────────


@router.post("/call/{call_id}/end")
async def end_call(
    call_id: str,
    current_user: Profile = Depends(get_current_user),
    company_id = Depends(get_current_company_id),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
):
    """End an active call from the frontend UI.

    Triggers Plivo hangup, saves transcript/duration, and kicks off
    summary generation — same post-call flow as the Plivo /hangup webhook
    but initiated by the user instead of Plivo.
    """
    # Verify call belongs to this company
    result = await db.execute(
        select(CallAttempt).where(
            CallAttempt.id == uuid.UUID(call_id),
            CallAttempt.company_id == company_id,
        )
    )
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if call.call_status == "ended":
        return {"success": True, "message": "Call already ended", "call_id": call_id}

    # Hang up via Plivo
    hung_up = await plivo_handler.hangup_call(call_id)

    # Save transcript and duration from in-memory state
    state = call_state_manager.get(call_id)
    call.call_status = "ended"
    call.ended_at = datetime.utcnow()
    if state:
        call.transcript = state.get_full_transcript()
        call.call_duration_seconds = state.get_duration_seconds()
    await db.commit()

    # Generate summary in background
    if state and state.transcript_segments:
        background_tasks.add_task(
            _save_summary_background,
            call_id=call_id,
            transcript=state.get_full_transcript(),
        )

    if state:
        call_state_manager.remove(call_id)

    return {
        "success": True,
        "message": "Call ended" if hung_up else "Call ended (Plivo hangup may have already occurred)",
        "call_id": call_id,
        "duration": call.call_duration_seconds,
    }


async def _handle_campaign_call_ended(call_id: str, call_duration: int):
    """Update campaign_lead for unanswered/short calls with no transcript."""
    try:
        from app.workers.campaign_worker import campaign_worker
        success = call_duration > 10
        await campaign_worker.handle_call_completed(call_id, success)
    except Exception as e:
        logger.warning("campaign call ended handler failed: %s", e)


async def _save_summary_background(call_id: str, transcript: str):
    """Post-call automation — runs as background task after hangup.

    Single pipeline: LLM analysis → update call → update lead → stage
    transition → notifications → follow-up task → campaign update.
    """
    from app.models.lead import Lead
    from app.models.task import Task
    from app.models.notification import Notification
    from app.services.pricing_service import calculate_agent_pricing
    from app.utils.date_helpers import now_utc, add_business_days

    # Look up the agent's custom analysis prompt (per-agent in DB) before
    # calling the LLM so the analysis is shaped by the agent's vertical.
    # Falls back to the generic prompt inside _analyze_call if NULL.
    agent: Optional[AIAgent] = None
    analysis_prompt: Optional[str] = None
    try:
        async with AsyncSessionLocal() as _prompt_db:
            _call_result = await _prompt_db.execute(
                select(CallAttempt).where(CallAttempt.id == uuid.UUID(call_id))
            )
            _call = _call_result.scalar_one_or_none()
            if _call and _call.ai_agent_id:
                _agent_result = await _prompt_db.execute(
                    select(AIAgent).where(AIAgent.id == _call.ai_agent_id)
                )
                agent = _agent_result.scalar_one_or_none()
                if agent:
                    analysis_prompt = getattr(agent, "post_call_analysis_prompt", None)
    except Exception as e:
        logger.warning(
            "post_call: prompt lookup failed for call %s: %s — using generic",
            call_id, e,
        )

    post_call = await _analyze_call(transcript, system_prompt=analysis_prompt)
    sentiment = post_call.get("sentiment", "neutral")
    interest = post_call.get("interest_level", "low")
    summary = post_call.get("summary", "")
    learned_name = post_call.get("user_name")  # may be None

    # Brand-specific extraction is everything OUTSIDE the core fields. We
    # don't know in advance what an Admitverse / future-tenant prompt
    # asks for, so just pass through all extra keys the LLM returned.
    # Notes block + custom_fields render dynamically below.
    extracted: dict = {
        k: v for k, v in post_call.items()
        if k not in _CORE_ANALYSIS_FIELDS and v not in (None, "", [], {})
    }
    # Always carry through objections / next_action even though they're
    # core (the previous behavior shipped them in custom_fields too).
    if post_call.get("objections"):
        extracted["objections"] = post_call["objections"]
    if post_call.get("next_action"):
        extracted["next_action"] = post_call["next_action"]

    try:
        async with AsyncSessionLocal() as db:
            # ── Load call ──
            result = await db.execute(
                select(CallAttempt).where(CallAttempt.id == uuid.UUID(call_id))
            )
            call = result.scalar_one_or_none()
            if not call:
                return

            # ── Load lead ──
            lead_result = await db.execute(select(Lead).where(Lead.id == call.lead_id))
            lead = lead_result.scalar_one_or_none()

            # ── Update call: summary + sentiment + cost ──
            call.summary = summary
            call.sentiment = sentiment
            call.sentiment_score = post_call.get("confidence", 0) / 100.0

            if call.call_duration_seconds and call.ai_agent_id and agent:
                try:
                    pricing = calculate_agent_pricing(agent)
                    call.cost = round(pricing["total_usd"] * call.call_duration_seconds / 60.0, 4)
                except Exception as e:
                    logger.warning("cost calc failed for call %s: %s", call_id, e)

            # ── Update lead tracking fields ──
            if lead:
                lead.call_attempt_count = Lead.call_attempt_count + 1
                lead.last_contacted_at = now_utc()
                if summary:
                    ts = now_utc().strftime("%Y-%m-%d %H:%M")
                    # Render whatever extra fields the LLM returned. Field
                    # labels are derived from the snake_case key — this
                    # makes the notes block agnostic to the agent's
                    # vertical (FMC ships loan_amount/banks_tried,
                    # Admitverse ships target_university/intake/etc.).
                    details: list[str] = []
                    for key, value in extracted.items():
                        if value in (None, "", [], {}):
                            continue
                        label = key.replace("_", " ").title()
                        if isinstance(value, list):
                            value_str = ", ".join(str(v) for v in value)
                        else:
                            value_str = str(value)
                        details.append(f"  {label}: {value_str}")
                    details_block = ("\n" + "\n".join(details)) if details else ""

                    entry = (
                        f"\n\n--- AI Call ({ts}) ---\n"
                        f"{summary}\n"
                        f"Sentiment: {sentiment} | Interest: {interest}"
                        f"{details_block}"
                    )
                    lead.notes = (lead.notes or "") + entry

                # Persist the structured extraction into lead.custom_fields
                # under "ai_last_call" so the FE can render it as a summary
                # widget. Overwrites previous call's snapshot — full history
                # remains in lead.notes (append-only).
                custom = dict(lead.custom_fields or {})
                custom["ai_last_call"] = {
                    "at": now_utc().isoformat(),
                    "call_id": str(call.id),
                    "sentiment": sentiment,
                    "interest_level": interest,
                    **extracted,
                }
                lead.custom_fields = custom

                # Save the user's name back to the lead record if we learned
                # one and didn't have one before. Next time we call this lead,
                # the welcome audio will use their real name instead of asking.
                # We do NOT overwrite a name that already exists — humans set
                # those deliberately and the LLM extraction can be wrong.
                existing = (lead.full_name or "").strip()
                # Same set of placeholders we treat as 'no name' across the
                # voice pipeline. Keep these in sync with _NAME_PLACEHOLDERS
                # in llm_service.py and _is_real_name in pipeline.py.
                existing_is_placeholder = (not existing) or existing.lower() in (
                    "", "unknown", "no name", "n/a", "lead", "user",
                    "there", "you", "sir", "ma'am", "madam",
                )
                if learned_name and existing_is_placeholder:
                    logger.info(
                        "POST-CALL learned lead name '%s' for lead %s (was %r)",
                        learned_name, lead.id, lead.full_name,
                    )
                    lead.full_name = learned_name

            await db.commit()

            # ── Lead stage auto-update ──
            # Skip if the lead is already in a terminal state. FMC's
            # post-revamp terminals are "disbursed" + "lost"; Admitverse
            # is "enrolled" + "lost"; legacy "won" stays in for any
            # pre-revamp leads still hanging around.
            if lead and lead.current_stage not in ("won", "lost", "disbursed", "enrolled"):
                try:
                    call_agent = call.agent_id or call.telecaller_id
                    await _auto_update_lead_stage(
                        db, call, lead, sentiment, interest, summary,
                        call_agent_id=call_agent,
                    )
                except Exception as e:
                    logger.warning("lead stage update failed (non-fatal): %s", e)
                    try:
                        await db.rollback()
                    except Exception:
                        pass

            # ── Activity log ──
            try:
                db.add(ActivityLog(
                    company_id=call.company_id,
                    actor_id=None,
                    action="call_ended",
                    entity_type="call",
                    entity_id=call.id,
                    new_values={
                        "sentiment": sentiment,
                        "interest_level": interest,
                        "duration": call.call_duration_seconds,
                        "cost_usd": call.cost,
                    },
                ))
                await db.commit()
            except Exception as e:
                logger.warning("activity log write failed: %s", e)

            # ── Campaign lead update ──
            if call.call_type == "ai_campaign":
                try:
                    from app.workers.campaign_worker import campaign_worker
                    success = bool(
                        call.call_status == "ended"
                        and call.call_duration_seconds
                        and call.call_duration_seconds > 10
                    )
                    await campaign_worker.handle_call_completed(call_id, success)
                except Exception as e:
                    logger.warning("campaign lead update failed: %s", e)

    except Exception as e:
        logger.error("post-call automation failed for %s: %s", call_id, e)


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


# Generic safe analysis prompt for any tenant whose agent has not set a
# custom post_call_analysis_prompt. Extracts only the brand-agnostic core
# fields so the notes/sentiment/UI flows still work without making any
# false assumptions about the vertical (no loan extraction, no admissions
# extraction). Brands that want richer extraction set their own prompt
# in ai_agents.post_call_analysis_prompt.
_GENERIC_ANALYSIS_PROMPT = (
    "You analyse phone call transcripts. Calls happen in Hinglish "
    "(Hindi+English). Focus on what the USER (the lead) said, NOT what "
    "the agent asked. Return ONLY valid JSON with these fields:\n\n"
    '- "summary": 3-5 sentences IN ENGLISH. Mention specific facts the '
    'user revealed. Avoid generic phrasing like "the user expressed '
    'interest" — quote specifics. If the call was very short or the user '
    'said almost nothing, write a one-line summary stating that.\n'
    '- "sentiment": "positive" if user clearly engaged, asked questions, '
    'or agreed to next steps. "negative" if user declined, asked not to '
    'be called, or was hostile. "neutral" for short / inconclusive / '
    'unclear calls. Default to "neutral" in doubt.\n'
    '- "confidence": integer 0-100. Your confidence in the sentiment / '
    'interest assessment given the transcript length and clarity. '
    'For transcripts under 200 chars, max confidence is 40.\n'
    '- "interest_level": "high" if user EXPLICITLY engaged with concrete '
    'details and committed to next steps. "medium" if user shared '
    'concrete details but no commitment. "low" otherwise — including if '
    'user only said "hello"/"ok"/"haan"/"who is this". When in doubt, '
    'choose lower tier.\n'
    '- "user_name": the user\'s name if they explicitly said it. null '
    'otherwise. Do NOT use the agent\'s name.\n'
    '- "objections": array of specific concerns the user raised. Empty '
    'array if none.\n'
    '- "next_action": what was agreed at the end. null if no concrete '
    'action was agreed.\n\n'
    "No markdown, no explanation, just the JSON object."
)


# Core fields every analysis prompt is expected to return. Anything
# OUTSIDE this set (loan_amount, target_university, etc.) is treated as
# brand-specific extracted detail and rendered dynamically in the notes
# auto-append block.
_CORE_ANALYSIS_FIELDS = {
    "summary", "sentiment", "confidence", "interest_level", "user_name",
    "objections", "next_action",
}


async def _analyze_call(transcript: str, system_prompt: Optional[str] = None) -> dict:
    """Single LLM call: summary + sentiment + interest level + extras.

    The system_prompt is sourced from ai_agents.post_call_analysis_prompt
    (per-agent). If null/empty, falls back to _GENERIC_ANALYSIS_PROMPT —
    a brand-agnostic version that extracts only core fields and makes no
    assumptions about the vertical. This keeps Admitverse calls from
    being analysed as if they were FMC loan calls.

    Returns dict with at minimum: summary, sentiment, confidence,
    interest_level, plus whatever extra fields the prompt asked for.

    On failure, returns the empty default but with a marker summary so the
    UI can distinguish 'AI didn't run' from 'AI ran and had nothing to say'.
    Every failure path logs the actual root cause — the previous catch-all
    exception swallowed JSON parse errors, rate limits, and auth failures
    indistinguishably, leaving 'No summary available' in the dashboard with
    no breadcrumb trail.
    """
    import json as _json

    def _empty(reason: str) -> dict:
        # Marker prefix lets the FE detect 'AI failed' state without changing
        # the schema. Reason is kept short for log greppability.
        return {
            "summary": f"[AI summary unavailable: {reason}]",
            "sentiment": "neutral",
            "confidence": 0,
            "interest_level": "low",
        }

    if not transcript:
        return _empty("empty transcript")

    # Guard rail — Sarvam STT occasionally returns 1-3 char transcripts on
    # noisy / dropped calls. Asking the LLM to summarize "ok" produces noise.
    if len(transcript.strip()) < 20:
        logger.info("call analysis: transcript too short (%d chars), skipping LLM", len(transcript.strip()))
        return _empty("transcript too short")

    settings = get_settings()
    if not settings.openrouter_api_key:
        logger.error("call analysis: OPENROUTER_API_KEY not configured")
        return _empty("API key missing")

    try:
        client = get_openrouter_client()
        # Path MUST include /api/v1 — get_openrouter_client uses base_url
        # https://openrouter.ai (no /api/v1 prefix). Hitting just
        # /chat/completions returns the OpenRouter marketing site's HTML,
        # which is what was breaking every AI summary in production.
        response = await client.post(
            "/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (system_prompt or _GENERIC_ANALYSIS_PROMPT),
                    },
                    {"role": "user", "content": transcript},
                ],
                "max_tokens": 600,
                "temperature": 0.2,  # tighter — extraction, not creative writing
                "response_format": {"type": "json_object"},
            },
        )
    except httpx.TimeoutException as e:
        logger.error("call analysis: OpenRouter timeout: %s", e)
        return _empty("LLM timeout")
    except httpx.HTTPError as e:
        logger.error("call analysis: OpenRouter HTTP error: %s", e)
        return _empty("LLM network error")
    except Exception as e:
        # Truly unexpected — let it surface in Sentry but still degrade gracefully.
        logger.exception("call analysis: unexpected error calling OpenRouter: %s", e)
        return _empty("LLM unexpected error")

    # Distinguish HTTP-level failures (rate limit, auth, 5xx) from parse failures.
    if response.status_code != 200:
        body_preview = response.text[:300] if response.text else ""
        logger.error(
            "call analysis: OpenRouter returned %d for transcript len=%d. Body: %s",
            response.status_code, len(transcript), body_preview,
        )
        if response.status_code == 401:
            return _empty("LLM auth error")
        if response.status_code == 429:
            return _empty("LLM rate limited")
        if response.status_code >= 500:
            return _empty(f"LLM server error {response.status_code}")
        return _empty(f"LLM HTTP {response.status_code}")

    try:
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()
    except (ValueError, KeyError, IndexError) as e:
        logger.error(
            "call analysis: malformed OpenRouter response: %s | preview=%s",
            e, response.text[:300],
        )
        return _empty("LLM malformed response")

    if not text:
        logger.warning("call analysis: empty content in OpenRouter response")
        return _empty("LLM empty response")

    try:
        result = _json.loads(text)
    except _json.JSONDecodeError as e:
        # Some models wrap JSON in markdown despite response_format=json_object.
        # Try to extract once before giving up.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            result = _json.loads(cleaned)
        except _json.JSONDecodeError:
            logger.error(
                "call analysis: LLM returned non-JSON: %s | preview=%s",
                e, text[:300],
            )
            return _empty("LLM JSON parse error")

    # Validate + sanitize — wrong values silently coerced to safe defaults.
    if not isinstance(result, dict):
        logger.error("call analysis: LLM returned non-dict: %r", text[:200])
        return _empty("LLM bad shape")

    summary = (result.get("summary") or "").strip()
    if not summary:
        # The LLM returned valid JSON but no summary — still better than nothing,
        # log it so we can see if a particular model has this pattern.
        logger.warning("call analysis: LLM returned empty summary for transcript len=%d", len(transcript))
        result["summary"] = "[AI summary unavailable: LLM returned empty]"
    if result.get("sentiment") not in ("positive", "neutral", "negative"):
        result["sentiment"] = "neutral"
    if result.get("interest_level") not in ("high", "medium", "low"):
        result["interest_level"] = "low"

    confidence = result.get("confidence", 0)
    try:
        result["confidence"] = max(0, min(100, int(confidence)))
    except (TypeError, ValueError):
        result["confidence"] = 0

    # user_name normalisation: strip, title-case, sanity-check.
    # The LLM occasionally returns the agent's name ("Priya"), the literal
    # word "user", or a fragment like "speaking" — filter those out.
    raw_name = result.get("user_name")
    if isinstance(raw_name, str):
        cleaned = raw_name.strip()
        bad = {
            "", "null", "none", "user", "agent", "priya", "speaking",
            "the user", "the lead", "n/a", "unknown",
        }
        if 2 <= len(cleaned) <= 60 and cleaned.lower() not in bad:
            result["user_name"] = cleaned.title()
        else:
            result["user_name"] = None
    else:
        result["user_name"] = None

    # Helper: clean up free-text string fields. The LLM sometimes returns
    # "null" / "none" / "N/A" as actual strings; coerce them to None.
    def _clean_text(value, max_len=200):
        if not isinstance(value, str):
            return None
        v = value.strip()
        if not v or v.lower() in {"null", "none", "n/a", "na", "unknown", "not mentioned"}:
            return None
        return v[:max_len]

    result["loan_amount"] = _clean_text(result.get("loan_amount"), 60)
    result["college"] = _clean_text(result.get("college"), 120)
    result["course"] = _clean_text(result.get("course"), 60)
    result["intake"] = _clean_text(result.get("intake"), 60)
    result["next_action"] = _clean_text(result.get("next_action"), 200)

    # study_location: enum
    sl = result.get("study_location")
    if isinstance(sl, str) and sl.strip().lower() in {"india", "abroad"}:
        result["study_location"] = sl.strip().lower()
    else:
        result["study_location"] = None

    # banks_tried: list of short strings
    banks = result.get("banks_tried")
    if isinstance(banks, list):
        result["banks_tried"] = [
            b.strip()[:30] for b in banks
            if isinstance(b, str) and b.strip() and b.strip().lower() not in {"null", "none"}
        ][:10]
    else:
        result["banks_tried"] = []

    # objections: list of short strings
    objs = result.get("objections")
    if isinstance(objs, list):
        result["objections"] = [
            o.strip()[:120] for o in objs
            if isinstance(o, str) and o.strip() and o.strip().lower() not in {"null", "none"}
        ][:10]
    else:
        result["objections"] = []

    return result


# Per-brand AI auto-stage paths.
#
# Admitverse uses linear stepping: CREATED → CONTACTED → CONNECTED →
# QUALIFIED, never moves backward, walks one step per call.
#
# FMC has its own logic in _fmc_auto_advance — DNP and 12-attempt-LOST
# don't fit a linear path. The "fmc" entry here is unused by the
# Plivo path post-2026-05 but kept for the call_attempts.py / Bolna
# webhook code that may still reference it.
_STAGE_PATHS = {
    "fmc": ["created", "contacted", "qualified"],
    "admitverse": ["created", "contacted", "connected", "qualified"],
}
_STAGE_ORDERS = {
    brand: {stage: idx for idx, stage in enumerate(path)}
    for brand, path in _STAGE_PATHS.items()
}

# FMC auto-advance config — externalized so the DNP-LOST threshold is
# easy to find and tune.
_FMC_AUTO_ADVANCE_STAGES = frozenset({"created", "contacted", "dnp"})
_FMC_DNP_LOST_THRESHOLD = 12


async def _fmc_auto_advance(
    db, call, lead, sentiment: str, interest_level: str,
    call_summary: str, call_agent_id, call_connected: bool,
):
    """FMC-specific AI auto-stage logic for the loan-processing pipeline.

    Auto-advance only covers the qualifying portion (Created → Contacted
    → Qualified). Anything past Qualified — Processing, Docs Pending,
    Logged In, Sanctioned, PF Paid, Disbursed — is human-driven loan
    paperwork, not something an AI call should mutate.

    Decision matrix (one transition per call, no stepping):

      no-pickup at CREATED/CONTACTED   → DNP
      no-pickup at DNP, attempts ≥ 12  → LOST  (auto-churn)
      positive at CREATED              → CONTACTED
      positive at DNP                  → CONTACTED  (re-engaged)
      positive + high interest + long
        transcript at CONTACTED        → QUALIFIED
      anything else                    → no-op
    """
    from app.utils.date_helpers import now_utc, add_business_days
    from app.models.notification import Notification

    old_stage = lead.current_stage
    if old_stage not in _FMC_AUTO_ADVANCE_STAGES:
        return

    target: str | None = None

    if not call_connected:
        attempt_count = lead.call_attempt_count or 0
        if old_stage == "dnp" and attempt_count >= _FMC_DNP_LOST_THRESHOLD:
            target = "lost"
        else:
            target = "dnp"
    elif sentiment == "positive":
        if old_stage == "created":
            target = "contacted"
        elif old_stage == "dnp":
            target = "contacted"
        elif old_stage == "contacted":
            transcript = call.transcript or ""
            qualified_eligible = (
                interest_level == "high"
                and len(transcript) >= 500
                and transcript.count("User:") >= 3
            )
            if qualified_eligible:
                target = "qualified"

    if not target or target == old_stage:
        return

    # Resolve owner for the LeadStageLog row. Same fallback chain as
    # the Admitverse path: explicit call_agent_id → assigned agent →
    # creator → any active admin/manager in the company.
    changed_by = call_agent_id or lead.assigned_agent_id or lead.created_by
    if not changed_by:
        from app.models.profile import Profile
        from app.core.constants import UserRole
        fb = (await db.execute(
            select(Profile).where(
                Profile.company_id == lead.company_id,
                Profile.role.in_([UserRole.ADMIN, UserRole.MANAGER]),
                Profile.is_active == True,  # noqa: E712
            ).limit(1)
        )).scalar_one_or_none()
        if fb:
            changed_by = fb.id
        else:
            logger.warning(
                "FMC_AUTO_UPDATE skipped — no owner / admin fallback for lead %s",
                lead.id,
            )
            return

    company_id = lead.company_id
    lead.current_stage = target

    if target == "contacted" and not lead.connected_time:
        lead.connected_time = now_utc()
    if target == "qualified":
        lead.due_date = add_business_days(now_utc(), 1)
    if target == "lost":
        lead.lost_time = now_utc()
        if not lead.lost_reason:
            lead.lost_reason = (
                f"Auto-lost: {_FMC_DNP_LOST_THRESHOLD} unanswered AI attempts"
            )

    db.add(LeadStageLog(
        lead_id=lead.id,
        company_id=company_id,
        from_stage=old_stage,
        to_stage=target,
        changed_by=changed_by,
        conversation_notes=(
            f"Auto: AI call. Sentiment={sentiment}, Interest={interest_level}"
        ),
    ))

    db.add(ActivityLog(
        company_id=company_id,
        actor_id=None,
        action="stage_changed",
        entity_type="lead",
        entity_id=lead.id,
        new_values={
            "from": old_stage, "to": target,
            "sentiment": sentiment, "interest": interest_level,
            "call_id": str(call.id),
        },
    ))

    notify_user = lead.assigned_agent_id or changed_by
    if notify_user:
        db.add(Notification(
            company_id=company_id,
            user_id=notify_user,
            type="stage_changed",
            title=f"Lead moved: {old_stage} → {target}",
            message=f"{lead.full_name} auto-updated after AI call. Sentiment: {sentiment}.",
            lead_id=lead.id,
        ))

    if target == "qualified":
        assignee = lead.assigned_agent_id or changed_by
        db.add(Task(
            company_id=company_id,
            lead_id=lead.id,
            assigned_to=assignee,
            created_by=assignee,
            task_type="follow_up",
            title=f"Follow up: {lead.full_name} — Qualified Lead",
            description=(
                f"AI call showed high interest. Summary: "
                f"{call_summary[:300] if call_summary else 'N/A'}"
            ),
            status="pending",
            due_date=add_business_days(now_utc(), 1),
        ))
        db.add(Notification(
            company_id=company_id,
            user_id=assignee,
            type="task_created",
            title=f"Follow-up task: {lead.full_name}",
            message="Auto-created for qualified lead after AI call.",
            lead_id=lead.id,
        ))

    # Drop stale callback tasks now that the stage moved on.
    from app.services.stage_machine import auto_complete_stale_call_tasks
    await auto_complete_stale_call_tasks(
        db, lead_id=lead.id, company_id=company_id, new_stage=target,
    )

    await db.commit()

    logger.info(
        "FMC_AUTO_UPDATE lead=%s %s→%s sentiment=%s interest=%s call=%s",
        str(lead.id)[:8], old_stage, target, sentiment, interest_level,
        str(call.id)[:8],
    )


async def _auto_update_lead_stage(
    db, call, lead, sentiment: str, interest_level: str,
    call_summary: str = "", call_agent_id=None,
):
    """Auto-update lead stage based on call outcome.

    Brand-aware:
      Admitverse walks CREATED → CONTACTED → CONNECTED → QUALIFIED, never
      moves backward, always steps one stage at a time.

      FMC delegates to _fmc_auto_advance, which handles the new
      loan-processing pipeline (DNP / 12-attempt LOST / re-engage).
      The old linear-path logic below would silently no-op on FMC
      after the May 2026 pipeline revamp because old stage names
      (lead/called/qualified_lead) no longer exist on FMC leads.
    """
    from app.models.lead import Lead
    from app.models.company import Company
    from app.models.task import Task
    from app.models.notification import Notification
    from app.utils.date_helpers import now_utc, add_business_days

    # Resolve brand from the lead's company so each call uses the right
    # stage names. Unknown / missing slug falls back to the FMC path.
    slug_result = await db.execute(select(Company.slug).where(Company.id == lead.company_id))
    slug = (slug_result.scalar_one_or_none() or "").lower()
    brand = "admitverse" if slug == "admitverse" else "fmc"

    old_stage = lead.current_stage
    # A "connected" call needs an actual conversation, not just a brief
    # pickup-and-hangup. Without the transcript guard, every silent
    # connect (duration > 0 but the user said nothing) was treated as
    # connected and fell through to a no-op — leaving the lead frozen at
    # "created" instead of moving to DNP. 20 chars ≈ 4-5 words, the
    # minimum to call it a real conversation versus background noise
    # or a quick "hello" before the line drops.
    transcript_text = call.transcript or ""
    call_connected = bool(
        call.call_duration_seconds and call.call_duration_seconds > 0
        and call.call_status == "ended"
        and len(transcript_text) >= 20
    )

    # FMC uses dedicated handler — new pipeline doesn't fit the linear
    # path-walking model below.
    if brand == "fmc":
        await _fmc_auto_advance(
            db, call, lead, sentiment, interest_level,
            call_summary, call_agent_id, call_connected,
        )
        return

    stage_path = _STAGE_PATHS[brand]
    stage_order = _STAGE_ORDERS[brand]
    contacted_stage, connected_stage, qualified_stage = stage_path[1], stage_path[2], stage_path[3]

    # Evidence threshold for "qualified_lead":
    # the LLM hallucinates "high interest" on transcripts where the user
    # only said "Can you speak?" — because the agent's intro line mentions
    # education loans. We block that by requiring real engagement: a
    # substantial transcript AND multiple user turns. Without these
    # gates, every connected call with the agent's standard opening got
    # qualified, regardless of what the user actually said.
    transcript = call.transcript or ""
    transcript_len = len(transcript)
    user_turns = transcript.count("User:")
    qualified_eligible = (
        sentiment == "positive"
        and interest_level == "high"
        and transcript_len >= 500
        and user_turns >= 3
    )

    # Determine target stage
    if not call_connected:
        # No answer — at minimum mark as contacted (we tried)
        target = contacted_stage
    elif qualified_eligible:
        target = qualified_stage
    elif sentiment == "positive" or call_connected:
        target = connected_stage
    else:
        target = contacted_stage

    # Never move backward
    if stage_order.get(target, 0) <= stage_order.get(old_stage, 0):
        return

    start_idx = stage_path.index(old_stage) if old_stage in stage_path else -1
    end_idx = stage_path.index(target) if target in stage_path else -1
    if start_idx < 0 or end_idx < 0 or end_idx <= start_idx:
        return

    changed_by = call_agent_id or lead.assigned_agent_id or lead.created_by
    if not changed_by:
        # Lead has no owner on file (CSV import without created_by, agent
        # deactivated since assignment, etc). Fall back to any active
        # admin / manager in the company so the auto-stage update isn't
        # silently dropped — previously 17% of leads sat in 'lead' forever
        # because of this skip.
        from app.models.profile import Profile
        from app.core.constants import UserRole
        fb_result = await db.execute(
            select(Profile)
            .where(
                Profile.company_id == lead.company_id,
                Profile.role.in_([UserRole.ADMIN, UserRole.MANAGER]),
                Profile.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        fb = fb_result.scalar_one_or_none()
        if fb:
            changed_by = fb.id
        else:
            logger.warning(
                "LEAD_AUTO_UPDATE skipped — no owner and no admin/manager "
                "fallback for lead %s in company %s",
                lead.id, lead.company_id,
            )
            return

    company_id = lead.company_id
    final_stage = old_stage

    for i in range(start_idx + 1, end_idx + 1):
        from_stage = stage_path[i - 1]
        to_stage = stage_path[i]

        lead.current_stage = to_stage
        final_stage = to_stage

        # Set timestamps
        if to_stage == connected_stage:
            lead.connected_time = now_utc()
        if to_stage == qualified_stage:
            lead.due_date = add_business_days(now_utc(), 1)

        # Stage log for each step
        db.add(LeadStageLog(
            lead_id=lead.id,
            company_id=company_id,
            from_stage=from_stage,
            to_stage=to_stage,
            changed_by=changed_by,
            conversation_notes=(
                f"Auto: AI call. Sentiment={sentiment}, Interest={interest_level}"
            ),
        ))

    # Activity log (one entry for the full transition)
    db.add(ActivityLog(
        company_id=company_id,
        actor_id=None,
        action="stage_changed",
        entity_type="lead",
        entity_id=lead.id,
        new_values={
            "from": old_stage, "to": final_stage,
            "sentiment": sentiment, "interest": interest_level,
            "call_id": str(call.id),
        },
    ))

    # Notification to assigned agent
    notify_user = lead.assigned_agent_id or changed_by
    if notify_user:
        db.add(Notification(
            company_id=company_id,
            user_id=notify_user,
            type="stage_changed",
            title=f"Lead moved: {old_stage} → {final_stage}",
            message=f"{lead.full_name} auto-updated after AI call. Sentiment: {sentiment}.",
            lead_id=lead.id,
        ))

    # Auto-create follow-up task for qualified leads
    if final_stage == qualified_stage:
        assignee = lead.assigned_agent_id or changed_by
        db.add(Task(
            company_id=company_id,
            lead_id=lead.id,
            assigned_to=assignee,
            created_by=assignee,
            task_type="follow_up",
            title=f"Follow up: {lead.full_name} — Qualified Lead",
            description=f"AI call showed high interest. Summary: {call_summary[:300] if call_summary else 'N/A'}",
            status="pending",
            due_date=add_business_days(now_utc(), 1),
        ))
        db.add(Notification(
            company_id=company_id,
            user_id=assignee,
            type="task_created",
            title=f"Follow-up task: {lead.full_name}",
            message="Auto-created for qualified lead after AI call.",
            lead_id=lead.id,
        ))

    # Auto-complete stale callback tasks. AI auto-stage advancement means
    # the lead moved forward; previous overdue callback tasks are no
    # longer relevant.
    if final_stage != old_stage:
        from app.services.stage_machine import auto_complete_stale_call_tasks
        await auto_complete_stale_call_tasks(
            db,
            lead_id=lead.id,
            company_id=company_id,
            new_stage=final_stage,
        )

    await db.commit()

    logger.info(
        "LEAD_AUTO_UPDATE lead=%s %s→%s sentiment=%s interest=%s call=%s",
        str(lead.id)[:8], old_stage, final_stage, sentiment, interest_level, str(call.id)[:8],
    )
