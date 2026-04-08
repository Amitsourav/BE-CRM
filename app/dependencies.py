import time
import uuid
from typing import Dict, Optional, Tuple

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import verify_jwt
from app.db.session import get_db
from app.models.profile import Profile


# In-memory TTL cache for profile lookups — Supabase Korea region can be
# 2-20s per query, which turns every authenticated endpoint into a 20s
# request. Profiles change rarely; a 30s cache eliminates the bottleneck
# while still picking up role/active changes within the cache lifetime.
_PROFILE_CACHE: Dict[str, Tuple[Profile, float]] = {}
_PROFILE_CACHE_TTL = 30.0  # seconds


def _cached_profile(user_id: str) -> Optional[Profile]:
    entry = _PROFILE_CACHE.get(user_id)
    if not entry:
        return None
    profile, expires_at = entry
    if expires_at < time.time():
        _PROFILE_CACHE.pop(user_id, None)
        return None
    return profile


def _cache_profile(user_id: str, profile: Profile) -> None:
    _PROFILE_CACHE[user_id] = (profile, time.time() + _PROFILE_CACHE_TTL)
    # Opportunistic cleanup — keep cache bounded
    if len(_PROFILE_CACHE) > 1000:
        now = time.time()
        stale = [k for k, (_, exp) in _PROFILE_CACHE.items() if exp < now]
        for k in stale:
            _PROFILE_CACHE.pop(k, None)


async def get_current_user(
    payload: dict = Depends(verify_jwt),
    db: AsyncSession = Depends(get_db),
) -> Profile:
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token")

    # Fast path: cached profile (30s TTL)
    cached = _cached_profile(user_id)
    if cached is not None:
        if not cached.is_active:
            raise ForbiddenError("Account is deactivated")
        return cached

    result = await db.execute(select(Profile).where(Profile.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise ForbiddenError("Account is deactivated")

    _cache_profile(user_id, user)
    return user


async def get_current_admin(
    current_user: Profile = Depends(get_current_user),
) -> Profile:
    """Only admin role."""
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user


async def get_current_manager(
    current_user: Profile = Depends(get_current_user),
) -> Profile:
    """Admin or manager role."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise ForbiddenError("Manager access required")
    return current_user


async def get_current_telecaller(
    current_user: Profile = Depends(get_current_user),
) -> Profile:
    """Any authenticated role (admin, manager, telecaller)."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.TELECALLER):
        raise ForbiddenError("Access denied")
    return current_user
