import logging
from fastapi import APIRouter, Request, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.services.meta_webhook_service import MetaWebhookService
from app.utils.hmac_verify import verify_meta_signature
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


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

    # Verify HMAC signature
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
