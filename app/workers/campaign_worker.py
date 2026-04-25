"""Campaign auto-dialer worker.

Runs every 30s via APScheduler. For each active campaign:
1. Check calling hours (daily window + weekends + date range)
2. Pick pending/retry-ready leads up to concurrency cap
3. Dispatch calls via Plivo (reuses the voice engine pipeline)
4. Track active calls and update campaign stats on completion

All DB access is async (matches the rest of the codebase).
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Optional

import pytz
from sqlalchemy import select, or_, and_, func, update as sql_update
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.ai_agent import AIAgent
from app.models.call_attempt import CallAttempt
from app.models.notification import Notification
from app.core.constants import NotificationType
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


class CampaignWorker:
    def __init__(self):
        self._running = False

    # ── Calling hours ──────────────────────────────────────

    @staticmethod
    def _in_calling_hours(campaign: Campaign) -> tuple[bool, str]:
        tz = pytz.timezone(campaign.timezone or "Asia/Kolkata")
        now = datetime.now(tz)
        current_time = now.time()
        day_name = now.strftime("%A")

        # Skip weekends
        if campaign.skip_weekends and now.weekday() >= 5:
            return False, f"weekend ({day_name})"

        # Daily time window
        start = campaign.daily_start_time
        end = campaign.daily_end_time
        if start and end:
            if not (start <= current_time <= end):
                return False, f"outside hours ({current_time.strftime('%H:%M')} not in {start}-{end})"

        # Campaign date range — compare tz-aware to tz-aware.
        # SQLAlchemy + asyncpg returns TIMESTAMPTZ columns as aware datetimes,
        # so no .replace(tzinfo=None) is needed.
        if campaign.start_date and now < campaign.start_date:
            return False, f"before start_date ({campaign.start_date})"
        if campaign.end_date and now > campaign.end_date:
            return False, f"after end_date ({campaign.end_date})"

        return True, "ok"

    # ── Active call tracking (DB-based, no in-memory counter) ──

    async def _active_count_db(self, db, campaign_id) -> int:
        """Count leads currently in 'calling' status from DB.

        Replaces the in-memory counter which got out of sync on deploys,
        exceptions, and missed hangup callbacks.
        """
        result = await db.execute(
            select(func.count()).where(
                CampaignLead.campaign_id == campaign_id,
                CampaignLead.status == "calling",
            )
        )
        return result.scalar() or 0

    # ── Main cycle ─────────────────────────────────────────

    async def run_cycle(self):
        """Single processing cycle — called by scheduler every 30s."""
        if self._running:
            return
        self._running = True
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Campaign)
                    .where(Campaign.status == "active")
                    .options(selectinload(Campaign.agent))
                )
                campaigns = result.scalars().all()
                if not campaigns:
                    logger.debug("CAMPAIGN_WORKER no active campaigns")
                    return

                logger.info("CAMPAIGN_WORKER found %d active campaigns", len(campaigns))
                for campaign in campaigns:
                    await self._process_campaign(db, campaign)

        except Exception as e:
            logger.error("CAMPAIGN_WORKER cycle error: %s", e)
        finally:
            self._running = False

    # ── Process one campaign ───────────────────────────────

    async def _process_campaign(self, db, campaign: Campaign):
        cid = str(campaign.id)
        short = cid[:8]

        in_hours, reason = self._in_calling_hours(campaign)
        if not in_hours:
            logger.info("[CAMPAIGN %s] Skipped: %s", short, reason)
            return

        # Recover stuck "calling" leads FIRST — before checking slots.
        # If a lead has been "calling" for >5 min, the call failed silently.
        now = now_utc()
        stuck_cutoff = now - timedelta(minutes=5)
        stuck_result = await db.execute(
            select(CampaignLead).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status == "calling",
                CampaignLead.last_attempt_at < stuck_cutoff,
            )
        )
        stuck = stuck_result.scalars().all()
        if stuck:
            for s in stuck:
                s.status = "failed"
                s.last_error = "Stuck in calling (recovered)"
                if s.attempt_count < campaign.max_retries:
                    s.next_retry_at = now + timedelta(minutes=1)
            await db.commit()
            logger.info("[CAMPAIGN %s] Recovered %d stuck leads", short, len(stuck))

        active = await self._active_count_db(db, campaign.id)
        slots = (campaign.max_concurrent_calls or 5) - active
        if slots <= 0:
            logger.info("[CAMPAIGN %s] No slots (calling=%d, max=%d)", short, active, campaign.max_concurrent_calls)
            return

        agent = campaign.agent
        if not agent or not getattr(agent, "is_active", True) or getattr(agent, "deleted_at", None) is not None:
            await self._pause_for_missing_agent(db, campaign, short)
            return

        if campaign.created_by is None:
            # agent_id on call_attempts is NOT NULL and must point at a real profile.
            # Without a creator we have no one to attribute the call to.
            await self._pause_for_missing_agent(
                db, campaign, short,
                message="Campaign has no creator — cannot dispatch calls. Reassign before resuming.",
                title="Campaign paused — no owner",
            )
            return

        # Pick leads: pending OR (failed + under retry limit + retry time passed)
        # Join to leads so soft-deleted / phone-less leads are skipped entirely.
        result = await db.execute(
            select(CampaignLead)
            .join(Lead, Lead.id == CampaignLead.lead_id)
            .where(
                CampaignLead.campaign_id == campaign.id,
                Lead.is_deleted == False,  # noqa: E712
                Lead.phone.isnot(None),
                or_(
                    CampaignLead.status == "pending",
                    and_(
                        CampaignLead.status == "failed",
                        CampaignLead.attempt_count < campaign.max_retries,
                        or_(
                            CampaignLead.next_retry_at == None,  # noqa: E711
                            CampaignLead.next_retry_at <= now,
                        ),
                    ),
                ),
            )
            .order_by(CampaignLead.priority.desc(), CampaignLead.created_at)
            .limit(slots)
        )
        leads_to_call = result.scalars().all()

        logger.info("[CAMPAIGN %s] slots=%d eligible_leads=%d", short, slots, len(leads_to_call))

        if not leads_to_call:
            await self._check_completion(db, campaign, short)
            return

        for cl in leads_to_call:
            await self._dispatch_call(db, campaign, cl, agent, short)
            await asyncio.sleep(0.3)

    # ── Dispatch a single call ─────────────────────────────

    async def _dispatch_call(self, db, campaign, cl: CampaignLead, agent: AIAgent, short: str):
        call_id: Optional[_uuid.UUID] = None
        committed_calling = False
        try:
            # Load lead
            result = await db.execute(select(Lead).where(Lead.id == cl.lead_id))
            lead = result.scalar_one_or_none()
            if not lead or not lead.phone or lead.is_deleted:
                cl.status = "failed"
                cl.last_error = "No phone number" if (lead and not lead.phone) else "Lead unavailable"
                await db.commit()
                return

            call_id = _uuid.uuid4()
            dispatched_at = now_utc()

            # Create CallAttempt — agent_id must be a real profile; campaign.created_by is
            # guaranteed non-null here (checked in _process_campaign).
            call = CallAttempt(
                id=call_id,
                company_id=campaign.company_id,
                lead_id=lead.id,
                agent_id=campaign.created_by,
                telecaller_id=campaign.created_by,
                ai_agent_id=agent.id,
                phone_number=lead.phone,
                attempt_number=cl.attempt_count + 1,
                disposition="connected",
                conversation_notes="",
                agent_agenda="",
                call_type="ai_campaign",
                call_status="initiated",
                started_at=dispatched_at,
            )
            db.add(call)

            # Update campaign lead + atomic counter bump (avoids read-modify-write drift).
            cl.status = "calling"
            cl.attempt_count += 1
            cl.last_attempt_at = dispatched_at
            cl.last_call_id = call_id
            await db.execute(
                sql_update(Campaign)
                .where(Campaign.id == campaign.id)
                .values(calls_made=Campaign.calls_made + 1)
            )
            await db.commit()
            committed_calling = True

            # Create in-memory call state for voice pipeline
            from app.services.voice_engine.call_state import call_state_manager
            try:
                state = call_state_manager.create(
                    call_id=str(call_id),
                    agent_id=str(agent.id),
                    lead_id=str(lead.id),
                    company_id=str(campaign.company_id),
                    lead_name=lead.full_name or "there",
                )
                state.agent = agent
            except RuntimeError:
                # Global concurrent cap hit — rewind
                cl.status = "pending"
                cl.attempt_count = max(0, cl.attempt_count - 1)
                cl.last_attempt_at = None
                cl.last_call_id = None
                await db.execute(
                    sql_update(Campaign)
                    .where(Campaign.id == campaign.id)
                    .values(calls_made=func.greatest(Campaign.calls_made - 1, 0))
                )
                await db.delete(call)
                await db.commit()
                return

            # Pre-gen welcome + warmups (same as outbound handler)
            asyncio.create_task(self._pregen_welcome(state, agent, lead))
            asyncio.create_task(self._warmup_llm(agent))
            asyncio.create_task(self._warmup_stt(agent))

            # Fire call via Plivo
            from app.services.voice_engine.plivo_handler import plivo_handler
            plivo_response = await plivo_handler.make_call(
                to_number=lead.phone,
                call_id=str(call_id),
                agent_id=str(agent.id),
                lead_id=str(lead.id),
                lead_name=lead.full_name or "there",
                from_number=getattr(agent, "phone_number", None) or "",
            )

            if not plivo_response.get("success"):
                call.call_status = "failed"
                cl.status = "failed"
                cl.last_error = plivo_response.get("error", "Plivo error")
                cl.last_call_status = "failed"
                if cl.attempt_count < campaign.max_retries:
                    cl.next_retry_at = dispatched_at + timedelta(hours=campaign.retry_gap_hours)
                await db.execute(
                    sql_update(Campaign)
                    .where(Campaign.id == campaign.id)
                    .values(calls_failed=Campaign.calls_failed + 1)
                )
                call_state_manager.remove(str(call_id))
                await db.commit()
                logger.warning(
                    "[CAMPAIGN %s] Call failed to %s: %s",
                    short, lead.phone, cl.last_error,
                )
                return

            call.external_call_id = plivo_response.get("plivo_call_uuid", "")
            call.call_status = "ringing"
            await db.commit()

            logger.info(
                "[CAMPAIGN %s] Call dispatched to %s (attempt %d/%d, call=%s)",
                short, lead.phone, cl.attempt_count, campaign.max_retries, str(call_id)[:8],
            )

        except Exception as e:
            logger.exception("[CAMPAIGN %s] Dispatch error: %s", short, e)
            await db.rollback()
            # If we already committed with status='calling', the rollback above
            # won't undo it. Explicitly mark the lead failed + schedule retry so
            # it doesn't stay stuck occupying a slot until the 5-min sweep.
            if committed_calling and call_id is not None:
                try:
                    await db.execute(
                        sql_update(CampaignLead)
                        .where(CampaignLead.id == cl.id)
                        .values(
                            status="failed",
                            last_error=f"Dispatch error: {e}"[:500],
                            last_call_status="failed",
                            next_retry_at=(
                                now_utc() + timedelta(hours=campaign.retry_gap_hours)
                                if cl.attempt_count < campaign.max_retries else None
                            ),
                        )
                    )
                    await db.execute(
                        sql_update(Campaign)
                        .where(Campaign.id == campaign.id)
                        .values(calls_failed=Campaign.calls_failed + 1)
                    )
                    # Drop the in-memory state if we created it
                    from app.services.voice_engine.call_state import call_state_manager
                    call_state_manager.remove(str(call_id))
                    await db.commit()
                except Exception:
                    logger.exception("[CAMPAIGN %s] Failed to mark lead as failed after dispatch error", short)
                    await db.rollback()

    async def _pause_for_missing_agent(
        self,
        db,
        campaign: Campaign,
        short: str,
        *,
        title: str = "Campaign paused — agent unavailable",
        message: Optional[str] = None,
    ):
        """Pause the campaign and notify its creator when the agent is gone.

        Without this, the worker would silently skip every cycle and the user
        would have no visible signal that the campaign stopped making progress.
        """
        if campaign.status != "paused":
            campaign.status = "paused"
        if campaign.created_by is not None:
            db.add(Notification(
                company_id=campaign.company_id,
                user_id=campaign.created_by,
                type=NotificationType.GENERAL.value,
                title=title,
                message=message or (
                    f"Campaign '{campaign.name}' was paused because its AI agent is "
                    f"missing or inactive. Reassign an agent and resume the campaign."
                ),
            ))
        await db.commit()
        logger.error("[CAMPAIGN %s] Paused — agent missing or inactive", short)

    # ── Call completion hook (called from hangup handler) ──

    async def handle_call_completed(self, call_id: str, success: bool):
        """Update campaign lead status when a campaign call ends."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(CampaignLead).where(CampaignLead.last_call_id == _uuid.UUID(call_id))
                )
                cl = result.scalar_one_or_none()
                if not cl:
                    return

                result = await db.execute(
                    select(Campaign).where(Campaign.id == cl.campaign_id)
                )
                campaign = result.scalar_one_or_none()

                now = now_utc()

                # Roll up call cost to campaign total (atomic SQL increment)
                try:
                    call_result = await db.execute(
                        select(CallAttempt.cost).where(CallAttempt.id == _uuid.UUID(call_id))
                    )
                    call_cost = call_result.scalar_one_or_none()
                    if campaign and call_cost:
                        await db.execute(
                            sql_update(Campaign)
                            .where(Campaign.id == campaign.id)
                            .values(total_cost_usd=Campaign.total_cost_usd + float(call_cost))
                        )
                except Exception:
                    logger.exception("[CAMPAIGN] Failed to roll up call cost for %s", call_id)

                if success:
                    cl.status = "completed"
                    cl.completed_at = now
                    cl.last_call_status = "completed"
                    if campaign:
                        await db.execute(
                            sql_update(Campaign)
                            .where(Campaign.id == campaign.id)
                            .values(calls_connected=Campaign.calls_connected + 1)
                        )
                else:
                    cl.last_call_status = "failed"
                    cl.status = "failed"
                    if cl.attempt_count < (campaign.max_retries if campaign else 3):
                        cl.next_retry_at = now + timedelta(
                            hours=(campaign.retry_gap_hours if campaign else 2)
                        )
                    if campaign:
                        await db.execute(
                            sql_update(Campaign)
                            .where(Campaign.id == campaign.id)
                            .values(calls_failed=Campaign.calls_failed + 1)
                        )

                await db.commit()
                logger.info(
                    "[CAMPAIGN %s] Call %s %s",
                    str(cl.campaign_id)[:8], call_id[:8],
                    "completed" if success else "failed",
                )
        except Exception as e:
            logger.exception("CAMPAIGN call completion error: %s", e)

    # ── Check if campaign should auto-complete ─────────────

    async def _check_completion(self, db, campaign: Campaign, short: str):
        """If no leads left to process, mark campaign as completed."""
        remaining = (await db.execute(
            select(func.count()).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status.in_(["pending", "queued", "calling"]),
            )
        )).scalar() or 0

        # Also check failed leads that still have retries left
        retryable = (await db.execute(
            select(func.count()).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.status == "failed",
                CampaignLead.attempt_count < campaign.max_retries,
            )
        )).scalar() or 0

        active = await self._active_count_db(db, campaign.id)
        if remaining == 0 and retryable == 0 and active == 0:
            campaign.status = "completed"
            campaign.completed_at = now_utc()
            await db.commit()
            logger.info("[CAMPAIGN %s] Completed — all leads processed", short)

    # ── Warmup helpers (mirror outbound handler) ───────────

    @staticmethod
    async def _pregen_welcome(state, agent, lead):
        try:
            from app.services.voice_engine.pipeline import voice_pipeline
            from app.services.voice_engine.audio_utils import wav_to_mulaw
            from app.services.voice_engine.plivo_handler import encode_for_plivo
            state.welcome_ready = asyncio.Event()
            wav = await voice_pipeline.generate_welcome_audio(
                agent=agent, lead_name=lead.full_name or "there"
            )
            state.welcome_audio = wav or b""
            if state.welcome_audio:
                mulaw = wav_to_mulaw(state.welcome_audio)
                if mulaw:
                    state.welcome_audio_b64 = encode_for_plivo(mulaw)
        except Exception:
            pass
        finally:
            if state.welcome_ready:
                state.welcome_ready.set()

    @staticmethod
    async def _warmup_llm(agent):
        try:
            from app.services.voice_engine.llm_service import llm_service
            await llm_service.warmup(model=agent.llm_model)
        except Exception:
            pass

    @staticmethod
    async def _warmup_stt(agent):
        try:
            from app.services.voice_engine.sarvam_stt import sarvam_stt
            await sarvam_stt.warmup(model=agent.stt_model or "saaras:v3")
        except Exception:
            pass


# Singleton
campaign_worker = CampaignWorker()
