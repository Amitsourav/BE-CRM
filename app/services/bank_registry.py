"""Reads of the canonical lender list.

Every bank-name validation and every grid render goes through here, so it
is on the hot path of a backend already fighting 2-20s Supabase-Korea
latency. Hence the short in-process cache.

`FMC_BANKS` in app/core/constants.py remains the seed for the migration
and the fallback if the table is somehow empty — a misconfigured or
half-migrated database degrades to the old hard-coded list rather than
rejecting every bank name in the CRM.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FMC_BANKS
from app.models.bank import Bank

logger = logging.getLogger(__name__)

# The list changes a few times a month at most; 60s of staleness is
# invisible to users and saves a query on every lead edit and grid load.
_TTL_S = 60.0
_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
# Rates are cached separately from names: `_load` projects only
# `Bank.name` and returns a tuple of strings, so a rate can't ride along
# without changing what every existing caller receives.
_rate_cache: tuple[float, dict[str, Decimal]] | None = None


def invalidate_bank_cache() -> None:
    """Call after any write so an admin sees their change immediately
    rather than waiting out the TTL."""
    global _rate_cache
    _cache.clear()
    _rate_cache = None


async def _load(db: AsyncSession, active_only: bool) -> tuple[str, ...]:
    key = "active" if active_only else "all"
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]

    query = select(Bank.name).order_by(Bank.sort_order, Bank.name)
    if active_only:
        query = query.where(Bank.is_active == True)  # noqa: E712
    names = tuple((await db.execute(query)).scalars().all())

    if not names:
        # Empty table means the migration hasn't run or something wiped
        # it. Falling back keeps the CRM usable instead of 400-ing every
        # bank name; loud so it gets noticed.
        logger.error(
            "BANK_REGISTRY: `banks` table is empty — falling back to the "
            "hard-coded FMC_BANKS constant. Run `alembic upgrade head`."
        )
        return tuple(FMC_BANKS)

    _cache[key] = (time.monotonic() + _TTL_S, names)
    return names


async def get_bank_names(db: AsyncSession) -> tuple[str, ...]:
    """Selectable lenders — what the dropdown offers and what validation
    accepts."""
    return await _load(db, active_only=True)


async def get_all_bank_names(db: AsyncSession) -> tuple[str, ...]:
    """Every lender ever on the list, including deactivated ones.

    Used for the grid's columns: a lender you have stopped working with
    still had real files go to it, and dropping its column would make
    that history invisible — the precise failure this list exists to
    avoid.
    """
    return await _load(db, active_only=False)


async def get_commission_rates(db: AsyncSession) -> dict[str, Decimal]:
    """Lender name -> commission percentage, for lenders that have one set.

    Keyed on the LOWERCASED name. `banks.name` is a controlled vocabulary
    with a case-insensitive unique index, but `lead_banks.bank_name` is
    unvalidated legacy free text — so a disbursement can carry "axis"
    where the list says "Axis". Matching case-sensitively would silently
    resolve those to no rate and price the commission at zero, which is
    exactly the kind of quiet wrong number this feature exists to stop.

    Lenders with no rate configured are ABSENT from the mapping rather
    than present with 0. A missing rate must be reported as missing, not
    billed as nothing.

    Includes deactivated lenders: a file disbursed under a lender you no
    longer work with still earns commission.
    """
    global _rate_cache
    if _rate_cache and time.monotonic() < _rate_cache[0]:
        return _rate_cache[1]

    rows = (await db.execute(
        select(Bank.name, Bank.commission_rate)
        .where(Bank.commission_rate.isnot(None))
    )).all()
    rates = {name.lower(): rate for name, rate in rows}
    _rate_cache = (time.monotonic() + _TTL_S, rates)
    return rates


async def get_commission_rate(db: AsyncSession, bank_name: str) -> Decimal | None:
    """The rate for one lender, or None when none is configured."""
    if not bank_name:
        return None
    return (await get_commission_rates(db)).get(bank_name.strip().lower())
