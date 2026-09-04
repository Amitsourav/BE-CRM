"""Aggregate views over the commission book — the analytics dashboard.

`CommissionService` answers "what is this row". This answers "how is the
book doing", which until now needed hand-written SQL: reconciling FMC's
CRM against its revenue tracker in September 2026 took two days of ad-hoc
queries because not one of these figures had a home.

Five questions, one round trip:

  1. how much of what lenders approved has actually come out
  2. how much is still to be drawn, and what it is worth
  3. are we collecting faster or slower than we are earning
  4. who owes us, and how old is it
  5. what in here cannot be trusted

Nothing new is captured. Every figure below already exists in
`bank_disbursements` and `lead_banks`; it was simply never aggregated.

INVOICING IS DELIBERATELY ABSENT. `invoice_service` never touches
`bank_disbursements`, so `invoice_id` is only ever set by a direct write
and the `billed` status is unreachable through the API. FMC raises its
bills outside the CRM. Reporting "unbilled" here would present a gap that
is really just a workflow living somewhere else.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal

from sqlalchemy import select, func, case, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import Bank
from app.models.bank_disbursement import BankDisbursement
from app.models.lead import Lead
from app.models.lead_bank import LeadBank

# A lender file only counts once the student has paid the processing fee.
# The revenue tracker states the rule outright and CommissionService
# already follows it; repeated here so the two cannot drift.
_CONFIRMED = ("pf_paid", "disbursed")
# Approved but not yet confirmed — reported separately, never summed in.
_SANCTIONED_ONLY = ("sanctioned",)
_LIVE = _CONFIRMED + _SANCTIONED_ONLY

# What counts as a real underpayment.
#
# BankDisbursement.SETTLEMENT_TOLERANCE is one rupee, which is right for a
# row-level status but useless in aggregate: on FMC's live book 65 of the
# 69 rows reading `short_paid` are short by under 2%, Rs 21,678 in total.
# That is rounding between what a lender computed and what we did, not
# debt. A dashboard announcing "69 lenders underpaid us" would be wrong
# and would be ignored inside a week.
#
# So the dashboard reports both: the raw count, and the count that is
# worth someone's afternoon. The row-level status is left alone — the
# existing reconciliation screen renders it and moving it would move
# numbers the frontend already shows.
_MATERIAL_FLOOR = Decimal("100")
_MATERIAL_PCT = Decimal("0.02")


# ── Cache ──────────────────────────────────────────────────────────────
# Nine round trips at ~350ms each to Supabase Korea is ~3s wall clock —
# latency, not query cost; every panel is a single indexed aggregate. The
# timing middleware warns above 1s, and a dashboard nobody edits inline
# has no business being recomputed on every glance.
#
# 60s rather than the Kanban's 15s: that cache exists so an edit shows up
# immediately, and here nothing is edited. Same shape as
# lead_service._kanban_cache so the eviction and key rules are familiar —
# company_id MUST stay element 0 of the key.
_CACHE_TTL_S = 60.0
_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_get(key):
    hit = _cache.get(key)
    if hit is None:
        return None
    expires_at, payload = hit
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return payload


def _cache_set(key, payload):
    if len(_cache) > 64:
        for k in list(_cache)[:16]:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic() + _CACHE_TTL_S, payload)


def invalidate_dashboard_cache_for_company(company_id) -> None:
    """Drop this tenant's cached dashboard. Safe to call when money moves."""
    for k in [k for k in _cache if k and k[0] == company_id]:
        _cache.pop(k, None)


def _pct(part, whole) -> float:
    """Share of `whole`, 1 dp. Zero when the denominator is zero."""
    p, w = float(part or 0), float(whole or 0)
    return round(p / w * 100, 1) if w else 0.0


