from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.company import Company
from app.core.exceptions import NotFoundError, ConflictError


class CompanyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_company(self, data: dict) -> Company:
        # Check slug uniqueness
        result = await self.db.execute(
            select(Company).where(Company.slug == data["slug"])
        )
        if result.scalar_one_or_none():
            raise ConflictError(f"Company with slug '{data['slug']}' already exists")

        company = Company(**data)
        self.db.add(company)
        await self.db.commit()
        await self.db.refresh(company)
        return company

    async def get_company(self, company_id: uuid.UUID) -> Company:
        result = await self.db.execute(select(Company).where(Company.id == company_id))
        company = result.scalar_one_or_none()
        if not company:
            raise NotFoundError("Company not found")
        return company

    async def list_companies(self) -> list[Company]:
        result = await self.db.execute(
            select(Company).order_by(Company.created_at.desc())
        )
        return result.scalars().all()

    async def update_company(self, company_id: uuid.UUID, data: dict) -> Company:
        company = await self.get_company(company_id)

        # If slug is changing, check uniqueness
        if "slug" in data and data["slug"] != company.slug:
            result = await self.db.execute(
                select(Company).where(Company.slug == data["slug"])
            )
            if result.scalar_one_or_none():
                raise ConflictError(f"Company with slug '{data['slug']}' already exists")

        for key, value in data.items():
            setattr(company, key, value)
        await self.db.commit()
        await self.db.refresh(company)
        return company
