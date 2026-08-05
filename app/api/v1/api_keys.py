from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.api_key import generate_api_key
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.tenant import get_current_company_id
from app.db.session import get_db
from app.dependencies import get_current_admin_human
from app.models.api_key import ApiKey
from app.models.profile import Profile
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit")

def require_api_keys_enabled() -> None:
    """404 the whole surface on a deployment where keys are switched off.

    404 rather than 403: on the Admitverse backend this feature does not
    exist, and saying so is more honest than implying the caller merely
    lacks permission. Applied at the router so no individual endpoint can
    forget it — same reasoning as the DELETE guard living in middleware.
    """
    if not get_settings().api_keys_enabled:
        raise NotFoundError("API key management is not enabled on this deployment")


# Admin-only, human-admin-only (see get_current_admin_human — keys cannot
# administer keys), and only on a deployment with API keys switched on.
router = APIRouter(
    prefix="/api-keys",
    tags=["API Keys"],
    dependencies=[Depends(require_api_keys_enabled)],
)


def _to_out(row: ApiKey, profile: Profile | None) -> dict:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "profile_id": row.profile_id,
        "profile_email": profile.email if profile else None,
        "profile_role": profile.role if profile else None,
        "name": row.name,
        "key_prefix": row.key_prefix,
        "is_active": row.is_active,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    body: ApiKeyCreate,
    admin: Profile = Depends(get_current_admin_human),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Mint an API key bound to a service-account profile.

    The plaintext key is in the `api_key` field of the response and is
    shown **once** — only its SHA-256 is stored. Losing it means minting
    a replacement.

    The key inherits the scope of `profile_id`'s role, so an
    admin-equivalent credential needs a profile with `role="admin"`.
    Regardless of role, no API key can DELETE (enforced globally in
    ApiKeyDeleteGuardMiddleware).

    Setting up a service account:
      1. POST /api/v1/auth/register  {"email": "...", "password": "...",
         "full_name": "...", "role": "admin"}
      2. GET  /api/v1/users?role=admin  → copy the new profile's id
      3. POST /api/v1/api-keys  {"profile_id": "<id>", "name": "..."}
    """
    profile = (
        await db.execute(
            select(Profile).where(
                Profile.id == body.profile_id,
                Profile.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    # Scoped to the admin's own company, so this 404s rather than 403s
    # for a profile in another tenant — the caller has no business
    # learning whether that id exists.
    if profile is None:
        raise NotFoundError("Profile not found in this company")
    if not profile.is_active:
        raise BadRequestError(
            "Cannot mint a key for a deactivated profile — every request "
            "with it would be rejected."
        )
    if body.expires_at is not None and body.expires_at <= now_utc():
        raise BadRequestError("expires_at must be in the future")

    raw, prefix, key_hash = generate_api_key()
    row = ApiKey(
        company_id=company_id,
        profile_id=profile.id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        expires_at=body.expires_at,
        created_by=admin.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    audit_logger.info(
        "API_KEY_CREATED key_id=%s name=%s profile_id=%s company_id=%s by=%s",
        row.id, row.name, row.profile_id, company_id, admin.email,
    )
    return {**_to_out(row, profile), "api_key": raw}


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    admin: Profile = Depends(get_current_admin_human),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    include_revoked: bool = Query(False),
):
    """List this company's API keys. Secrets are never returned."""
    query = select(ApiKey).where(ApiKey.company_id == company_id)
    if not include_revoked:
        query = query.where(ApiKey.is_active == True)  # noqa: E712
    rows = (await db.execute(query.order_by(ApiKey.created_at.desc()))).scalars().all()

    profiles = {
        p.id: p
        for p in (
            await db.execute(
                select(Profile).where(Profile.id.in_([r.profile_id for r in rows]))
            )
        ).scalars().all()
    } if rows else {}

    return [_to_out(r, profiles.get(r.profile_id)) for r in rows]


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_api_key(
    key_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin_human),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a key. Takes effect on its next request — there is no
    cached credential to wait out.

    Deliberately a POST, not a DELETE: the row is kept so the audit
    trail can still explain which credential made which writes. Revoking
    is idempotent.
    """
    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.company_id == company_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("API key not found")

    if row.is_active:
        row.is_active = False
        row.revoked_at = now_utc()
        row.revoked_by = admin.id
        await db.commit()
        await db.refresh(row)
        audit_logger.warning(
            "API_KEY_REVOKED key_id=%s name=%s by=%s", row.id, row.name, admin.email,
        )

    profile = (
        await db.execute(select(Profile).where(Profile.id == row.profile_id))
    ).scalar_one_or_none()
    return _to_out(row, profile)
