from __future__ import annotations

import uuid
import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.lead import Lead
from app.models.ai_agent import AIAgent
from app.core.exceptions import NotFoundError, BadRequestError

logger = logging.getLogger(__name__)


class CampaignService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def create(self, user_id: uuid.UUID, data) -> Campaign:
        # Verify agent exists
        result = await self.db.execute(
            select(AIAgent).where(
                AIAgent.id == data.ai_agent_id,
                AIAgent.company_id == self.company_id,
            )
        )
        if not result.scalar_one_or_none():
            raise NotFoundError("AI Agent not found")

        campaign = Campaign(
            company_id=self.company_id,
            ai_agent_id=data.ai_agent_id,
            name=data.name,
            description=data.description,
            status="draft",
            start_date=data.start_date,
            end_date=data.end_date,
            daily_start_time=data.daily_start_time,
            daily_end_time=data.daily_end_time,
            skip_weekends=data.skip_weekends,
            timezone=data.timezone,
            max_retries=data.max_retries,
            retry_gap_hours=data.retry_gap_hours,
            max_concurrent_calls=data.max_concurrent_calls,
            created_by=user_id,
        )
        self.db.add(campaign)
        await self.db.flush()

        if data.lead_ids:
            await self._assign_leads(campaign, data.lead_ids)

        await self.db.commit()
        await self.db.refresh(campaign)
        return campaign

    async def list(self, status: Optional[str] = None, page: int = 1, page_size: int = 20):
        query = (
            select(Campaign)
            .where(Campaign.company_id == self.company_id)
            .options(selectinload(Campaign.agent))
            .order_by(Campaign.created_at.desc())
        )
        if status:
            query = query.where(Campaign.status == status)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def get(self, campaign_id: uuid.UUID) -> Campaign:
        result = await self.db.execute(
            select(Campaign)
            .where(Campaign.id == campaign_id, Campaign.company_id == self.company_id)
            .options(selectinload(Campaign.agent))
        )
        campaign = result.scalar_one_or_none()
        if not campaign:
            raise NotFoundError("Campaign not found")
        return campaign

    async def update(self, campaign_id: uuid.UUID, data) -> Campaign:
        campaign = await self.get(campaign_id)
        if campaign.status == "active":
            raise BadRequestError("Cannot edit active campaign. Pause it first.")

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(campaign, field, value)
        await self.db.commit()
        await self.db.refresh(campaign)
        return campaign

    async def delete(self, campaign_id: uuid.UUID):
        campaign = await self.get(campaign_id)
        if campaign.status == "active":
            raise BadRequestError("Cannot delete active campaign. Stop it first.")
        await self.db.delete(campaign)
        await self.db.commit()

    async def assign_leads(self, campaign_id: uuid.UUID, lead_ids: List[uuid.UUID]) -> int:
        campaign = await self.get(campaign_id)
        return await self._assign_leads(campaign, lead_ids)

    async def _assign_leads(self, campaign: Campaign, lead_ids: List[uuid.UUID]) -> int:
        # Get valid leads from this company
        result = await self.db.execute(
            select(Lead.id).where(
                Lead.id.in_(lead_ids),
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        valid_ids = {row[0] for row in result.all()}

        # Check existing
        result = await self.db.execute(
            select(CampaignLead.lead_id).where(
                CampaignLead.campaign_id == campaign.id,
                CampaignLead.lead_id.in_(valid_ids),
            )
        )
        existing_ids = {row[0] for row in result.all()}

        added = 0
        for lid in valid_ids - existing_ids:
            self.db.add(CampaignLead(
                campaign_id=campaign.id,
                lead_id=lid,
                company_id=self.company_id,
                status="pending",
            ))
            added += 1

        campaign.total_leads = (campaign.total_leads or 0) + added
        await self.db.commit()
        return added

    async def start(self, campaign_id: uuid.UUID) -> Campaign:
        campaign = await self.get(campaign_id)
        if campaign.status not in ("draft", "paused", "scheduled"):
            raise BadRequestError(f"Cannot start {campaign.status} campaign")

        count = (await self.db.execute(
            select(func.count()).where(CampaignLead.campaign_id == campaign_id)
        )).scalar() or 0
        if count == 0:
            raise BadRequestError("No leads assigned to campaign")

        campaign.status = "active"
        if not campaign.started_at:
            campaign.started_at = datetime.utcnow()
        await self.db.commit()
        return campaign

    async def pause(self, campaign_id: uuid.UUID) -> Campaign:
        campaign = await self.get(campaign_id)
        if campaign.status != "active":
            raise BadRequestError("Campaign not active")
        campaign.status = "paused"
        await self.db.commit()
        return campaign

    async def stop(self, campaign_id: uuid.UUID) -> Campaign:
        campaign = await self.get(campaign_id)
        campaign.status = "stopped"
        campaign.completed_at = datetime.utcnow()
        await self.db.commit()
        return campaign

    async def get_stats(self, campaign_id: uuid.UUID) -> dict:
        await self.get(campaign_id)  # verify access

        result = await self.db.execute(
            select(CampaignLead.status, func.count(CampaignLead.id))
            .where(CampaignLead.campaign_id == campaign_id)
            .group_by(CampaignLead.status)
        )
        stats = {s: 0 for s in ("pending", "queued", "calling", "completed", "failed", "dnd", "opted_out")}
        for status, count in result.all():
            stats[status] = count

        total = sum(stats.values())
        return {
            "total_leads": total,
            **stats,
            "success_rate": round(stats["completed"] / total * 100, 1) if total > 0 else 0,
        }

    async def get_leads(
        self, campaign_id: uuid.UUID, status: Optional[str] = None, page: int = 1, page_size: int = 50
    ):
        await self.get(campaign_id)  # verify access

        query = (
            select(CampaignLead)
            .where(CampaignLead.campaign_id == campaign_id)
            .options(selectinload(CampaignLead.lead))
            .order_by(CampaignLead.priority.desc(), CampaignLead.created_at)
        )
        if status:
            query = query.where(CampaignLead.status == status)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_q)).scalar() or 0

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        return result.scalars().all(), total
