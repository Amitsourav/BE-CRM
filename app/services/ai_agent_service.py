from __future__ import annotations

import uuid
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.ai_agent import AIAgent
from app.core.exceptions import NotFoundError, BadRequestError


class AIAgentService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def create_agent(self, data: dict) -> AIAgent:
        # If setting as default, unset existing defaults first
        if data.get("is_default"):
            await self._unset_defaults()

        agent = AIAgent(company_id=self.company_id, **data)
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def get_agents(
        self,
        is_active: bool | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[AIAgent]:
        query = (
            select(AIAgent)
            .where(AIAgent.company_id == self.company_id)
            .order_by(AIAgent.is_default.desc(), AIAgent.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        if is_active is not None:
            query = query.where(AIAgent.is_active == is_active)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_agent(self, agent_id: uuid.UUID) -> AIAgent:
        result = await self.db.execute(
            select(AIAgent).where(
                AIAgent.id == agent_id,
                AIAgent.company_id == self.company_id,
            )
        )
        agent = result.scalar_one_or_none()
        if not agent:
            raise NotFoundError("AI Agent not found")
        return agent

    async def update_agent(self, agent_id: uuid.UUID, data: dict) -> AIAgent:
        agent = await self.get_agent(agent_id)

        # If setting as default, unset existing defaults first
        if data.get("is_default"):
            await self._unset_defaults()

        for key, value in data.items():
            setattr(agent, key, value)

        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def delete_agent(self, agent_id: uuid.UUID) -> None:
        agent = await self.get_agent(agent_id)

        if agent.is_default:
            raise BadRequestError(
                "Cannot delete default agent. Please set another agent as default first."
            )

        await self.db.delete(agent)
        await self.db.commit()

    async def get_default_agent(self) -> AIAgent | None:
        result = await self.db.execute(
            select(AIAgent).where(
                AIAgent.company_id == self.company_id,
                AIAgent.is_default == True,
                AIAgent.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def set_default(self, agent_id: uuid.UUID) -> AIAgent:
        agent = await self.get_agent(agent_id)
        await self._unset_defaults()
        agent.is_default = True
        await self.db.commit()
        await self.db.refresh(agent)
        return agent

    async def clone_agent(self, agent_id: uuid.UUID, created_by: uuid.UUID | None = None) -> AIAgent:
        source = await self.get_agent(agent_id)

        # Copy all column values except id, created_at, updated_at, deleted_at, created_by
        skip = {"id", "created_at", "updated_at", "deleted_at", "created_by"}
        data = {}
        for col in AIAgent.__table__.columns:
            if col.name not in skip:
                data[col.name] = getattr(source, col.name)

        data["name"] = f"{source.name} (copy)"
        data["is_default"] = False
        data["created_by"] = created_by

        clone = AIAgent(**data)
        self.db.add(clone)
        await self.db.commit()
        await self.db.refresh(clone)
        return clone

    async def _unset_defaults(self) -> None:
        """Set is_default=False for all agents in this company."""
        await self.db.execute(
            update(AIAgent)
            .where(AIAgent.company_id == self.company_id, AIAgent.is_default == True)
            .values(is_default=False)
        )
