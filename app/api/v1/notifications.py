from __future__ import annotations

import asyncio
import logging
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

logger = logging.getLogger(__name__)
from app.dependencies import get_current_user
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.notification_service import NotificationService
from app.schemas.notification import NotificationOut, UnreadCountOut

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    service = NotificationService(db, company_id)
    return await service.get_notifications(current_user.id, page, page_size)


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    # Polled endpoint — must never return 500. Supabase slow/timeout → return 0.
    try:
        service = NotificationService(db, company_id)
        count = await asyncio.wait_for(
            service.get_unread_count(current_user.id), timeout=5.0
        )
        return UnreadCountOut(count=count)
    except asyncio.TimeoutError:
        logger.warning("unread_count DB timeout — returning 0 fallback")
        return UnreadCountOut(count=0)
    except Exception as e:
        logger.warning("unread_count failed: %s — returning 0 fallback", e)
        return UnreadCountOut(count=0)


@router.put("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = NotificationService(db, company_id)
    return await service.mark_read(notification_id, current_user.id)


@router.put("/read-all")
async def mark_all_read(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = NotificationService(db, company_id)
    count = await service.mark_all_read(current_user.id)
    return {"message": f"{count} notifications marked as read"}
