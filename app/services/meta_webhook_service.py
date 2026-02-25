from __future__ import annotations

import uuid
import logging
import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.lead_stage_log import LeadStageLog
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import LeadStage, LeadSourceType, UserRole, NotificationType
from app.config import get_settings

logger = logging.getLogger(__name__)


class MetaWebhookService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def process_webhook(self, data: dict) -> None:
        """Process incoming Meta Lead Ads webhook payload."""
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                if change.get("field") == "leadgen":
                    value = change.get("value", {})
                    leadgen_id = value.get("leadgen_id")
                    form_id = value.get("form_id")
                    if leadgen_id:
                        await self._process_lead(leadgen_id, form_id)

    async def _process_lead(self, leadgen_id: str, form_id: str | None) -> None:
        """Fetch lead data from Graph API and create lead."""
        try:
            lead_data = await self._fetch_lead_from_graph(leadgen_id)
            if not lead_data:
                logger.error("Failed to fetch lead data for %s", leadgen_id)
                return

            # Parse field data
            fields = {}
            for field in lead_data.get("field_data", []):
                name = field.get("name", "").lower()
                values = field.get("values", [])
                if values:
                    fields[name] = values[0]

            # Map fields
            full_name = fields.get("full_name") or fields.get("name", "Unknown")
            email = fields.get("email")
            phone = fields.get("phone_number") or fields.get("phone")

            # Check duplicate
            if phone:
                result = await self.db.execute(select(Lead).where(Lead.phone == phone).limit(1))
                if result.scalar_one_or_none():
                    logger.info("Duplicate lead from Meta (phone: %s)", phone)
                    return
            if email:
                result = await self.db.execute(select(Lead).where(Lead.email == email).limit(1))
                if result.scalar_one_or_none():
                    logger.info("Duplicate lead from Meta (email: %s)", email)
                    return

            # Get Meta Ads lead source
            result = await self.db.execute(
                select(LeadSource).where(LeadSource.source_type == LeadSourceType.META_ADS).limit(1)
            )
            source = result.scalar_one_or_none()

            # Round-robin assignment
            agent = await self._get_next_agent()

            lead = Lead(
                full_name=full_name,
                email=email,
                phone=phone,
                city=fields.get("city"),
                state=fields.get("state"),
                current_stage=LeadStage.LEAD,
                lead_source_id=source.id if source else None,
                assigned_agent_id=agent.id if agent else None,
                custom_fields={"meta_leadgen_id": leadgen_id, "meta_form_id": form_id},
            )
            self.db.add(lead)
            await self.db.flush()

            # Stage log
            stage_log = LeadStageLog(
                lead_id=lead.id,
                from_stage=None,
                to_stage=LeadStage.LEAD,
                changed_by=agent.id if agent else lead.id,  # system
            )
            self.db.add(stage_log)

            # Notification to agent
            if agent:
                notif = Notification(
                    user_id=agent.id,
                    type=NotificationType.LEAD_ASSIGNED,
                    title="New Lead from Meta Ads",
                    message=f"New lead '{full_name}' assigned to you from Meta Ads.",
                    lead_id=lead.id,
                )
                self.db.add(notif)

            await self.db.commit()
            logger.info("Created lead from Meta: %s (assigned to %s)", lead.id, agent.id if agent else "none")

        except Exception:
            logger.exception("Error processing Meta lead %s", leadgen_id)
            await self.db.rollback()

    async def _fetch_lead_from_graph(self, leadgen_id: str) -> dict | None:
        """Fetch lead data from Facebook Graph API."""
        if not self.settings.meta_access_token:
            logger.warning("META_ACCESS_TOKEN not configured, skipping Graph API fetch")
            return None

        url = f"https://graph.facebook.com/v18.0/{leadgen_id}"
        params = {"access_token": self.settings.meta_access_token}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            logger.error("Graph API error %d: %s", response.status_code, response.text)
            return None

    async def _get_next_agent(self) -> Profile | None:
        """Round-robin: get agent with fewest leads."""
        result = await self.db.execute(
            select(Profile)
            .where(Profile.role == UserRole.AGENT, Profile.is_active == True)
            .outerjoin(Lead, Lead.assigned_agent_id == Profile.id)
            .group_by(Profile.id)
            .order_by(func.count(Lead.id).asc())
            .limit(1)
        )
        return result.scalar_one_or_none()
