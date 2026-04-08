from __future__ import annotations

import uuid
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import UserRole
from app.core.exceptions import NotFoundError, ForbiddenError


class NotificationService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def get_notifications(self, user_id: uuid.UUID, page: int = 1, page_size: int = 25) -> list[Notification]:
        offset = (page - 1) * page_size
        result = await self.db.execute(
            select(Notification)
            .where(Notification.user_id == user_id, Notification.company_id == self.company_id)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return result.scalars().all()

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.company_id == self.company_id, Notification.is_read == False)
        )
        return result.scalar() or 0

    async def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> Notification:
        result = await self.db.execute(
            select(Notification).where(Notification.id == notification_id)
        )
        notif = result.scalar_one_or_none()
        if not notif:
            raise NotFoundError("Notification not found")
        if notif.user_id != user_id:
            raise ForbiddenError("Not authorized")
        notif.is_read = True
        await self.db.commit()
        await self.db.refresh(notif)
        return notif

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.company_id == self.company_id, Notification.is_read == False)
            .values(is_read=True)
        )
        await self.db.commit()
        return result.rowcount

    async def create_notification(
        self,
        user_id: uuid.UUID,
        type: str,
        title: str,
        message: str,
        lead_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
    ) -> Notification:
        notif = Notification(
            company_id=self.company_id,
            user_id=user_id,
            type=type,
            title=title,
            message=message,
            lead_id=lead_id,
            task_id=task_id,
        )
        self.db.add(notif)
        await self.db.commit()
        await self.db.refresh(notif)
        return notif
