"""Aggregate views over the commission book — the loan intelligence dashboard.

`CommissionService` answers "what is this row". This answers "how is the
book doing", which until now needed hand-written SQL: reconciling FMC's
CRM against its revenue tracker in September 2026 took two days of ad-hoc
queries because not one of these figures had a home.

The overview panels:

  funnel          approved -> PF confirmed -> disbursed -> earned -> collected
  pipeline_ahead  commission on money approved but not yet released
  monthly         earned by disbursement month vs collected by receipt month
  by_lender       who owes what, biggest debt first
  ageing          outstanding by age, with an explicit no-date bucket
  data_quality    the counters that say why a figure might be wrong

and the operating layer:

  pipeline        stage funnel by value, revenue bridge, biggest opportunities
  sources         which channels actually produce revenue
  exceptions      the register of records to fix, each naming a student
  drilldown       any segment -> the students inside it

Nothing new is captured. Every figure already exists in
`bank_disbursements` and `lead_banks`; it was simply never aggregated.

INVOICING IS DELIBERATELY ABSENT. `invoice_service` never touches
`bank_disbursements`, so `invoice_id` is only ever set by a direct write
and the `billed` status is unreachable through the API. FMC raises its
bills outside the CRM. Reporting "unbilled" here would present a gap that
is really a workflow living somewhere else.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func, case, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import LEAD_STAGE_VALUES
from app.core.exceptions import BadRequestError
from app.models.bank import Bank
from app.models.bank_disbursement import BankDisbursement
from app.models.lead import Lead
from app.models.lead_bank import LeadBank
from app.models.lead_source import LeadSource

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
# row-level status but useless in aggregate: on FMC's live book 71 rows
# read short and only 4 are short by more than this. The rest is rounding
# between what a lender computed and what we did. A dashboard announcing
# "71 lenders underpaid us" would be wrong and ignored inside a week.
_MATERIAL_FLOOR = Decimal("100")
_MATERIAL_PCT = Decimal("0.02")

# The net-theoretical haircut, mirroring invoice_settings.net_theoretical_factor.
# Used only for the forward "potential net revenue" on the opportunities
# queue — a gross figure there would overstate what a drawdown is worth.
_NET_FACTOR = Decimal("0.80")

# The stages the stage funnel draws. Everything else lands in its `other`
# bucket, which the drill-down has to be able to reopen.
_STAGE_FUNNEL_STAGES = ("logged_in", "sanctioned", "pf_paid", "disbursed")
_LEAD_STAGES = frozenset(LEAD_STAGE_VALUES)


@dataclass
class Filters:
    """One filter set, honoured identically by every panel.

    Defined once and splatted into each query rather than re-expressed per
    endpoint. The GST bug that made two endpoints disagree about what a
    lender owed came from one formula living in two places; a filter set
    living in six would be that mistake at six times the size.

    Every field is optional and an empty Filters() means "the whole book",
    so an unfiltered call behaves exactly as it did before filters existed.
    """
    bank_name: list[str] = dc_field(default_factory=list)
    source_id: list[uuid.UUID] = dc_field(default_factory=list)
    disbursed_from: date | None = None
    disbursed_to: date | None = None

    def cache_key(self) -> tuple:
        return (
            tuple(sorted(self.bank_name)),
            tuple(sorted(str(s) for s in self.source_id)),
            self.disbursed_from, self.disbursed_to,
        )


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
    """Drop this tenant's cached panels. Safe to call when money moves."""
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

    # ── Scoping ────────────────────────────────────────────────────────

    def _lead_scope(self, f: Filters | None = None):
        """Ids of the leads this call is allowed to see.

        Two jobs in one subquery. It excludes soft-deleted leads — they
        are SOFT-deleted, so the CASCADE on bank_disbursements never fires
        and a removed student's money went on counting in every total. And
        it applies the lead-side filter (source), so a source filter
        reaches panels built on `bank_disbursements` without any of them
        needing their own join to `leads`.
        """
        q = select(Lead.id).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if f and f.source_id:
            q = q.where(Lead.lead_source_id.in_(f.source_id))
        return q

    def _disb_where(self, f: Filters | None = None) -> list:
        """Criteria for any query over bank_disbursements."""
        w = [
            BankDisbursement.company_id == self.company_id,
            BankDisbursement.lead_id.in_(self._lead_scope(f)),
        ]
        if f:
            if f.bank_name:
                w.append(BankDisbursement.bank_name.in_(f.bank_name))
            if f.disbursed_from:
                w.append(BankDisbursement.disbursed_on >= f.disbursed_from)
            if f.disbursed_to:
                w.append(BankDisbursement.disbursed_on <= f.disbursed_to)
        return w

    def _file_where(self, f: Filters | None = None) -> list:
        """Criteria for any query over lead_banks.

        The disbursement date filters are deliberately NOT applied here. A
        lender file has no disbursement date of its own, and dropping
        files whose money fell outside the window would leave sanctioned
        and disbursed describing different sets of students.
        """
        w = [
            LeadBank.company_id == self.company_id,
            LeadBank.lead_id.in_(self._lead_scope(f)),
        ]
        if f and f.bank_name:
            w.append(LeadBank.bank_name.in_(f.bank_name))
        return w

    def _drawn_subq(self, f: Filters | None = None):
        """Tranche total per lender file. Every drawdown figure reads this."""
        return (
            select(
                BankDisbursement.lead_bank_id.label("lb"),
                func.sum(BankDisbursement.disbursed_amount).label("drawn"),
            )
            .where(*self._disb_where(f))
            .group_by(BankDisbursement.lead_bank_id)
            .subquery()
        )

    # ── The funnel ─────────────────────────────────────────────────────

    async def funnel(self, f: Filters | None = None) -> dict:
        """Where the money is between a lender saying yes and cash arriving.

        Two queries: sanctions live on `lead_banks`, released money on
        `bank_disbursements`, and they cannot be summed in one pass
        without a fan-out multiplying every sanction by its tranche count.
        """
        # The file COUNT covers every live file; the VALUE can only cover
        # the ones carrying an amount. Filtering both together — which
        # this did until 2026-09-05 — silently dropped 40 files from the
        # count, so the funnel said "134 files" while the exception
        # register said 40 live files have no sanctioned amount, and the
        # two could not be reconciled by anyone reading them.
        priced = LeadBank.loan_amount.isnot(None)
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
                func.count().filter(and_(
                    LeadBank.bank_status.in_(_LIVE), ~priced)),
                func.count().filter(and_(
                    LeadBank.bank_status.in_(_CONFIRMED), ~priced)),
            ).where(*self._file_where(f))
        )).one()
        (sanctioned, confirmed, n_live, n_confirmed,
         n_live_unpriced, n_confirmed_unpriced) = s

        d = (await self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
                # Row by row, never earned-minus-collected: one lender
                # overpaying must not cancel another's debt. Prit Jain's
                # Credila row is ₹280.45 over, which used to shrink the
                # whole book's outstanding by exactly that much and put
                # this panel ₹280.45 below `ageing` and `by_lender`.
                func.coalesce(func.sum(BankDisbursement.shortfall), 0),
                # Commission alone, without GST — the base a payout is
                # taken out of.
                func.coalesce(func.sum(BankDisbursement.commission_amount), 0),
            ).where(*self._disb_where(f))
        )).one()
        tranches, disbursed, earned, collected, outstanding, commission = d

        # Payout lives on the FILE, not the tranche — it is agreed on the
        # deal and computed off the sanction. A third query rather than a
        # join, because joining files to tranches fans every sanction out
        # by its tranche count. Every revenue figure was GROSS until this
        # existed, which is what the tracker's month tabs corrected by
        # hand.
        p = (await self.db.execute(
            select(
                func.coalesce(func.sum(LeadBank.payout_due), 0),
                func.coalesce(func.sum(LeadBank.payout_paid), 0),
            ).where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE))
        )).one()
        payout_due, payout_paid = p

        return {
            "sanctioned_total": sanctioned,
            "sanctioned_files": n_live,
            # Live files carrying no amount. `sanctioned_total` cannot
            # include them, so this is how much of the funnel's head is
            # unmeasured rather than zero.
            "sanctioned_files_unpriced": n_live_unpriced,
            "confirmed_total": confirmed,
            "confirmed_files": n_confirmed,
            "confirmed_files_unpriced": n_confirmed_unpriced,
            "disbursed_total": disbursed,
            "tranches": tranches,
            "earned_total": earned,
            "collected_total": collected,
            "outstanding_total": outstanding,
            # Commission ex-GST, what is shared away out of it, and what
            # is actually kept. `net_commission_total` is the only one of
            # the three that is FMC's own money.
            "commission_total": commission,
            "payout_due_total": payout_due,
            "payout_paid_total": payout_paid,
            "payout_outstanding_total": (payout_due or 0) - (payout_paid or 0),
            "net_commission_total": (commission or 0) - (payout_due or 0),
            # Each step as a share of the one before it. A low drawdown
            # percentage is money approved and never taken, which no other
            # screen surfaces.
            "confirmed_pct_of_sanctioned": _pct(confirmed, sanctioned),
            "disbursed_pct_of_confirmed": _pct(disbursed, confirmed),
            "collected_pct_of_earned": _pct(collected, earned),
        }

    # ── What is still coming ───────────────────────────────────────────

    async def pipeline_ahead(self, f: Filters | None = None) -> dict:
        """Commission on money a lender has approved but not yet released.

        Education loans come out semester by semester, so a confirmed file
        keeps earning for years. This is the most useful forward number in
        the system and it existed nowhere: only ~38% of confirmed
        sanctions have been drawn, leaving lakhs of commission that
        arrives with no new business at all.

        Restricted to CONFIRMED files. A sanction the student never
        committed to is not a forecast, it is a hope.
        """
        drawn = self._drawn_subq(f)
        drawn_amt = func.coalesce(drawn.c.drawn, 0)
        # Never negative: a file can be over-drawn against a stale
        # sanction figure, and a negative "still to come" is nonsense.
        # A file with no sanction figure yields no remaining at all —
        # unknown is not zero, and inventing a ceiling from what has
        # already been drawn would forecast revenue nobody promised.
        remaining = case(
            (LeadBank.loan_amount.is_(None), 0),
            else_=func.greatest(LeadBank.loan_amount - drawn_amt, 0),
        )
        unpriced = LeadBank.loan_amount.is_(None)
        r = (await self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LeadBank.loan_amount), 0),
                func.coalesce(func.sum(drawn_amt), 0),
                func.coalesce(func.sum(case((unpriced, drawn_amt), else_=0)), 0),
                func.coalesce(func.sum(remaining), 0),
                func.coalesce(
                    func.sum(remaining * LeadBank.commission_rate / 100), 0
                ),
                func.count().filter(LeadBank.commission_rate.is_(None)),
                func.count().filter(unpriced),
            )
            .select_from(LeadBank)
            .outerjoin(drawn, drawn.c.lb == LeadBank.id)
            .where(
                *self._file_where(f),
                LeadBank.bank_status.in_(_CONFIRMED),
            )
        )).one()
        (files, sanctioned, drawn_total, drawn_unpriced,
         undrawn, future, no_rate, no_sanction) = r
        return {
            "confirmed_files": files,
            "sanctioned_total": sanctioned,
            # Every rupee released against a confirmed file, so this ties
            # to the book. Excluding unpriced files — which it did until
            # 2026-09-05 — hid ₹26.95 L of real drawdown here and added
            # the same amount to `undrawn_total`, overstating the
            # forecast twice over.
            "drawn_total": drawn_total,
            # Of that, money on files with no sanction figure. It cannot
            # be set against a ceiling, so it is out of `drawn_pct`.
            "drawn_unpriced_total": drawn_unpriced,
            "undrawn_total": undrawn,
            "future_commission": future,
            "drawn_pct": _pct(drawn_total - drawn_unpriced, sanctioned),
            # Files whose future commission cannot be computed because the
            # lender has no rate. Excluded from `future_commission` rather
            # than counted as zero, so the forecast is a floor.
            "files_missing_rate": no_rate,
            # ...and files with no sanctioned amount, which are out of
            # the forecast for the same reason.
            "files_missing_sanction": no_sanction,
        }

    # ── Monthly: earning vs collecting ─────────────────────────────────

    async def monthly(self, months: int = 12, f: Filters | None = None) -> list[dict]:
        """Earned by disbursement month against collected by receipt month.

        Two different dates, so two grouped queries stitched on the month
        key rather than one join — a row earned in June and collected in
        August belongs to both months, in different columns.

        Rows with no date cannot sit in any month and are reported by
        `data_quality`, not silently dropped into the oldest bucket.

        A date filter selects WHICH TRANCHES, and does not clip the month
        axis. Filter to "disbursed on or before 30 June" and July still
        appears, carrying zero earned and real collected — June money that
        arrived in July. That is the honest answer, and clipping would
        hide the useful question underneath it: how long after disbursement
        does a lender actually pay? The frontend has to say so, because a
        June filter showing an August bar looks like a bug otherwise.
        """
        m_disb = func.date_trunc("month", BankDisbursement.disbursed_on)
        earned = (await self.db.execute(
            select(
                m_disb.label("m"),
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
            )
            .where(*self._disb_where(f), BankDisbursement.disbursed_on.isnot(None))
            .group_by(m_disb)
        )).all()

        m_recd = func.date_trunc("month", BankDisbursement.received_on)
        got = (await self.db.execute(
            select(
                m_recd.label("m"),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
            )
            .where(*self._disb_where(f), BankDisbursement.received_on.isnot(None))
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

    async def by_lender(self, f: Filters | None = None) -> list[dict]:
        """Per-lender: what they released, what they owe, how well they pay.

        Ordered by what is owed, because that is the column someone acts
        on. A lender with nothing outstanding still appears — seeing that
        UC Axis is the biggest route matters even in a month it owes
        nothing. `share_of_disbursed_pct` is what portfolio mix and
        concentration risk both read off.
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
            .where(*self._disb_where(f))
            .group_by(BankDisbursement.bank_name)
        )).all()
        out = [
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
        ]
        total = sum((float(x["disbursed_total"]) for x in out), 0.0)
        for x in out:
            x["share_of_disbursed_pct"] = _pct(x["disbursed_total"], total)
        return sorted(
            out,
            key=lambda x: (-float(x["outstanding_total"]), -float(x["disbursed_total"])),
        )

    # ── How old the debt is ────────────────────────────────────────────

    async def ageing(self, f: Filters | None = None) -> dict:
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
                *self._disb_where(f),
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
        undateable = found.get("no_date", (0, Decimal("0")))[1]
        return {
            "buckets": buckets,
            "total_outstanding": total,
            # Debt that cannot be aged at all. Called out separately
            # because it decides whether the rest of this panel means
            # anything.
            "undateable_outstanding": undateable,
            "undateable_pct": _pct(undateable, total),
        }

    # ── What cannot be trusted ─────────────────────────────────────────

    async def data_quality(self, f: Filters | None = None) -> dict:
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
                # them together is what turns a book that mostly has not
                # been paid yet into "124 lenders underpaid us".
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
                func.count().filter(and_(
                    BankDisbursement.total_settled > 0,
                    BankDisbursement.shortfall > _MATERIAL_FLOOR,
                    BankDisbursement.shortfall
                    > BankDisbursement.total_due * _MATERIAL_PCT,
                )),
                func.count().filter(
                    BankDisbursement.write_off_reason.isnot(None)),
                func.count().filter(BankDisbursement.earns_commission.is_(False)),
                # Dated, but the date was DERIVED from an invoice or a
                # month rather than recorded. They age and they appear in
                # monthly totals, so the count has to travel with them —
                # otherwise a recovered figure is indistinguishable from a
                # captured one, which is the whole reason blanks were
                # safer than guesses.
                func.count().filter(
                    BankDisbursement.disbursed_on_estimated.is_(True)),
            ).where(*self._disb_where(f))
        )).one()

        fq = (await self.db.execute(
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
            .where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE))
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
            "tranches_with_estimated_date": d[9],
            "live_files": fq[0],
            "files_without_sanctioned_amount": fq[1],
            "files_that_cannot_be_priced": fq[2],
            # A file parked on UniCred/Nomad/Axis rather than the specific
            # route beneath it can never earn — an aggregator has no single
            # rate. These need moving before they are worth anything.
            "files_on_aggregator": fq[3],
        }

    # ── Pipeline & forecast ────────────────────────────────────────────

    async def pipeline(self, f: Filters | None = None, limit: int = 20) -> dict:
        """Stage funnel by value, the revenue bridge, and what to chase.

        The stage funnel groups by LEAD stage, not lender-file status: the
        question is where a STUDENT sits, and a student with three lender
        files sits in exactly one place.
        """
        # The four loan stages, plus an explicit "other" catching every
        # remaining stage. Without it the funnel silently dropped Rs 1.41cr
        # of disbursement and Rs 4.86cr of sanctions belonging to students
        # parked at created / dnp / contacted / lost — money that exists,
        # on a stage the funnel does not draw. A funnel that does not sum
        # to the book is a funnel people stop believing.
        stages = _STAGE_FUNNEL_STAGES
        sanc = (
            select(
                LeadBank.lead_id.label("lid"),
                func.sum(LeadBank.loan_amount).label("sanc"),
            )
            .where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE))
            .group_by(LeadBank.lead_id)
            .subquery()
        )
        disb = (
            select(
                BankDisbursement.lead_id.label("lid"),
                func.sum(BankDisbursement.disbursed_amount).label("disb"),
            )
            .where(*self._disb_where(f))
            .group_by(BankDisbursement.lead_id)
            .subquery()
        )
        rows = (await self.db.execute(
            select(
                Lead.current_stage,
                func.count(),
                func.coalesce(func.sum(func.coalesce(sanc.c.sanc, 0)), 0),
                func.coalesce(func.sum(func.coalesce(disb.c.disb, 0)), 0),
            )
            .select_from(Lead)
            .outerjoin(sanc, sanc.c.lid == Lead.id)
            .outerjoin(disb, disb.c.lid == Lead.id)
            .where(Lead.id.in_(self._lead_scope(f)))
            .group_by(Lead.current_stage)
        )).all()
        found = {str(r[0]): r for r in rows}
        stage_funnel = [
            {
                "stage": st,
                "leads": found[st][1] if st in found else 0,
                "sanctioned": found[st][2] if st in found else Decimal("0"),
                "disbursed": found[st][3] if st in found else Decimal("0"),
            }
            for st in stages
        ]
        other = [r for k, r in found.items() if k not in stages]
        stage_funnel.append({
            "stage": "other",
            "leads": sum(r[1] for r in other),
            "sanctioned": sum((r[2] for r in other), Decimal("0")),
            "disbursed": sum((r[3] for r in other), Decimal("0")),
        })

        ahead = await self.pipeline_ahead(f)
        # Commission WITHOUT GST. The bridge sets what we have earned
        # against what we can still earn, and the right-hand side is
        # rate x remaining, which carries no GST — billing GST on top is
        # not revenue, it is money collected for the government. Until
        # 2026-09-05 this side was GST-inclusive, so the bridge added
        # Rs 2.99 L of tax to earnings and compared it against a
        # tax-free forecast.
        b = (await self.db.execute(
            select(
                func.coalesce(func.sum(BankDisbursement.commission_amount), 0),
                func.coalesce(func.sum(BankDisbursement.gst_amount), 0),
            ).where(*self._disb_where(f))
        )).one()
        booked, booked_gst = b
        payout = (await self.db.execute(
            select(func.coalesce(func.sum(LeadBank.payout_due), 0))
            .where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE))
        )).scalar()

        # Biggest opportunities: confirmed files with money still to draw,
        # ranked by what that money is worth to FMC after the net haircut.
        drawn = self._drawn_subq(f)
        pending = LeadBank.loan_amount - func.coalesce(drawn.c.drawn, 0)
        opp = (await self.db.execute(
            select(
                Lead.id, Lead.serial_no, Lead.full_name, Lead.current_stage,
                LeadBank.bank_name, LeadBank.loan_amount,
                func.coalesce(drawn.c.drawn, 0),
                pending,
                pending * LeadBank.commission_rate / 100 * _NET_FACTOR,
            )
            .select_from(LeadBank)
            .join(Lead, Lead.id == LeadBank.lead_id)
            .outerjoin(drawn, drawn.c.lb == LeadBank.id)
            .where(
                *self._file_where(f),
                LeadBank.bank_status.in_(_CONFIRMED),
                LeadBank.loan_amount.isnot(None),
                LeadBank.commission_rate.isnot(None),
                pending > 0,
            )
            .order_by((pending * LeadBank.commission_rate).desc())
            .limit(limit)
        )).all()

        return {
            "stage_funnel": stage_funnel,
            "revenue_bridge": {
                # Earned on money that has already come out.
                "booked": booked,
                # Charged on top of it. Shown so the bridge still ties to
                # `funnel.earned_total`, which is booked + booked_gst.
                "booked_gst": booked_gst,
                # Booked commission that goes back out to whoever
                # supplied the lead, and what is left after it. `kept` is
                # the number to put in front of anyone asking what the
                # business earned.
                "shared_away": payout,
                "kept": (booked or 0) - (payout or 0),
                # Earnable on money approved and still to come. A floor —
                # files with no lender rate are excluded, not zeroed.
                "unlockable": ahead["future_commission"],
                # The same forecast after the 80% haircut, which is the
                # basis `opportunities[].potential_net_revenue` uses. Both
                # appear on this panel, so both are stated.
                "unlockable_net": (
                    ahead["future_commission"] * _NET_FACTOR
                ).quantize(Decimal("0.01")),
                "undrawn_total": ahead["undrawn_total"],
                "drawn_pct": ahead["drawn_pct"],
                "files_missing_rate": ahead["files_missing_rate"],
                "files_missing_sanction": ahead["files_missing_sanction"],
            },
            "opportunities": [
                {
                    "lead_id": r[0], "serial_no": r[1], "full_name": r[2],
                    "stage": str(r[3]), "bank_name": r[4],
                    "sanctioned": r[5], "disbursed": r[6],
                    "pending": r[7], "potential_net_revenue": r[8],
                }
                for r in opp
            ],
        }

    # ── Source economics ───────────────────────────────────────────────

    async def sources(self, f: Filters | None = None) -> dict:
        """Which channels produce revenue, not which produce leads.

        `students` counts only students who have actually disbursed. The
        naive count — every lead carrying the source — makes unattributed
        read as 8,651 students earning Rs 83 each, which is not a number
        anyone can act on.

        Unattributed is returned SEPARATELY rather than ranked among real
        channels. On FMC's book it is the single largest bucket — 43% of
        commission — and letting it head a league table of marketing
        channels would be actively misleading.
        """
        rows = (await self.db.execute(
            select(
                LeadSource.id,
                LeadSource.name,
                func.count(func.distinct(BankDisbursement.lead_id)),
                func.count(),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.commission_amount), 0),
                func.coalesce(func.sum(BankDisbursement.total_settled), 0),
                func.coalesce(func.sum(BankDisbursement.total_due), 0),
            )
            .select_from(BankDisbursement)
            .join(Lead, Lead.id == BankDisbursement.lead_id)
            .outerjoin(LeadSource, LeadSource.id == Lead.lead_source_id)
            .where(*self._disb_where(f))
            .group_by(LeadSource.id, LeadSource.name)
        )).all()

        def shape(r):
            students = r[2] or 0
            comm = r[5] or Decimal("0")      # commission, EX GST
            earned = r[7] or Decimal("0")    # commission + GST
            return {
                "source_id": r[0],
                "source_name": r[1] if r[0] else "Unattributed",
                "students": students,
                "tranches": r[3],
                "disbursed_total": r[4],
                "commission_total": comm,
                # Same definition by_lender.earned_total uses. Both are
                # returned because the two answer different questions —
                # commission is what FMC earns, earned is what the lender
                # is billed — but a dashboard where "revenue" silently
                # means commission on one tab and commission+GST on
                # another is a dashboard nobody can reconcile.
                "earned_total": earned,
                "collected_total": r[6],
                "revenue_per_student": (
                    (comm / students).quantize(Decimal("0.01"))
                    if students else Decimal("0")
                ),
                "collected_pct": _pct(r[6], earned),
            }

        attributed = sorted(
            (shape(r) for r in rows if r[0] is not None),
            key=lambda x: -float(x["disbursed_total"]),
        )
        unattributed = next((shape(r) for r in rows if r[0] is None), None)
        total = sum((float(x["disbursed_total"]) for x in attributed), 0.0)
        total += float(unattributed["disbursed_total"]) if unattributed else 0.0
        for x in attributed:
            x["share_of_disbursed_pct"] = _pct(x["disbursed_total"], total)
        if unattributed:
            unattributed["share_of_disbursed_pct"] = _pct(
                unattributed["disbursed_total"], total
            )
        return {"sources": attributed, "unattributed": unattributed}

    # ── Exception register ─────────────────────────────────────────────

    # (severity, code, label, why it matters)
    _EXCEPTIONS = (
        ("high", "on_aggregator", "File sits on an aggregator",
         "UniCred, Nomad and Axis front several lenders at different rates, "
         "so they carry no rate of their own. This file can never earn "
         "until it is moved to the specific route beneath it."),
        ("high", "no_sanctioned_amount", "Live file with no sanctioned amount",
         "Excluded from sanctioned value and from the forward forecast, so "
         "both read lower than the truth."),
        ("medium", "no_disbursement_date", "Tranche with no disbursement date",
         "Falls out of every monthly figure and cannot be aged, so the debt "
         "looks younger and the trend thinner than they are."),
        ("medium", "no_receipt_date", "Payment with no receipt date",
         "The money is counted but the month it arrived is unknown, so the "
         "collection trend understates every month."),
        ("high", "money_on_prestage", "Money disbursed, lead still at an early stage",
         "The bank has released funds but the lead never moved to "
         "Disbursed — it still sits at created, contacted, dnp or lost. It "
         "is invisible on the pipeline board, drops out of the stage "
         "funnel, and nobody is chasing the remaining tranches."),
        ("low", "estimated_disbursement_date", "Disbursement date is an estimate",
         "The tracker never recorded this date. It was derived from the "
         "invoice date, the receipt or the month, so it is the LATEST the "
         "money could have moved — the row ages younger than it really is. "
         "Replace it when the lender confirms."),
        ("low", "materially_short", "Lender paid materially less than owed",
         "Short by more than Rs 100 AND more than 2% — beyond rounding, so "
         "worth querying with the lender."),
    )
    _EX = {c: (s, lbl, why) for s, c, lbl, why in _EXCEPTIONS}

    async def exceptions(self, f: Filters | None = None, limit: int = 200) -> dict:
        """The register: one row per problem, each naming the student.

        `data_quality` counts these; this lists them so someone can act.
        Ordered by severity then by the money at stake, because a register
        nobody can triage is a register nobody reads.
        """
        out: list[dict] = []

        files = (await self.db.execute(
            select(
                Lead.id, Lead.serial_no, Lead.full_name,
                LeadBank.bank_name, LeadBank.loan_amount, Bank.is_aggregator,
            )
            .select_from(LeadBank)
            .join(Lead, Lead.id == LeadBank.lead_id)
            .outerjoin(Bank, Bank.name == LeadBank.bank_name)
            .where(
                *self._file_where(f),
                LeadBank.bank_status.in_(_LIVE),
                or_(Bank.is_aggregator.is_(True), LeadBank.loan_amount.is_(None)),
            )
        )).all()
        for r in files:
            # A file can trip both rules at once — parked on an aggregator
            # AND missing its sanctioned amount — and they are two separate
            # things to fix, so each becomes its own row. Emitting only the
            # first would make the register disagree with the counters in
            # data_quality, which is the pair most likely to be compared.
            for flag, code in ((r[5], "on_aggregator"),
                               (r[4] is None, "no_sanctioned_amount")):
                if not flag:
                    continue
                sev, label, why = self._EX[code]
                out.append({
                    "severity": sev, "code": code, "issue": label, "why": why,
                    "lead_id": r[0], "serial_no": r[1], "full_name": r[2],
                    "bank_name": r[3], "amount": r[4],
                })

        no_date = BankDisbursement.disbursed_on.is_(None)
        no_receipt = and_(
            BankDisbursement.amount_received.isnot(None),
            BankDisbursement.received_on.is_(None),
        )
        short = and_(
            BankDisbursement.total_settled > 0,
            BankDisbursement.shortfall > _MATERIAL_FLOOR,
            BankDisbursement.shortfall
            > BankDisbursement.total_due * _MATERIAL_PCT,
        )
        tr = (await self.db.execute(
            select(
                Lead.id, Lead.serial_no, Lead.full_name,
                BankDisbursement.bank_name, BankDisbursement.disbursed_amount,
                no_date, no_receipt, short, BankDisbursement.shortfall,
                BankDisbursement.disbursed_on_estimated,
            )
            .select_from(BankDisbursement)
            .join(Lead, Lead.id == BankDisbursement.lead_id)
            .where(*self._disb_where(f), or_(
                no_date, no_receipt, short,
                BankDisbursement.disbursed_on_estimated.is_(True),
            ))
        )).all()
        for r in tr:
            # One tranche can trip more than one rule, and each is a
            # separate thing to fix, so each becomes its own row.
            for flag, code in ((r[5], "no_disbursement_date"),
                               (r[6], "no_receipt_date"),
                               (r[7], "materially_short"),
                               (r[9], "estimated_disbursement_date")):
                if not flag:
                    continue
                sev, label, why = self._EX[code]
                out.append({
                    "severity": sev, "code": code, "issue": label, "why": why,
                    "lead_id": r[0], "serial_no": r[1], "full_name": r[2],
                    "bank_name": r[3],
                    "amount": r[8] if code == "materially_short" else r[4],
                })

        # Students holding real disbursement while parked at a pre-loan
        # stage. Found by the September audit: Rs 1.41cr of released money
        # sits on leads at created / dnp / contacted / lost, including
        # Kajal Saxena (Rs 31.4L) and Sayan Nandy (Rs 27.4L) both still
        # reading "created". The money is counted everywhere except the
        # one place a human looks — the pipeline board.
        stray = (await self.db.execute(
            select(
                Lead.id, Lead.serial_no, Lead.full_name,
                Lead.current_stage, Lead.bank_name,
                func.sum(BankDisbursement.disbursed_amount),
            )
            .select_from(BankDisbursement)
            .join(Lead, Lead.id == BankDisbursement.lead_id)
            .where(
                *self._disb_where(f),
                Lead.current_stage.notin_(
                    ("disbursed", "pf_paid", "sanctioned", "logged_in")
                ),
            )
            .group_by(Lead.id, Lead.serial_no, Lead.full_name,
                      Lead.current_stage, Lead.bank_name)
        )).all()
        sev, label, why = self._EX["money_on_prestage"]
        for r in stray:
            out.append({
                "severity": sev, "code": "money_on_prestage",
                "issue": label,
                "why": f"{why} This lead reads '{r[3]}'.",
                "lead_id": r[0], "serial_no": r[1], "full_name": r[2],
                "bank_name": r[4], "amount": r[5],
            })

        rank = {"high": 0, "medium": 1, "low": 2}
        out.sort(key=lambda x: (rank[x["severity"]], -float(x["amount"] or 0)))
        counts: dict[str, int] = {}
        for x in out:
            counts[x["code"]] = counts.get(x["code"], 0) + 1
        return {
            "total": len(out),
            "by_code": counts,
            "items": out[:limit],
            "truncated": len(out) > limit,
        }

    # ── Drill-down ─────────────────────────────────────────────────────

    _SEGMENTS = ("stage", "lender", "ageing_bucket", "source",
                 "funnel_step", "exception")

    async def drilldown(
        self, segment: str, value: str, f: Filters | None = None,
        page: int = 1, page_size: int = 50,
    ) -> dict:
        """Any segment on the dashboard -> the students inside it.

        One endpoint rather than a drill-down per panel: the answer has
        the same shape every time, and six near-copies would drift the way
        the outstanding formula did.

        The row count MUST equal the number its segment advertised, and so
        must the money on the rows. A clickable segment that opens a
        different set of students — or the same students carrying larger
        figures — is worse than no drill-down at all.
        """
        if segment not in self._SEGMENTS:
            raise BadRequestError(
                f"segment must be one of {list(self._SEGMENTS)} (got '{segment}')."
            )

        # Built first, because the money columns have to be narrowed to the
        # same slice. Until 2026-09-05 they were not: every row carried the
        # student's ENTIRE outstanding whatever bucket had been clicked, so
        # a student with tranches in two ageing buckets was counted in full
        # in both and the five buckets summed to Rs 7.97 L against a book
        # of Rs 7.03 L.
        lead_where: list = []      # which students belong in this segment
        tranche_extra: list = []   # which of their tranches the segment covers
        file_extra: list = []      # ...and which of their lender files

        if segment == "stage":
            # `other` is the stage funnel's own synthetic bucket — every
            # stage it does not draw. It is a clickable segment like any
            # other, and until 2026-09-05 clicking it reached Postgres as
            # a lead_stage enum literal and returned a raw 500.
            if value == "other":
                lead_where.append(Lead.current_stage.notin_(_STAGE_FUNNEL_STAGES))
            elif value not in _LEAD_STAGES:
                raise BadRequestError(
                    f"'{value}' is not a lead stage. Use one of "
                    f"{sorted(_LEAD_STAGES)} or 'other'."
                )
            else:
                lead_where.append(Lead.current_stage == value)

        elif segment == "funnel_step":
            step = {
                "sanctioned": _LIVE,
                "confirmed": _CONFIRMED,
                "disbursed": ("disbursed",),
            }.get(value)
            if not step:
                raise BadRequestError(
                    "funnel_step must be sanctioned | confirmed | disbursed."
                )
            file_extra = [LeadBank.bank_status.in_(step)]
            lead_where.append(Lead.id.in_(
                select(LeadBank.lead_id).where(*self._file_where(f), *file_extra)
            ))

        elif segment == "lender":
            tranche_extra = [BankDisbursement.bank_name == value]
            file_extra = [LeadBank.bank_name == value]
            lead_where.append(Lead.id.in_(
                select(BankDisbursement.lead_id).where(
                    *self._disb_where(f), *tranche_extra
                )
            ))

        elif segment == "source":
            unattributed = value in ("", "unattributed", "none")
            lead_where += [
                Lead.lead_source_id.is_(None) if unattributed
                else Lead.lead_source_id == uuid.UUID(value),
                Lead.id.in_(select(BankDisbursement.lead_id)
                            .where(*self._disb_where(f))),
            ]

        elif segment == "ageing_bucket":
            age = func.current_date() - BankDisbursement.disbursed_on
            cond = {
                "no_date": BankDisbursement.disbursed_on.is_(None),
                "0_30": and_(BankDisbursement.disbursed_on.isnot(None), age <= 30),
                "31_60": and_(age > 30, age <= 60),
                "61_90": and_(age > 60, age <= 90),
                "over_90": age > 90,
            }.get(value)
            if cond is None:
                raise BadRequestError(
                    "ageing_bucket must be 0_30 | 31_60 | 61_90 | over_90 | no_date."
                )
            tranche_extra = [
                BankDisbursement.shortfall > 0,
                BankDisbursement.write_off_reason.is_(None),
                cond,
            ]
            lead_where.append(Lead.id.in_(
                select(BankDisbursement.lead_id).where(
                    *self._disb_where(f), *tranche_extra
                )
            ))

        elif segment == "exception":
            # The exception register is the panel most obviously worth
            # clicking, and it was the one segment the drill-down did not
            # accept. Each code narrows to exactly the rows the register
            # counted under it, so the two can never disagree.
            if value in ("no_disbursement_date", "no_receipt_date",
                         "materially_short"):
                tranche_extra = {
                    "no_disbursement_date": [
                        BankDisbursement.disbursed_on.is_(None)],
                    "no_receipt_date": [
                        BankDisbursement.amount_received.isnot(None),
                        BankDisbursement.received_on.is_(None)],
                    "materially_short": [
                        BankDisbursement.total_settled > 0,
                        BankDisbursement.shortfall > _MATERIAL_FLOOR,
                        BankDisbursement.shortfall
                        > BankDisbursement.total_due * _MATERIAL_PCT],
                }[value]
                lead_where.append(Lead.id.in_(
                    select(BankDisbursement.lead_id).where(
                        *self._disb_where(f), *tranche_extra
                    )
                ))
            elif value == "estimated_disbursement_date":
                tranche_extra = [
                    BankDisbursement.disbursed_on_estimated.is_(True)]
                lead_where.append(Lead.id.in_(
                    select(BankDisbursement.lead_id).where(
                        *self._disb_where(f), *tranche_extra
                    )
                ))
            elif value in ("no_sanctioned_amount", "on_aggregator",
                           "cannot_be_priced"):
                file_extra = [LeadBank.bank_status.in_(_LIVE)] + {
                    "no_sanctioned_amount": [LeadBank.loan_amount.is_(None)],
                    # `banks` is a global catalogue keyed by name, not a
                    # per-tenant table — the same join data_quality uses.
                    "on_aggregator": [LeadBank.bank_name.in_(
                        select(Bank.name).where(Bank.is_aggregator.is_(True)))],
                    "cannot_be_priced": [LeadBank.bank_name.in_(
                        select(Bank.name).where(
                            or_(Bank.is_aggregator.is_(True),
                                Bank.commission_rate.is_(None))))],
                }[value]
                lead_where.append(Lead.id.in_(
                    select(LeadBank.lead_id).where(
                        *self._file_where(f), *file_extra
                    )
                ))
            else:
                raise BadRequestError(
                    f"'{value}' is not an exception code. Use one of the keys "
                    "in the exception register's `by_code`."
                )

        sanc = (
            select(
                LeadBank.lead_id.label("lid"),
                func.sum(LeadBank.loan_amount).label("sanc"),
            )
            .where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE),
                   *file_extra)
            .group_by(LeadBank.lead_id)
            .subquery()
        )
        money = (
            select(
                BankDisbursement.lead_id.label("lid"),
                func.sum(BankDisbursement.disbursed_amount).label("disb"),
                func.sum(BankDisbursement.total_due).label("due"),
                func.sum(BankDisbursement.total_settled).label("settled"),
                func.sum(BankDisbursement.shortfall).label("owed"),
            )
            .where(*self._disb_where(f), *tranche_extra)
            .group_by(BankDisbursement.lead_id)
            .subquery()
        )
        q = (
            select(
                Lead.id, Lead.serial_no, Lead.full_name, Lead.current_stage,
                Lead.bank_name,
                func.coalesce(sanc.c.sanc, 0),
                func.coalesce(money.c.disb, 0),
                func.coalesce(money.c.due, 0),
                func.coalesce(money.c.settled, 0),
                func.coalesce(money.c.owed, 0),
            )
            .select_from(Lead)
            .outerjoin(sanc, sanc.c.lid == Lead.id)
            .outerjoin(money, money.c.lid == Lead.id)
            .where(Lead.id.in_(self._lead_scope(f)), *lead_where)
        )

        sub = q.subquery()
        total = (await self.db.execute(
            select(func.count()).select_from(sub)
        )).scalar() or 0
        # Tranches as well as students, because the panels do not all count
        # the same thing: ageing and by_lender count TRANCHES, the stage
        # funnel counts STUDENTS, and one student can hold several tranches
        # in the same bucket. Returning both lets the header read "17
        # students · 22 tranches" instead of appearing to contradict the
        # segment the user just clicked.
        tranche_total = (await self.db.execute(
            select(func.count())
            .select_from(BankDisbursement)
            .where(
                *self._disb_where(f), *tranche_extra,
                BankDisbursement.lead_id.in_(select(sub.c[0])),
            )
        )).scalar() or 0
        rows = (await self.db.execute(
            q.order_by(func.coalesce(money.c.disb, 0).desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).all()
        return {
            "segment": segment,
            "value": value,
            "total": total,
            "tranche_total": tranche_total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "lead_id": r[0], "serial_no": r[1], "full_name": r[2],
                    "stage": str(r[3]) if r[3] else None, "bank_name": r[4],
                    "sanctioned": r[5], "disbursed": r[6],
                    "earned": r[7], "collected": r[8], "outstanding": r[9],
                }
                for r in rows
            ],
        }


    async def by_expected_month(self, f: Filters | None = None) -> dict:
        """What we expect to close each month, against what actually did.

        The forecast the CRM never had: every other panel reports history.
        `expected_closure_month` is a TARGET set at sanction, so a month
        in the past with money still undrawn is a target that slipped —
        which is the only thing on this dashboard that looks forward.

        Students, not files: a student with three lender files is one
        deal expected to close once.
        """
        sanc = (
            select(
                LeadBank.lead_id.label("lid"),
                func.sum(LeadBank.loan_amount).label("sanc"),
            )
            .where(*self._file_where(f), LeadBank.bank_status.in_(_LIVE))
            .group_by(LeadBank.lead_id)
            .subquery()
        )
        disb = (
            select(
                BankDisbursement.lead_id.label("lid"),
                func.sum(BankDisbursement.disbursed_amount).label("disb"),
                func.sum(BankDisbursement.commission_amount).label("comm"),
            )
            .where(*self._disb_where(f))
            .group_by(BankDisbursement.lead_id)
            .subquery()
        )
        rows = (await self.db.execute(
            select(
                Lead.expected_closure_month,
                func.count(),
                func.coalesce(func.sum(func.coalesce(sanc.c.sanc, 0)), 0),
                func.coalesce(func.sum(func.coalesce(disb.c.disb, 0)), 0),
                func.coalesce(func.sum(func.coalesce(disb.c.comm, 0)), 0),
            )
            .select_from(Lead)
            .outerjoin(sanc, sanc.c.lid == Lead.id)
            .outerjoin(disb, disb.c.lid == Lead.id)
            .where(
                Lead.id.in_(self._lead_scope(f)),
                Lead.expected_closure_month.isnot(None),
            )
            .group_by(Lead.expected_closure_month)
            .order_by(Lead.expected_closure_month)
        )).all()

        today = date.today()
        months = []
        for m, n, sanctioned, drawn, comm in rows:
            pending = (sanctioned or Decimal("0")) - (drawn or Decimal("0"))
            months.append({
                "month": m,
                "students": n,
                "sanctioned": sanctioned,
                "disbursed": drawn,
                "commission": comm,
                # Approved money the month came and went without drawing.
                # A past month with pending money is a slipped target,
                # and it is the number this panel exists to surface.
                "pending": pending if pending > 0 else Decimal("0"),
                "is_past": bool(m and m < today.replace(day=1)),
            })

        # Everyone with money or a live sanction but no target date. They
        # are absent from the forecast entirely, so the count travels with
        # it rather than the forecast quietly reading low.
        no_month = (await self.db.execute(
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.id.in_(self._lead_scope(f)),
                Lead.expected_closure_month.is_(None),
                Lead.id.in_(select(LeadBank.lead_id).where(
                    *self._file_where(f), LeadBank.bank_status.in_(_LIVE))),
            )
        )).scalar() or 0

        slipped = [m for m in months if m["is_past"] and m["pending"] > 0]
        return {
            "months": months,
            "students_without_month": no_month,
            "slipped_months": len(slipped),
            "slipped_pending": sum((m["pending"] for m in slipped), Decimal("0")),
        }

    # ── Assembly ───────────────────────────────────────────────────────

    async def dashboard(self, months: int = 12, f: Filters | None = None) -> dict:
        """The overview panels, one payload.

        Sequential rather than gathered: they share the single
        request-scoped AsyncSession, which is not safe for concurrent use.
        Cached for 60s — see _CACHE_TTL_S.
        """
        key = (self.company_id, "dashboard", months, (f or Filters()).cache_key())
        cached = _cache_get(key)
        if cached is not None:
            return cached
        payload = {
            "funnel": await self.funnel(f),
            "pipeline_ahead": await self.pipeline_ahead(f),
            "monthly": await self.monthly(months, f),
            "by_lender": await self.by_lender(f),
            "ageing": await self.ageing(f),
            "data_quality": await self.data_quality(f),
        }
        _cache_set(key, payload)
        return payload
