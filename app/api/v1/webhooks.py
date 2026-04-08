import logging
import uuid
from fastapi import APIRouter, Request, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.services.meta_webhook_service import MetaWebhookService
from app.services.bolna_service import bolna_service
from app.utils.hmac_verify import verify_meta_signature
from app.utils.date_helpers import now_utc
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ── Meta Lead Ads Webhooks (existing) ─────────────────────────────

@router.get("/meta")
async def verify_meta_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta webhook verification endpoint."""
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        logger.info("Meta webhook verified")
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Verification failed", status_code=403)


@router.post("/meta")
async def receive_meta_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Meta Lead Ads webhook. Responds 200 immediately, processes in background."""
    settings = get_settings()
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if settings.meta_app_secret:
        if not verify_meta_signature(body, signature, settings.meta_app_secret):
            logger.warning("Invalid Meta webhook signature")
            return PlainTextResponse("Invalid signature", status_code=403)

    data = await request.json()
    background_tasks.add_task(_process_meta_webhook, data)
    return {"status": "ok"}


async def _process_meta_webhook(data: dict):
    """Background task to process Meta webhook."""
    async with AsyncSessionLocal() as db:
        try:
            service = MetaWebhookService(db)
            await service.process_webhook(data)
        except Exception:
            logger.exception("Error processing Meta webhook")


# ── Bolna AI Webhooks ─────────────────────────────────────────────

@router.post("/bolna")
async def receive_bolna_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive Bolna AI call events. Always returns 200 to prevent retries."""
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Bolna-Signature", "")
    if bolna_service.webhook_secret and signature:
        if not bolna_service.verify_webhook_signature(body, signature):
            logger.warning("[WEBHOOK] Invalid Bolna signature")
            return JSONResponse({"status": "invalid_signature"}, status_code=401)

    try:
        data = await request.json()
    except Exception:
        logger.error("[WEBHOOK] Invalid JSON from Bolna")
        return {"status": "ok"}

    event_type = data.get("event") or data.get("type") or "unknown"
    metadata = data.get("metadata", {})
    call_id = metadata.get("call_id")
    company_id = metadata.get("company_id")

    logger.info("[WEBHOOK] Bolna event: %s, call_id=%s", event_type, call_id)

    if call_id and company_id:
        background_tasks.add_task(
            _process_bolna_event, event_type, data, call_id, company_id
        )

    # Always 200 to Bolna
    return {"status": "ok"}


async def _process_bolna_event(
    event_type: str, data: dict, call_id: str, company_id: str,
):
    """Background task to process a Bolna webhook event."""
    from app.services.call_service import CallService

    async with AsyncSessionLocal() as db:
        try:
            cid = uuid.UUID(company_id)
            service = CallService(db, cid)
            uid = uuid.UUID(call_id)

            if event_type in ("call_initiated", "call.initiated"):
                await service.update_call_status(uid, {"call_status": "initiated"})
                logger.info("[WEBHOOK] Call %s → initiated", call_id)

            elif event_type in ("call_connected", "call.connected"):
                await service.update_call_status(uid, {
                    "call_status": "connected",
                    "started_at": now_utc(),
                })
                logger.info("[WEBHOOK] Call %s → connected", call_id)

            elif event_type in ("call_ended", "call.ended", "call_hangup", "call.hangup"):
                ended_at = now_utc()
                update_data = {
                    "call_status": "ended",
                    "ended_at": ended_at,
                }
                call = await service._get_call(uid)
                if call.started_at:
                    duration = int((ended_at - call.started_at).total_seconds())
                    update_data["call_duration_seconds"] = duration

                cost = data.get("cost") or data.get("data", {}).get("cost")
                if cost is not None:
                    update_data["cost"] = float(cost)

                await service.update_call_status(uid, update_data)
                logger.info("[WEBHOOK] Call %s → ended (duration=%ss)", call_id, update_data.get("call_duration_seconds"))

                # Trigger post-call pipeline
                from app.services.post_call_service import post_call_pipeline
                await post_call_pipeline(db, uid, cid)

            elif event_type in ("transcript_ready", "transcript.ready"):
                transcript = data.get("transcript") or data.get("data", {}).get("transcript", "")
                if transcript:
                    await service.save_call_post_data(uid, {"transcript": transcript})
                    logger.info("[WEBHOOK] Call %s → transcript saved (%d chars)", call_id, len(transcript))

            elif event_type in ("recording_ready", "recording.ready"):
                url = data.get("recording_url") or data.get("data", {}).get("recording_url", "")
                if url:
                    await service.save_call_post_data(uid, {"call_recording_url": url})
                    logger.info("[WEBHOOK] Call %s → recording saved", call_id)

            elif event_type in ("call_failed", "call.failed"):
                await service.update_call_status(uid, {"call_status": "failed"})
                error = data.get("error") or data.get("data", {}).get("error", "unknown")
                logger.error("[WEBHOOK] Call %s → FAILED: %s", call_id, error)

            else:
                logger.warning("[WEBHOOK] Unknown Bolna event: %s", event_type)

        except Exception:
            logger.exception("[WEBHOOK] Error processing Bolna event %s for call %s", event_type, call_id)
