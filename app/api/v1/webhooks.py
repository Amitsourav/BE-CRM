from __future__ import annotations

import logging
import uuid
from fastapi import APIRouter, Request, Query, BackgroundTasks, Header, Depends, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal, get_db
from app.services.meta_webhook_service import MetaWebhookService
from app.services.bolna_service import bolna_service
from app.utils.hmac_verify import verify_meta_signature
from app.utils.date_helpers import now_utc
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
internal_router = APIRouter(prefix="/internal", tags=["Internal"])


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
    """Receive Meta Lead Ads webhook. Persists every entry to the
    meta_webhook_events queue before returning 200 — durable against
    AV outages, restart during processing, and Meta's 36h retry window.

    Signature check is mandatory in production. APP_ENV=development
    accepts unsigned payloads for local testing.
    """
    settings = get_settings()
    body = await request.body()

    # Signature check — REQUIRED in production. Without this, anyone
    # who knows the webhook URL can inject leads.
    signature = request.headers.get("X-Hub-Signature-256", "")
    if settings.meta_app_secret:
        if not verify_meta_signature(body, signature, settings.meta_app_secret):
            logger.warning("Invalid Meta webhook signature")
            return PlainTextResponse("Invalid signature", status_code=403)
    else:
        # No secret configured. Acceptable only in development; refuse
        # in production so a misconfigured env var doesn't silently
        # turn off signature verification.
        if settings.app_env == "production":
            logger.error("META_APP_SECRET missing in production — refusing webhook")
            return PlainTextResponse("Server misconfigured", status_code=503)
        logger.warning(
            "META_APP_SECRET not set (env=%s) — accepting unsigned payload",
            settings.app_env,
        )

    try:
        data = await request.json()
    except Exception:
        logger.warning("Meta webhook: invalid JSON body")
        return {"status": "ok"}  # always 200 so Meta doesn't retry malformed

    # Persist every leadgen change to the queue. Background worker
    # processes them. We return 200 fast so Meta is happy + we never
    # lose a lead even if downstream (AV, Graph API) is down.
    background_tasks.add_task(_enqueue_meta_events, data)
    return {"status": "ok"}


async def _enqueue_meta_events(data: dict):
    """Walk the webhook payload, extract each leadgen change, and insert
    a meta_webhook_events row per change. UNIQUE index on leadgen_id
    handles Meta retries: second insert no-ops via ON CONFLICT.
    """
    from app.models.meta_webhook_event import MetaWebhookEvent
    from app.models.meta_form_routing import MetaFormRouting
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with AsyncSessionLocal() as db:
        try:
            entries = data.get("entry", []) or []
            for entry in entries:
                page_id = str(entry.get("id", "")) if entry.get("id") is not None else None
                for change in entry.get("changes", []) or []:
                    if change.get("field") != "leadgen":
                        continue
                    value = change.get("value") or {}
                    leadgen_id = value.get("leadgen_id")
                    form_id = value.get("form_id")
                    if not leadgen_id:
                        continue

                    # Pre-resolve routing target so admin can see at a
                    # glance whether the row will go to FMC/AV/dropped.
                    target = None
                    source_id = None
                    if form_id:
                        routing = (await db.execute(
                            sa_select(MetaFormRouting).where(MetaFormRouting.form_id == str(form_id))
                        )).scalar_one_or_none()
                        if routing:
                            target = routing.target
                            source_id = routing.source_id

                    stmt = pg_insert(MetaWebhookEvent).values(
                        leadgen_id=str(leadgen_id),
                        form_id=str(form_id) if form_id else None,
                        page_id=str(value.get("page_id") or page_id) if value.get("page_id") or page_id else None,
                        raw_payload={"entry": entry, "change": change},
                        target=target,
                        source_id=source_id,
                        status="pending",
                    )
                    # UNIQUE(leadgen_id) WHERE leadgen_id IS NOT NULL —
                    # Meta retries can't double-enqueue. The partial
                    # index needs the same predicate in the ON CONFLICT
                    # clause to match.
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=["leadgen_id"],
                        index_where=MetaWebhookEvent.leadgen_id.isnot(None),
                    )
                    await db.execute(stmt)
            await db.commit()
            logger.info("Meta: enqueued events from payload (entries=%d)", len(entries))
        except Exception:
            logger.exception("Meta: failed to enqueue webhook events")
            await db.rollback()


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


