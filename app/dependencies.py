import uuid
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.core.security import verify_jwt
from app.core.exceptions import UnauthorizedError, ForbiddenError
from app.core.constants import UserRole
from app.models.profile import Profile


async def get_current_user(
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token")

    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise ForbiddenError("Account is deactivated")

    return user


async def get_current_admin(
    current_user: Profile = Depends(get_current_user),
) -> Profile:
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user