class CommissionAnalyticsService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    def _live_leads(self):
        """Ids of leads that still exist. See CommissionService._live_leads.

        Leads are soft-deleted, so without this a removed student's money
        keeps arriving in every panel.
        """
        return select(Lead.id).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )

    # ── The funnel: approved -> confirmed -> released -> earned -> banked

    async def funnel(self) -> dict:
        """Where the money is between a lender saying yes and cash arriving.

        Two queries: sanctions live on `lead_banks`, released money on
        `bank_disbursements`, and they cannot be summed in one pass
        without a fan-out multiplying every sanction by its tranche count.
        """
        s = (await self.db.execute(
            select(
                func.coalesce(func.sum(case(
                    (LeadBank.bank_status.in_(_LIVE), LeadBank.loan_amount)
                )), 0),
                func.coalesce(func.sum(case(
                    (LeadBank.bank_status.in_(_CONFIRMED), LeadBank.loan_amount)
                )), 0),
                func.count().filter(LeadBank.bank_status.in_(_LIVE)),
                func.count().filter(LeadBank.bank_status.in_(_CONFIRMED)),
            ).where(
                LeadBank.company_id == self.company_id,
                LeadBank.loan_amount.isnot(None),
                LeadBank.lead_id.in_(self._live_leads()),
            )
        )).one()
        sanctioned, confirmed, n_live, n_confirmed = s

        d = (await self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
            ).where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
            )
        )).one()
        tranches, disbursed, earned, collected = d

        return {
            "sanctioned_total": sanctioned,
            "sanctioned_files": n_live,
            "confirmed_total": confirmed,
            "confirmed_files": n_confirmed,
            "disbursed_total": disbursed,
            "tranches": tranches,
            "earned_total": earned,
            "collected_total": collected,
            "outstanding_total": (earned or 0) - (collected or 0),
            # Each step as a share of the one before it. These are the
            # numbers worth watching: a low drawdown percentage is money
            # approved and never taken, which no other screen surfaces.
            "confirmed_pct_of_sanctioned": _pct(confirmed, sanctioned),
            "disbursed_pct_of_confirmed": _pct(disbursed, confirmed),
            "collected_pct_of_earned": _pct(collected, earned),
        }

    # ── What is still coming ───────────────────────────────────────────

    async def pipeline_ahead(self) -> dict:
        """Commission on money a lender has approved but not yet released.

        Education loans come out semester by semester, so a confirmed file
        keeps earning for years. This is the single most useful forward
        number in the system and it existed nowhere: on FMC's book only
        ~39% of confirmed sanctions have been drawn, leaving Rs 23.7 lakh
        of commission that arrives with no new business at all.

        Restricted to CONFIRMED files. A sanction the student never
        committed to is not a forecast, it is a hope.
        """
        drawn = (
            select(
                BankDisbursement.lead_bank_id.label("lb"),
                func.sum(BankDisbursement.disbursed_amount).label("drawn"),
            )
            .where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
            )
            .group_by(BankDisbursement.lead_bank_id)
            .subquery()
        )
        # Never negative: a file can be over-drawn against a stale
        # sanction figure, and a negative "still to come" is nonsense.
        remaining = func.greatest(
            LeadBank.loan_amount - func.coalesce(drawn.c.drawn, 0), 0
        )
        r = (await self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LeadBank.loan_amount), 0),
                func.coalesce(func.sum(func.coalesce(drawn.c.drawn, 0)), 0),
                func.coalesce(func.sum(remaining), 0),
                func.coalesce(
                    func.sum(remaining * LeadBank.commission_rate / 100), 0
                ),
                func.count().filter(LeadBank.commission_rate.is_(None)),
            )
            .select_from(LeadBank)
            .outerjoin(drawn, drawn.c.lb == LeadBank.id)
            .where(
                LeadBank.company_id == self.company_id,
                LeadBank.bank_status.in_(_CONFIRMED),
                LeadBank.loan_amount.isnot(None),
                LeadBank.lead_id.in_(self._live_leads()),
            )
        )).one()
        files, sanctioned, drawn_total, undrawn, future, no_rate = r
        return {
            "confirmed_files": files,
            "sanctioned_total": sanctioned,
            "drawn_total": drawn_total,
            "undrawn_total": undrawn,
            "future_commission": future,
            "drawn_pct": _pct(drawn_total, sanctioned),
            # Files whose future commission cannot be computed because the
            # lender has no rate. Excluded from `future_commission` rather
            # than counted as zero, so the forecast is a floor.
            "files_missing_rate": no_rate,
        }

    # ── Monthly: earning vs collecting ─────────────────────────────────

    async def monthly(self, months: int = 12) -> list[dict]:
        """Earned by disbursement month against collected by receipt month.

        Two different dates, so two grouped queries stitched on the month
        key rather than one join — a row earned in June and collected in
        August belongs to both months, in different columns.

        Rows with no date cannot sit in any month and are reported by
        `data_quality`, not silently dropped into the oldest bucket.
        """
        m_disb = func.date_trunc("month", BankDisbursement.disbursed_on)
        earned = (await self.db.execute(
            select(
                m_disb.label("m"),
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
            )
            .where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
                BankDisbursement.disbursed_on.isnot(None),
            )
            .group_by(m_disb)
        )).all()

        m_recd = func.date_trunc("month", BankDisbursement.received_on)
        got = (await self.db.execute(
            select(
                m_recd.label("m"),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
            )
            .where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
                BankDisbursement.received_on.isnot(None),
            )
            .group_by(m_recd)
        )).all()

        e = {r[0].date(): r for r in earned}
        g = {r[0].date(): r[1] for r in got}
        keys = sorted(set(e) | set(g))[-months:] if (e or g) else []
        return [
            {
                "month": k.strftime("%Y-%m"),
                "tranches": e[k][1] if k in e else 0,
                "disbursed": e[k][2] if k in e else Decimal("0"),
                "earned": e[k][3] if k in e else Decimal("0"),
                "collected": g.get(k, Decimal("0")),
            }
            for k in keys
        ]

    # ── Who owes us ────────────────────────────────────────────────────

    async def by_lender(self) -> list[dict]:
        """Per-lender: what they released, what they owe, what is still to draw.

        Ordered by what is owed, because that is the column someone acts
        on. A lender with nothing outstanding still appears — seeing that
        UC Axis is your biggest route matters even in a month it owes
        nothing.
        """
        rows = (await self.db.execute(
            select(
                BankDisbursement.bank_name,
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
                func.coalesce(func.sum(BankDisbursement.shortfall), 0),
            )
            .where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
            )
            .group_by(BankDisbursement.bank_name)
        )).all()
        return sorted(
            [
                {
                    "bank_name": r[0],
                    "tranches": r[1],
                    "disbursed_total": r[2],
                    "earned_total": r[3],
                    "collected_total": r[4],
                    "outstanding_total": r[5],
                    "collected_pct": _pct(r[4], r[3]),
                }
                for r in rows
            ],
            key=lambda x: (-float(x["outstanding_total"]), -float(x["disbursed_total"])),
        )

    # ── How old the debt is ────────────────────────────────────────────

    async def ageing(self) -> dict:
        """Outstanding commission bucketed by age, in SQL.

        Two departures from the row-level `days_outstanding`, which is
        computed per row in Python and so can never be grouped:

          * only rows still owing something appear. `days_outstanding`
            keeps counting on a settled row, which makes a paid book look
            like an ageing one.
          * rows with no disbursement date get their own bucket instead of
            being dropped. On FMC's book that bucket holds the MAJORITY of
            everything outstanding, and a report that quietly omitted it
            would understate the debt by more than half.
        """
        age = func.current_date() - BankDisbursement.disbursed_on
        bucket = case(
            (BankDisbursement.disbursed_on.is_(None), "no_date"),
            (age <= 30, "0_30"),
            (age <= 60, "31_60"),
            (age <= 90, "61_90"),
            else_="over_90",
        )
        rows = (await self.db.execute(
            select(
                bucket.label("b"),
                func.count(),
                func.coalesce(func.sum(BankDisbursement.shortfall), 0),
            )
            .where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
                BankDisbursement.shortfall > 0,
                BankDisbursement.write_off_reason.is_(None),
            )
            .group_by(bucket)
        )).all()
        found = {r[0]: (r[1], r[2]) for r in rows}
        order = ["0_30", "31_60", "61_90", "over_90", "no_date"]
        buckets = [
            {
                "bucket": b,
                "tranches": found.get(b, (0, Decimal("0")))[0],
                "outstanding": found.get(b, (0, Decimal("0")))[1],
            }
            for b in order
        ]
        total = sum((b["outstanding"] for b in buckets), Decimal("0"))
        return {
            "buckets": buckets,
            "total_outstanding": total,
            # Debt that cannot be aged at all. Called out separately
            # because it is the number that decides whether the rest of
            # this panel means anything.
            "undateable_outstanding": found.get("no_date", (0, Decimal("0")))[1],
            "undateable_pct": _pct(
                found.get("no_date", (0, Decimal("0")))[1], total
            ),
        }

    # ── What cannot be trusted ─────────────────────────────────────────

    async def data_quality(self) -> dict:
        """The counters that say why a figure above might be wrong.

        Every dashboard that hides these ends up trusted more than it
        deserves. FMC's own theoretical-revenue endpoint already reports
        its exclusions rather than counting them as zero; this does the
        same for the book as a whole.
        """
        d = (await self.db.execute(
            select(
                func.count(),
                func.count().filter(BankDisbursement.disbursed_on.is_(None)),
                func.count().filter(and_(
                    BankDisbursement.amount_received.isnot(None),
                    BankDisbursement.received_on.is_(None),
                )),
                func.count().filter(func.coalesce(
                    BankDisbursement.tds_deducted, 0) > 0),
                # Nothing has arrived at all. A different problem from an
                # underpayment and must not be counted as one — lumping
                # them together is what makes "124 lenders underpaid us"
                # out of a book where most rows simply have not been paid.
                func.count().filter(and_(
                    BankDisbursement.total_settled == 0,
                    BankDisbursement.total_due > 0,
                )),
                # Paid something, but short.
                func.count().filter(and_(
                    BankDisbursement.total_settled > 0,
                    BankDisbursement.shortfall > 0,
                )),
                # ...and short by enough to be worth an afternoon.
                # See _MATERIAL_FLOOR.
                func.count().filter(and_(
                    BankDisbursement.total_settled > 0,
                    BankDisbursement.shortfall > _MATERIAL_FLOOR,
                    BankDisbursement.shortfall
                    > BankDisbursement.total_due * _MATERIAL_PCT,
                )),
                func.count().filter(
                    BankDisbursement.write_off_reason.isnot(None)),
                func.count().filter(BankDisbursement.earns_commission.is_(False)),
            ).where(
                BankDisbursement.company_id == self.company_id,
                BankDisbursement.lead_id.in_(self._live_leads()),
            )
        )).one()

        f = (await self.db.execute(
            select(
                func.count(),
                func.count().filter(LeadBank.loan_amount.is_(None)),
                func.count().filter(or_(
                    Bank.is_aggregator.is_(True),
                    Bank.commission_rate.is_(None),
                )),
                func.count().filter(Bank.is_aggregator.is_(True)),
            )
            .select_from(LeadBank)
            .outerjoin(Bank, Bank.name == LeadBank.bank_name)
            .where(
                LeadBank.company_id == self.company_id,
                LeadBank.bank_status.in_(_LIVE),
                LeadBank.lead_id.in_(self._live_leads()),
            )
        )).one()

        return {
            "tranches": d[0],
            "tranches_without_date": d[1],
            "payments_without_receipt_date": d[2],
            "tranches_with_tds": d[3],
            "tranches_awaiting_payment": d[4],
            "tranches_short": d[5],
            "tranches_materially_short": d[6],
            "tranches_written_off": d[7],
            "tranches_earning_nothing": d[8],
            "live_files": f[0],
            "files_without_sanctioned_amount": f[1],
            "files_that_cannot_be_priced": f[2],
            # A file parked on UniCred/Nomad/Axis rather than the specific
            # route beneath it can never earn — an aggregator has no single
            # rate. These need moving before they are worth anything.
            "files_on_aggregator": f[3],
        }

    # ── Assembly ───────────────────────────────────────────────────────

    async def dashboard(self, months: int = 12) -> dict:
        """Everything above, one payload, six queries plus one.

        Sequential rather than gathered: they share the single
        request-scoped AsyncSession, which is not safe for concurrent use.
        Cached for 60s — see _CACHE_TTL_S for why.
        """
        key = (self.company_id, months)
        cached = _cache_get(key)
        if cached is not None:
            return cached
        payload = {
            "funnel": await self.funnel(),
            "pipeline_ahead": await self.pipeline_ahead(),
            "monthly": await self.monthly(months),
            "by_lender": await self.by_lender(),
            "ageing": await self.ageing(),
            "data_quality": await self.data_quality(),
        }
        _cache_set(key, payload)
        return payload