# ── Internal: cross-backend Meta ingest ───────────────────────────────
#
# FMC backend acts as the Meta webhook gateway. When the routing table
# says a form belongs to AV, FMC POSTs the parsed lead here. Authorized
# only via the shared INTERNAL_META_SECRET — not exposed to the public
# beyond the secret check.

class _InternalMetaIngest(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    form_id: str
    leadgen_id: str
    source_id: str | None = None
    extra_fields: dict = {}


@internal_router.post("/meta/ingest")
async def internal_meta_ingest(
    body: _InternalMetaIngest,
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Receive a Meta lead forwarded from the FMC gateway. Only callable
    with the shared INTERNAL_META_SECRET header.
    """
    settings = get_settings()
    expected = settings.internal_meta_secret
    if not expected or x_internal_secret != expected:
        logger.warning("Internal meta ingest: bad or missing secret")
        raise HTTPException(status_code=403, detail="Forbidden")

    # Resolve company_id from source_id (the routing row's source lives
    # on the AV DB, scoped to the AV company). If no source_id provided,
    # fall back to the first admin's company on this DB.
    from app.models.lead_source import LeadSource
    from app.models.profile import Profile
    from app.services.lead_service import LeadService
    from app.utils.csv_parser import normalize_phone
    from app.models.lead import Lead

    company_id = None
    if body.source_id:
        try:
            sid = uuid.UUID(body.source_id)
        except Exception:
            sid = None
        if sid:
            row = (await db.execute(select(LeadSource.company_id).where(LeadSource.id == sid))).first()
            if row:
                company_id = row[0]
    if not company_id:
        # Last resort: any admin on this tenant
        admin = (await db.execute(select(Profile).where(Profile.role == "admin").limit(1))).scalar_one_or_none()
        if not admin:
            raise HTTPException(status_code=400, detail="No company resolvable")
        company_id = admin.company_id

    svc = LeadService(db, company_id)
    phone = normalize_phone(body.phone) if body.phone else None

    # Gap D — dedup on leadgen_id FIRST. FMC gateway might call us
    # twice (worker retry after a transient blip), and Meta itself
    # retries for 36h. Without this, retries create duplicates.
    leadgen_dup = (await db.execute(
        select(Lead.id).where(
            Lead.company_id == company_id,
            Lead.is_deleted == False,  # noqa: E712
            Lead.custom_fields["meta_leadgen_id"].astext == str(body.leadgen_id),
        )
    )).first()
    if leadgen_dup:
        return {"status": "duplicate", "leadgen_id": body.leadgen_id}

    if phone:
        exists = (await db.execute(
            select(Lead.id).where(
                Lead.company_id == company_id,
                Lead.phone == phone,
                Lead.is_deleted == False,  # noqa: E712
            )
        )).first()
        if exists:
            return {"status": "duplicate", "phone": phone}

    admin = (await db.execute(
        select(Profile).where(Profile.company_id == company_id, Profile.role == "admin").limit(1)
    )).scalar_one_or_none()
    creator_id = admin.id if admin else None

    sid = uuid.UUID(body.source_id) if body.source_id else None
    data = {
        "full_name": body.full_name,
        "email": body.email,
        "phone": phone,
        "city": body.city,
        "state": body.state,
        "lead_source_id": sid,
        "custom_fields": {
            "meta_leadgen_id": body.leadgen_id,
            "meta_form_id": body.form_id,
            **(body.extra_fields or {}),
        },
    }
    lead = await svc.create_lead(data, creator_id, creator_role=None)
    logger.info("Internal meta ingest: created lead %s (#%s) on tenant %s from form %s",
                lead.id, lead.serial_no, company_id, body.form_id)
    return {"status": "ok", "lead_id": str(lead.id), "serial_no": lead.serial_no}
