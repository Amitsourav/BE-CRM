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
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.ai_agent import AIAgent
from app.models.call_attempt import CallAttempt

logger = logging.getLogger(__name__)


class CampaignWorker:
    def __init__(self):
        self._running = False
        # {campaign_id: int} — in-flight calls per campaign
        self._active: dict[str, int] = {}

    # ── Calling hours ──────────────────────────────────────

    @staticmethod
    def _in_calling_hours(campaign: Campaign) -> bool:
        tz = pytz.timezone(campaign.timezone or "Asia/Kolkata")
        now = datetime.now(tz)

        # Skip weekends
        if campaign.skip_weekends and now.weekday() >= 5:
            return False

        # Daily time window
        if campaign.daily_start_time and campaign.daily_end_time:
            if not (campaign.daily_start_time <= now.time() <= campaign.daily_end_time):
                return False

        # Campaign date range
        now_naive = now.replace(tzinfo=None)
        if campaign.start_date and now_naive < campaign.start_date:
            return False
        if campaign.end_date and now_naive > campaign.end_date:
            return False

        return True

    # ── Active call tracking ───────────────────────────────

    def _active_count(self, cid: str) -> int:
        return self._active.get(cid, 0)

    def _inc(self, cid: str):
        self._active[cid] = self._active.get(cid, 0) + 1

    def _dec(self, cid: str):
        v = self._active.get(cid, 0)
        self._active[cid] = max(0, v - 1)

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
                    return

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

        if not self._in_calling_hours(campaign):
            return

        active = self._active_count(cid)
        slots = (campaign.max_concurrent_calls or 5) - active
        if slots <= 0:
            return

        agent = campaign.agent
        if not agent:
            logger.error("[CAMPAIGN %s] Agent missing", short)
            return

        # Pick leads: pending OR (failed + under retry limit + retry time passed)
        now = datetime.utcnow()
        result = await db.execute(
            select(CampaignLead)
            .where(
                CampaignLead.campaign_id == campaign.id,
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

        if not leads_to_call:
            await self._check_completion(db, campaign, short)
            return

        for cl in leads_to_call:
            await self._dispatch_call(db, campaign, cl, agent, short)
            await asyncio.sleep(0.3)

    # ── Dispatch a single call ─────────────────────────────

    async def _dispatch_call(self, db, campaign, cl: CampaignLead, agent: AIAgent, short: str):
        try:
            # Load lead
            result = await db.execute(select(Lead).where(Lead.id == cl.lead_id))
            lead = result.scalar_one_or_none()
            if not lead or not lead.phone:
                cl.status = "failed"
                cl.last_error = "No phone number"
                await db.commit()
                return

            call_id = _uuid.uuid4()

            # Create CallAttempt
            call = CallAttempt(
                id=call_id,
                company_id=campaign.company_id,
                lead_id=lead.id,
                agent_id=campaign.created_by or lead.id,  # fallback
                telecaller_id=campaign.created_by,
                ai_agent_id=agent.id,
                phone_number=lead.phone,
                attempt_number=cl.attempt_count + 1,
                disposition="connected",
                conversation_notes="",
                agent_agenda="",
                call_type="ai_campaign",
                call_status="initiated",
                started_at=datetime.utcnow(),
            )
            db.add(call)

            # Update campaign lead
            cl.status = "calling"
            cl.attempt_count += 1
            cl.last_attempt_at = datetime.utcnow()
            cl.last_call_id = call_id
            campaign.calls_made = (campaign.calls_made or 0) + 1
            await db.commit()

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
                # Global concurrent cap hit
                cl.status = "pending" if cl.attempt_count <= 1 else "failed"
                cl.attempt_count = max(0, cl.attempt_count - 1)
                campaign.calls_made = max(0, (campaign.calls_made or 1) - 1)
                await db.commit()
                return

            # Pre-gen welcome + warmups (same as outbound handler)
            asyncio.create_task(self._pregen_welcome(state, agent, lead))
            asyncio.create_task(self._warmup_llm(agent))
            asyncio.create_task(self._warmup_stt(agent))

            self._inc(str(campaign.id))

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
                    cl.next_retry_at = datetime.utcnow() + timedelta(hours=campaign.retry_gap_hours)
                campaign.calls_failed = (campaign.calls_failed or 0) + 1
                self._dec(str(campaign.id))
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
            logger.error("[CAMPAIGN %s] Dispatch error: %s", short, e)
            await db.rollback()

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

                if success:
                    cl.status = "completed"
                    cl.completed_at = datetime.utcnow()
                    cl.last_call_status = "completed"
                    if campaign:
                        campaign.calls_connected = (campaign.calls_connected or 0) + 1
                else:
                    cl.last_call_status = "failed"
                    if cl.attempt_count < (campaign.max_retries if campaign else 3):
                        cl.status = "failed"
                        cl.next_retry_at = datetime.utcnow() + timedelta(
                            hours=(campaign.retry_gap_hours if campaign else 2)
                        )
                    else:
                        cl.status = "failed"
                    if campaign:
                        campaign.calls_failed = (campaign.calls_failed or 0) + 1

                if campaign:
                    self._dec(str(campaign.id))

                await db.commit()
                logger.info(
                    "[CAMPAIGN %s] Call %s %s",
                    str(cl.campaign_id)[:8], call_id[:8],
                    "completed" if success else "failed",
                )
        except Exception as e:
            logger.error("CAMPAIGN call completion error: %s", e)

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

        if remaining == 0 and retryable == 0 and self._active_count(str(campaign.id)) == 0:
            campaign.status = "completed"
            campaign.completed_at = datetime.utcnow()
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
