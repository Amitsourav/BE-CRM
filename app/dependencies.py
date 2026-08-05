import time
import uuid
from typing import Dict, Optional, Tuple

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.api_key import API_KEY_HEADER, resolve_api_key
from app.core.constants import UserRole
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_jwt
from app.db.session import get_db
from app.models.profile import Profile

# Both schemes are declared non-auto-erroring: a request carrying an API
# key has no Authorization header, and a request carrying a JWT has no
# X-API-Key header. Letting either scheme 403 on its own absence would
# make it impossible to accept both on the same route. `get_current_user`
# does the "at least one, and it must be valid" check instead.
_bearer_optional = HTTPBearer(auto_error=False)
_api_key_optional = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


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
    request: Request,
    api_key: Optional[str] = Depends(_api_key_optional),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_optional),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Profile:
    """Resolve the caller to a Profile from either credential type.

    Two ways in:
      • `X-API-Key: crmk_live_…`  — a machine client (app/core/api_key.py)
      • `Authorization: Bearer …` — a human's Supabase session

    Every authenticated route in the app depends on this function (directly
    or through get_current_admin / get_current_manager / get_current_company_id),
    so accepting keys here is what makes the credential general rather than
    bolted onto a handful of endpoints.

    Sets `request.state.auth_method` to "api_key" or "jwt" — for logging
    and for any future caller that needs to branch on how the principal
    authenticated. Note that neither of the two rules restricting keys
    reads it: ApiKeyDeleteGuardMiddleware and get_current_admin_human
    both inspect the raw header instead, so they hold even on a path
    that never reaches this function.
    """
    # API key wins when both are present. A caller that sends a key is
    # asking to act as the machine principal; silently preferring a
    # co-present human token would attribute the write to the wrong
    # identity, and the DELETE guard keys off the header's presence.
    if api_key:
        user = await resolve_api_key(db, api_key)
        request.state.auth_method = "api_key"
        # No profile-cache write here. resolve_api_key already validated
        # the account is active on this request, and caching under the
        # profile id would let a JWT request for the same account skip
        # its own liveness check.
        return user

    request.state.auth_method = "jwt"

    if credentials is None:
        raise UnauthorizedError("Not authenticated")

    payload = decode_jwt(credentials.credentials, settings)
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
    """Any authenticated role (admin, manager, pre_counsellor)."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER, UserRole.PRE_COUNSELLOR):
        raise ForbiddenError("Access denied")
    return current_user


async def get_current_admin_human(
    request: Request,
    current_user: Profile = Depends(get_current_admin),
) -> Profile:
    """Admin, authenticated by a human login — never by an API key.

    Gates API-key administration itself. A leaked key that could mint
    further keys, or lift its own expiry, would be a credential that
    escalates rather than one that can be contained; requiring a human
    session to manage keys keeps revocation meaningful.

    Reads the raw header rather than `request.state.auth_method` so the
    check holds even if some future dependency reaches these routes
    without passing through `get_current_user`.
    """
    if request.headers.get(API_KEY_HEADER):
        raise ForbiddenError(
            "API keys cannot manage API keys. Use a human admin login for this endpoint."
        )
    return current_user
