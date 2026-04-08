from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_admin
from app.models.profile import Profile
from app.services.company_service import CompanyService
from app.schemas.company import CompanyCreate, CompanyUpdate, CompanyOut

router = APIRouter(prefix="/companies", tags=["Companies"])


@router.post("", response_model=CompanyOut, status_code=201)
async def create_company(
    body: CompanyCreate,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = CompanyService(db)
    return await service.create_company(body.model_dump())


@router.get("", response_model=list[CompanyOut])
async def list_companies(
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = CompanyService(db)
    return await service.list_companies()


@router.get("/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = CompanyService(db)
    return await service.get_company(company_id)


@router.put("/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: uuid.UUID,
    body: CompanyUpdate,
    admin: Profile = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    service = CompanyService(db)
    return await service.update_company(company_id, body.model_dump(exclude_unset=True))
