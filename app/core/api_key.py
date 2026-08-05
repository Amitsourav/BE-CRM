"""API-key credentials for machine clients.

Generation, hashing, and resolution live here; the wiring that makes a
key usable on *every* authenticated route lives in `app/dependencies.py`
(`get_current_user` accepts a key or a JWT), and the guard that stops a
key from reaching any DELETE route lives in `app/core/middleware.py`.

Deliberately not attached to individual endpoints. The existing
`X-Internal-Secret` checks are inline in two route handlers, which means
every new endpoint that wants machine access has to re-implement the
check. Resolving the key inside `get_current_user` instead means an API
key works anywhere a logged-in user works, and new routes inherit it.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.models.api_key import ApiKey
from app.models.profile import Profile
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)

# Header the machine client sends the key in.
API_KEY_HEADER = "X-API-Key"

# crmk = "CRM key". The brand is not in the prefix on purpose — one
# codebase serves FundMyCampus and Admitverse, and a key is only ever
# valid against the deployment whose database holds its row.
_KEY_PREFIX = "crmk"
_KEY_ENV = "live"
# 32 bytes → 43 url-safe chars. Well past guessing range, and short
# enough to paste into an env var without wrapping.
_KEY_ENTROPY_BYTES = 32

# The visible-in-the-UI portion: "crmk_live_" plus the first 6 chars.
_PREFIX_DISPLAY_LEN = len(f"{_KEY_PREFIX}_{_KEY_ENV}_") + 6


def generate_api_key() -> tuple[str, str, str]:
    """Mint a new key. Returns (plaintext, display_prefix, sha256_hex).

    The plaintext is returned to the caller exactly once and never
    stored. Losing it means minting a new key.
    """
    raw = f"{_KEY_PREFIX}_{_KEY_ENV}_{secrets.token_urlsafe(_KEY_ENTROPY_BYTES)}"
    return raw, raw[:_PREFIX_DISPLAY_LEN], hash_api_key(raw)


def hash_api_key(raw: str) -> str:
    """SHA-256 hex of a key.

    Plain SHA-256 rather than bcrypt/argon2 on purpose: this value is a
    192-bit random token, not a user-chosen password, so there is no
    dictionary to slow down — and it is verified on *every* API request,
    where a deliberately-slow KDF would add tens of milliseconds to each
    call. The lookup is also a hash-equality probe, which a per-row salt
    would turn into a table scan.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_api_key(raw: str | None) -> bool:
    """Cheap shape check so a malformed value fails before touching the DB."""
    return bool(raw) and raw.startswith(f"{_KEY_PREFIX}_")


# ── last_used_at write throttle ────────────────────────────────────────
# Stamping last_used_at on every request would put an UPDATE on the hot
# path of an already latency-sensitive backend (Supabase Korea, 2-20s
# tail). A per-process, per-key 60s throttle keeps the column useful for
# "is this key still in use / when did it go quiet" without the write
# amplification. Approximate by design.
_LAST_USED_THROTTLE_S = 60.0
_last_used_marks: dict[uuid.UUID, float] = {}


def _should_stamp_last_used(key_id: uuid.UUID) -> bool:
    now = time.monotonic()
    prev = _last_used_marks.get(key_id)
    if prev is not None and (now - prev) < _LAST_USED_THROTTLE_S:
        return False
    _last_used_marks[key_id] = now
    if len(_last_used_marks) > 500:
        cutoff = now - _LAST_USED_THROTTLE_S
        for k in [k for k, t in _last_used_marks.items() if t < cutoff]:
            _last_used_marks.pop(k, None)
    return True


async def resolve_api_key(db: AsyncSession, raw: str) -> Profile:
    """Authenticate a raw API key and return the profile it acts as.

    Raises UnauthorizedError for every failure mode, with the same
    generic message, so a caller can't distinguish "no such key" from
    "revoked" from "expired" from "the service account was disabled".
    The specific reason is logged server-side.
    """
    if not looks_like_api_key(raw):
        raise UnauthorizedError("Invalid API key")

    row = (
        await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw))
        )
    ).scalar_one_or_none()

    if row is None:
        logger.warning("API_KEY_AUTH_FAILED reason=unknown_key")
        raise UnauthorizedError("Invalid API key")

    # Constant-time re-compare of the stored hash. The DB lookup above
    # already matched on equality, so this is belt-and-braces against a
    # future change that switches to a prefix lookup + compare.
    if not hmac.compare_digest(row.key_hash, hash_api_key(raw)):
        logger.warning("API_KEY_AUTH_FAILED key_id=%s reason=hash_mismatch", row.id)
        raise UnauthorizedError("Invalid API key")

    if not row.is_active or row.revoked_at is not None:
        logger.warning("API_KEY_AUTH_FAILED key_id=%s reason=revoked", row.id)
        raise UnauthorizedError("Invalid API key")

    if row.expires_at is not None and row.expires_at <= now_utc():
        logger.warning("API_KEY_AUTH_FAILED key_id=%s reason=expired", row.id)
        raise UnauthorizedError("Invalid API key")

    profile = row.profile
    if profile is None:
        profile = (
            await db.execute(select(Profile).where(Profile.id == row.profile_id))
        ).scalar_one_or_none()

    if profile is None:
        logger.warning("API_KEY_AUTH_FAILED key_id=%s reason=missing_profile", row.id)
        raise UnauthorizedError("Invalid API key")

    if not profile.is_active:
        logger.warning(
            "API_KEY_AUTH_FAILED key_id=%s profile_id=%s reason=inactive_service_account",
            row.id, profile.id,
        )
        raise UnauthorizedError("Invalid API key")

    # Guard against a key outliving a profile that moved tenants. The
    # key is minted for one company; if the account is now elsewhere,
    # honouring the key would be a cross-tenant write.
    if profile.company_id != row.company_id:
        logger.error(
            "API_KEY_AUTH_FAILED key_id=%s reason=company_mismatch key_company=%s profile_company=%s",
            row.id, row.company_id, profile.company_id,
        )
        raise UnauthorizedError("Invalid API key")

    if _should_stamp_last_used(row.id):
        try:
            await db.execute(
                update(ApiKey).where(ApiKey.id == row.id).values(last_used_at=now_utc())
            )
            await db.commit()
        except Exception:
            # Telemetry must never fail a request. The session is left
            # dirty-free by the rollback so the route's own work proceeds.
            await db.rollback()
            logger.exception("API_KEY last_used_at stamp failed key_id=%s", row.id)

    logger.info(
        "API_KEY_AUTH ok key_id=%s name=%s profile_id=%s company_id=%s",
        row.id, row.name, profile.id, row.company_id,
    )
    return profile
