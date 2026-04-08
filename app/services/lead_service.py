from __future__ import annotations

import uuid
import logging
from datetime import date
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.profile import Profile
from app.models.lead_stage_log import LeadStageLog
from app.core.exceptions import NotFoundError, ForbiddenError, BadRequestError
from app.core.constants import UserRole, LeadStage
from app.utils.pagination import paginate
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


class LeadService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def create_lead(self, data: dict, created_by: uuid.UUID) -> Lead:
        data["company_id"] = self.company_id
        lead = Lead(**data, created_by=created_by)
        self.db.add(lead)
        await self.db.flush()

        # Create initial stage log
        stage_log = LeadStageLog(
            company_id=self.company_id,
            lead_id=lead.id,
            from_stage=None,
            to_stage=LeadStage.LEAD,
            changed_by=created_by,
        )
        self.db.add(stage_log)
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def get_lead(self, lead_id: uuid.UUID, user: Profile) -> Lead:
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role == UserRole.TELECALLER and lead.assigned_agent_id != user.id:
            raise ForbiddenError("Not authorized to view this lead")
        return lead

    async def update_lead(self, lead_id: uuid.UUID, data: dict, user: Profile) -> Lead:
        lead = await self.get_lead(lead_id, user)
        for key, value in data.items():
            setattr(lead, key, value)
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def delete_lead(self, lead_id: uuid.UUID) -> None:
        """Soft delete — sets is_deleted=True and deleted_at timestamp."""
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        lead.is_deleted = True
        lead.deleted_at = now_utc()
        await self.db.commit()

    async def list_leads(
        self,
        user: Profile,
        page: int = 1,
        page_size: int = 25,
        stage: str | None = None,
        agent_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:
        query = select(Lead).where(Lead.company_id == self.company_id, Lead.is_deleted == False).order_by(Lead.created_at.desc())

        if user.role == UserRole.TELECALLER:
            query = query.where(Lead.assigned_agent_id == user.id)
        elif agent_id:
            query = query.where(Lead.assigned_agent_id == agent_id)

        if stage:
            query = query.where(Lead.current_stage == stage)
        if source_id:
            query = query.where(Lead.lead_source_id == source_id)
        if tags:
            query = query.where(Lead.tags.overlap(tags))
        if date_from:
            query = query.where(func.date(Lead.created_at) >= date_from)
        if date_to:
            query = query.where(func.date(Lead.created_at) <= date_to)

        return await paginate(self.db, query, page, page_size)

    async def search_leads(self, q: str, user: Profile, page: int = 1, page_size: int = 25) -> dict:
        query = select(Lead).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,
            or_(
                Lead.full_name.ilike(f"%{q}%"),
                Lead.email.ilike(f"%{q}%"),
                Lead.phone.ilike(f"%{q}%"),
            )
        ).order_by(Lead.created_at.desc())

        if user.role == UserRole.TELECALLER:
            query = query.where(Lead.assigned_agent_id == user.id)

        return await paginate(self.db, query, page, page_size)

    async def assign_lead(self, lead_id: uuid.UUID, agent_id: uuid.UUID) -> Lead:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        result = await self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.company_id == self.company_id, Lead.is_deleted == False)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")

        lead.assigned_agent_id = agent_id
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def bulk_assign(self, lead_ids: list[uuid.UUID], agent_id: uuid.UUID) -> int:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        stmt = (
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.company_id == self.company_id, Lead.is_deleted == False)
            .values(assigned_agent_id=agent_id)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount

    async def get_timeline(self, lead_id: uuid.UUID, user: Profile) -> list[LeadStageLog]:
        await self.get_lead(lead_id, user)  # Auth check
        result = await self.db.execute(
            select(LeadStageLog)
            .where(LeadStageLog.lead_id == lead_id, LeadStageLog.company_id == self.company_id)
            .order_by(LeadStageLog.created_at.desc())
        )
        return result.scalars().all()
