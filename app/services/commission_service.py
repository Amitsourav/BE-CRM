"""Commission earned from lenders, and whether it has actually been paid.

FMC is paid a percentage of what a lender disburses. Until this module
existed none of that was in the CRM — the ledger lived on a spreadsheet,
so nobody could answer the three questions that find money:

  1. which disbursements were never billed
  2. which bills were never paid
  3. where the lender paid less than it owed

Everything here works on `bank_disbursements`, one row per tranche
released. `lead_banks` remains the record of the RELATIONSHIP with a
lender; this is the record of the money that came out of it.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_disbursement import BankDisbursement
from app.models.lead import Lead
from app.models.lead_bank import LeadBank
from app.models.profile import Profile
from app.core.constants import LAKH_IN_RUPEES
from app.core.exceptions import BadRequestError, NotFoundError
from app.services.bank_registry import get_commission_rate
from app.services.invoice_tax import _round2
from app.utils.date_helpers import now_utc


def compute_commission(amount_rupees: Decimal, rate_percent: Decimal) -> Decimal:
    """Commission on a disbursed amount, rounded to paise.

    Uses the invoice module's ROUND_HALF_UP rounder rather than Python's
    banker's rounding, so a commission line and the invoice line that
    bills it can never differ by a paisa.
    """
    return _round2(Decimal(amount_rupees) * Decimal(rate_percent) / Decimal("100"))


class CommissionService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    # ── Recording ──────────────────────────────────────────────────────

    async def record_disbursement(
        self,
        *,
        entry: LeadBank,
        disbursed_amount: Decimal,
        disbursed_on: date,
        user: Profile | None = None,
        rate_override: Decimal | None = None,
        tranche_no: int | None = None,
        utr_reference: str | None = None,
        notes: str | None = None,
        source: str = "manual",
        earns_commission: bool = True,
        commit: bool = False,
    ) -> BankDisbursement:
        """Record one tranche against an existing (lead, lender) file.

        Does NOT commit by default: the callers that matter — the stage
        machine and the bank-grid update — are already inside a
        transaction that also moves the lead, and the money and the stage
        must land together or not at all.
        """
        if disbursed_amount is None or Decimal(disbursed_amount) <= 0:
            raise BadRequestError("Disbursed amount must be greater than 0.")
        if disbursed_on is None:
            raise BadRequestError(
                "Disbursement date is required — every ageing and monthly "
                "figure in the reconciliation report is built on it."
            )
        if disbursed_on > now_utc().date():
            raise BadRequestError("Disbursement date cannot be in the future.")

        rate = rate_override
        if rate is None:
            rate = await get_commission_rate(self.db, entry.bank_name)
        if rate is None:
            raise BadRequestError(
                f"No commission rate is set for '{entry.bank_name}', so the "
                f"commission cannot be worked out. Set it on the lender "
                f"first (PATCH /leads/banks/{{id}}), or pass an explicit "
                f"rate for this one disbursement."
            )

        if tranche_no is None:
            tranche_no = (await self.db.execute(
                select(func.coalesce(func.max(BankDisbursement.tranche_no), 0) + 1)
                .where(BankDisbursement.lead_bank_id == entry.id)
            )).scalar_one()

        row = BankDisbursement(
            company_id=self.company_id,
            lead_bank_id=entry.id,
            lead_id=entry.lead_id,
            bank_name=entry.bank_name,
            disbursed_amount=Decimal(disbursed_amount),
            disbursed_on=disbursed_on,
            tranche_no=tranche_no,
            utr_reference=utr_reference,
            commission_rate=Decimal(rate),
            earns_commission=earns_commission,
            # A tranche that earns nothing is worth zero regardless of the
            # rate, and the rate is still stored so the report can show
            # what it WOULD have been worth.
            commission_amount=(
                compute_commission(disbursed_amount, rate)
                if earns_commission else Decimal("0")
            ),
            notes=notes,
            source=source,
            created_by=user.id if user else None,
        )
        self.db.add(row)
        await self.db.flush()
        if commit:
            await self.db.commit()
            await self.db.refresh(row)
        return row

    async def has_disbursement(self, lead_bank_id: uuid.UUID) -> bool:
        """Whether this file already has any tranche recorded.

        Used to keep the automatic capture idempotent: re-saving a cell
        that is already `disbursed` must not invent a second tranche.
        """
        return bool((await self.db.execute(
            select(BankDisbursement.id)
            .where(BankDisbursement.lead_bank_id == lead_bank_id)
            .limit(1)
        )).scalar_one_or_none())

    # ── Reading ────────────────────────────────────────────────────────

    async def list_for_entry(self, lead_bank_id: uuid.UUID) -> list[BankDisbursement]:
        return list((await self.db.execute(
            select(BankDisbursement)
            .where(
                BankDisbursement.lead_bank_id == lead_bank_id,
                BankDisbursement.company_id == self.company_id,
            )
            .order_by(BankDisbursement.tranche_no)
        )).scalars().all())

    async def get(self, disbursement_id: uuid.UUID) -> BankDisbursement:
        row = (await self.db.execute(
            select(BankDisbursement).where(
                BankDisbursement.id == disbursement_id,
                BankDisbursement.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not row:
            raise NotFoundError("Disbursement not found")
        return row

    # ── Updating ───────────────────────────────────────────────────────

    _EDITABLE = (
        "disbursed_on", "utr_reference", "amount_received", "tds_deducted",
        "received_on", "payment_reference", "write_off_reason", "notes",
        "gst_amount",
    )

    async def update(
        self, disbursement_id: uuid.UUID, payload: dict, user: Profile,
    ) -> BankDisbursement:
        """Correct a disbursement or record what the lender paid.

        Changing the amount or the rate recomputes the commission — those
        two are the inputs to the figure we chase, so letting them drift
        apart from it would make the report lie.
        """
        row = await self.get(disbursement_id)

        # Lakhs in, rupees out — same convention as everywhere else money
        # is typed in this CRM.
        if payload.get("disbursed_amount_lakh") is not None:
            payload["disbursed_amount"] = _round2(
                Decimal(payload["disbursed_amount_lakh"]) * LAKH_IN_RUPEES
            )
        payload.pop("disbursed_amount_lakh", None)

        if payload.get("disbursed_amount") is not None:
            if Decimal(payload["disbursed_amount"]) <= 0:
                raise BadRequestError("Disbursed amount must be greater than 0.")
            row.disbursed_amount = Decimal(payload["disbursed_amount"])
        if payload.get("commission_rate") is not None:
            row.commission_rate = Decimal(payload["commission_rate"])
        # Toggling eligibility recomputes too — ticking it back on has to
        # restore the figure, not leave a zero behind.
        if "earns_commission" in payload:
            row.earns_commission = bool(payload["earns_commission"])
        if (
            payload.get("disbursed_amount") is not None
            or payload.get("commission_rate") is not None
            or "earns_commission" in payload
        ):
            row.commission_amount = (
                compute_commission(row.disbursed_amount, row.commission_rate)
                if row.earns_commission else Decimal("0")
            )
        # An explicit commission overrides the formula — lenders do
        # occasionally settle at a negotiated figure, and the report has
        # to reflect what was actually agreed rather than what the
        # percentage says.
        if payload.get("commission_amount") is not None:
            if not row.earns_commission:
                raise BadRequestError(
                    "This disbursement is marked as not earning commission, "
                    "so an amount cannot be set on it. Tick 'earns "
                    "commission' first."
                )
            row.commission_amount = _round2(Decimal(payload["commission_amount"]))

        for f in self._EDITABLE:
            if f in payload:
                setattr(row, f, payload[f])

        if row.disbursed_on and row.disbursed_on > now_utc().date():
            raise BadRequestError("Disbursement date cannot be in the future.")
        if row.received_on and row.disbursed_on and row.received_on < row.disbursed_on:
            raise BadRequestError(
                "Payment date cannot be before the disbursement it settles."
            )

        row.updated_at = now_utc()
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def delete(self, disbursement_id: uuid.UUID) -> None:
        """Remove a disbursement. Blocked once a bill claims it.

        Deleting a billed row would leave an invoice claiming money with
        nothing behind it, and invoice numbers are permanent.
        """
        row = await self.get(disbursement_id)
        if row.invoice_id is not None:
            raise BadRequestError(
                "This disbursement is on an invoice. Void the invoice "
                "before deleting it."
            )
        await self.db.delete(row)
        await self.db.commit()

    # ── The report ─────────────────────────────────────────────────────

    async def reconciliation(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        bank_name: list[str] | None = None,
        status: list[str] | None = None,
        disbursed_from: date | None = None,
        disbursed_to: date | None = None,
        q: str | None = None,
    ) -> dict:
        """Every disbursement with what it earned and what came back.

        Totals are computed over the WHOLE filtered set, not the current
        page — a page total is the classic wrong answer to "how much are
        we owed".
        """
        from app.utils.pagination import paginate

        base = (
            select(BankDisbursement)
            .where(BankDisbursement.company_id == self.company_id)
        )
        if bank_name:
            base = base.where(BankDisbursement.bank_name.in_(bank_name))
        if status:
            base = base.where(BankDisbursement.status.in_(status))
        if disbursed_from:
            base = base.where(BankDisbursement.disbursed_on >= disbursed_from)
        if disbursed_to:
            base = base.where(BankDisbursement.disbursed_on <= disbursed_to)
        if q:
            base = base.where(
                BankDisbursement.lead_id.in_(
                    select(Lead.id).where(
                        Lead.company_id == self.company_id,
                        Lead.full_name.ilike(f"%{q}%"),
                    )
                )
            )

        paged = await paginate(
            self.db, base.order_by(BankDisbursement.disbursed_on.desc()),
            page, page_size,
        )
        rows = paged["items"]

        names = await self._lead_names([r.lead_id for r in rows])
        items = [
            {
                "id": r.id,
                "lead_id": r.lead_id,
                "lead_name": names.get(r.lead_id, {}).get("full_name"),
                "serial_no": names.get(r.lead_id, {}).get("serial_no"),
                "bank_name": r.bank_name,
                "tranche_no": r.tranche_no,
                "disbursed_amount": r.disbursed_amount,
                "disbursed_on": r.disbursed_on,
                "commission_rate": r.commission_rate,
                "earns_commission": r.earns_commission,
                "commission_amount": r.commission_amount,
                "gst_amount": r.gst_amount,
                "invoice_id": r.invoice_id,
                "amount_received": r.amount_received,
                "tds_deducted": r.tds_deducted,
                "received_on": r.received_on,
                "shortfall": r.shortfall,
                "status": r.status,
                # None rather than 0 when the date is unknown — a
                # historical row with no date is not "0 days old", and
                # showing it as fresh would hide the oldest debts.
                "days_outstanding": (
                    (now_utc().date() - r.disbursed_on).days
                    if r.disbursed_on else None
                ),
                "utr_reference": r.utr_reference,
                "source": r.source,
            }
            for r in rows
        ]

        return {
            **{k: v for k, v in paged.items() if k != "items"},
            "items": items,
            "totals": await self._totals(base),
        }

    async def _totals(self, base) -> dict:
        """Money totals over the filtered set."""
        sub = base.subquery()
        row = (await self.db.execute(
            select(
                func.coalesce(func.sum(sub.c.disbursed_amount), 0),
                func.coalesce(func.sum(sub.c.commission_amount), 0),
                func.coalesce(func.sum(sub.c.gst_amount), 0),
                func.coalesce(func.sum(sub.c.amount_received), 0),
                func.coalesce(func.sum(sub.c.tds_deducted), 0),
                func.count(),
            )
        )).one()
        disbursed, commission, gst, received, tds, n = row
        return {
            "count": n,
            "disbursed_total": disbursed,
            "commission_total": commission,
            "gst_total": gst,
            "received_total": received,
            "tds_total": tds,
            # What is still genuinely owed: everything billed and charged,
            # less cash in AND less TDS — because TDS was paid to the tax
            # department on our behalf and is not a shortfall.
            "outstanding_total": (commission + gst) - (received + tds),
        }

    async def summary(self) -> list[dict]:
        """Per-lender rollup — "who owes us what"."""
        rows = (await self.db.execute(
            select(
                BankDisbursement.bank_name,
                func.count().label("files"),
                func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0),
                func.coalesce(func.sum(BankDisbursement.commission_amount), 0),
                func.coalesce(func.sum(BankDisbursement.amount_received), 0),
                func.coalesce(func.sum(BankDisbursement.tds_deducted), 0),
                func.count().filter(BankDisbursement.invoice_id.is_(None)),
            )
            .where(BankDisbursement.company_id == self.company_id)
            .group_by(BankDisbursement.bank_name)
            .order_by(func.sum(BankDisbursement.commission_amount).desc())
        )).all()
        # Gross theoretical per lender, merged in. Comes from lead_banks
        # rather than bank_disbursements, so a lender can appear here with
        # sanctioned files and no disbursements at all — which is exactly
        # the case worth seeing, since it is approved money that has not
        # converted.
        theo = (await self.db.execute(
            select(
                LeadBank.bank_name,
                func.count(),
                func.coalesce(func.sum(LeadBank.loan_amount), 0),
                func.count().filter(LeadBank.loan_amount.is_(None)),
                func.coalesce(func.sum(
                    LeadBank.loan_amount * LeadBank.commission_rate / 100
                ), 0),
            )
            .where(
                LeadBank.company_id == self.company_id,
                LeadBank.bank_status.in_(self._REVENUE_ELIGIBLE_STATUSES),
            )
            .group_by(LeadBank.bank_name)
        )).all()
        theo_by_bank = {
            t[0]: {
                "sanctioned_files": t[1],
                "sanctioned_total": t[2],
                "files_missing_amount": t[3],
                "gross_theoretical_revenue": _round2(Decimal(t[4])),
            }
            for t in theo
        }

        out = []
        seen = set()
        for r in rows:
            seen.add(r[0])
            t = theo_by_bank.get(r[0], {})
            out.append({
                "bank_name": r[0],
                "files": r[1],
                "disbursed_total": r[2],
                "commission_total": r[3],
                "received_total": r[4],
                "tds_total": r[5],
                "outstanding_total": r[3] - (r[4] + r[5]),
                "unbilled_count": r[6],
                "sanctioned_files": t.get("sanctioned_files", 0),
                "sanctioned_total": t.get("sanctioned_total", Decimal("0")),
                "gross_theoretical_revenue": t.get(
                    "gross_theoretical_revenue", Decimal("0")
                ),
                "files_missing_amount": t.get("files_missing_amount", 0),
            })
        # Lenders with sanctioned files but nothing disbursed yet. Left
        # out of the loop above because it iterates disbursements.
        for name, t in theo_by_bank.items():
            if name in seen:
                continue
            out.append({
                "bank_name": name,
                "files": 0,
                "disbursed_total": Decimal("0"),
                "commission_total": Decimal("0"),
                "received_total": Decimal("0"),
                "tds_total": Decimal("0"),
                "outstanding_total": Decimal("0"),
                "unbilled_count": 0,
                **t,
            })
        out.sort(key=lambda x: x["gross_theoretical_revenue"], reverse=True)
        return out

    # ── Gross theoretical revenue ──────────────────────────────────────
    # Amit's term, and his definition: the commission FMC would earn if
    # every sanctioned loan drew down in full — the lender's rate applied
    # to the SANCTIONED amount, as against `revenue` which applies it to
    # what was actually disbursed. Lifetime, every file ever sanctioned,
    # whether or not it has since disbursed (his choice, 2026-08-31).
    #
    # The interesting number is the gap between the two: loans approved
    # but never fully drawn.

    # Revenue counts from PF onward, NOT from sanction. FMC's revenue
    # tracker states the rule outright — "Revenue counts only for stage =
    # PF or Disbursed" — and its own numbers match it exactly: 114
    # revenue-eligible students = 17 at PF + 97 disbursed, with the 17
    # sitting at Sanction contributing nothing.
    #
    # That is the whole significance of PF: the student paying the
    # processing fee is FMC's confirmation that the loan is real and which
    # lender won it. Before that, a sanction is only an offer.
    _REVENUE_ELIGIBLE_STATUSES = ("pf_paid", "disbursed")
    # Sanctioned files are still worth counting SEPARATELY — approved but
    # not yet confirmed — so they are reported rather than silently
    # dropped out of the picture.
    _AWAITING_CONFIRMATION_STATUSES = ("sanctioned",)

    async def gross_theoretical(self, bank_name: list[str] | None = None) -> dict:
        """GTR, plus an honest count of what it could not include.

        A file with no sanctioned amount, or one whose lender has no rate,
        is EXCLUDED from the sum and counted separately — never treated as
        zero. Of the 79 files that reached sanctioned before the capture
        rule existed, 48 carry no amount, so a total presented without
        those counters would read as complete and be barely half the book.
        """
        base = select(LeadBank).where(
            LeadBank.company_id == self.company_id,
            LeadBank.bank_status.in_(self._REVENUE_ELIGIBLE_STATUSES),
        )
        if bank_name:
            base = base.where(LeadBank.bank_name.in_(bank_name))
        rows = (await self.db.execute(base)).scalars().all()

        sanctioned_total = Decimal("0")
        gtr = Decimal("0")
        missing_amount = 0
        missing_rate = 0
        counted = 0
        for r in rows:
            if r.loan_amount is None:
                missing_amount += 1
                continue
            sanctioned_total += r.loan_amount
            if r.commission_rate is None:
                # The amount is known but the lender's cut is not, so the
                # file's worth is unknowable rather than zero.
                missing_rate += 1
                continue
            gtr += compute_commission(r.loan_amount, r.commission_rate)
            counted += 1

        return {
            "files": len(rows),
            "files_counted": counted,
            "files_missing_amount": missing_amount,
            "files_missing_rate": missing_rate,
            "sanctioned_total": sanctioned_total,
            "gross_theoretical_revenue": gtr,
        }

    # Falls back to 80 when a tenant has no invoice_settings row at all.
    # Better a stated default than a crash on a screen whose job is to
    # show a number.
    _DEFAULT_NET_FACTOR = Decimal("80.00")

    async def net_theoretical_factor(self) -> Decimal:
        """The % of gross theoretical revenue expected to be realised.

        Gross theoretical assumes every sanctioned loan draws down in
        full. They do not — students take less than approved, go
        elsewhere, or drop out — so this haircut is what makes the figure
        usable as a forecast rather than a ceiling.
        """
        from app.models.invoice_settings import InvoiceSettings
        factor = (await self.db.execute(
            select(InvoiceSettings.net_theoretical_factor)
            .where(InvoiceSettings.company_id == self.company_id)
        )).scalar_one_or_none()
        return factor if factor is not None else self._DEFAULT_NET_FACTOR

    async def set_net_theoretical_factor(self, factor: Decimal) -> Decimal:
        from app.models.invoice_settings import InvoiceSettings
        row = (await self.db.execute(
            select(InvoiceSettings)
            .where(InvoiceSettings.company_id == self.company_id)
        )).scalar_one_or_none()
        if row is None:
            raise BadRequestError(
                "This tenant has no invoice settings yet. Set up the "
                "company's billing details first (PUT /invoices/settings)."
            )
        row.net_theoretical_factor = Decimal(factor)
        await self.db.commit()
        return row.net_theoretical_factor

    async def revenue_vs_theoretical(self) -> dict:
        """GTR against actual revenue, and the gap between them.

        `revenue` here is the same figure the reconciliation report calls
        `commission_total` — commission on what was actually disbursed.
        Kept to one definition so the two screens cannot disagree.
        """
        theo = await self.gross_theoretical()
        earned = (await self.db.execute(
            select(func.coalesce(func.sum(BankDisbursement.commission_amount), 0))
            .where(BankDisbursement.company_id == self.company_id)
        )).scalar_one()
        disbursed = (await self.db.execute(
            select(func.coalesce(func.sum(BankDisbursement.disbursed_amount), 0))
            .where(BankDisbursement.company_id == self.company_id)
        )).scalar_one()
        factor = await self.net_theoretical_factor()
        return {
            **theo,
            # Amit's term: 80% of gross theoretical. The haircut for loans
            # that are approved but never fully drawn.
            "net_theoretical_factor": factor,
            "net_theoretical_revenue": _round2(
                theo["gross_theoretical_revenue"] * factor / Decimal("100")
            ),
            "disbursed_total": disbursed,
            "revenue": earned,
            # Positive = approved money that has not been drawn down (or
            # has been drawn but not yet recorded). Can go negative if a
            # lender releases more than the sanction on file, which is a
            # data problem worth seeing rather than clamping away.
            "drawdown_gap": theo["gross_theoretical_revenue"] - earned,
        }

    async def _lead_names(self, lead_ids: list) -> dict:
        ids = [i for i in lead_ids if i]
        if not ids:
            return {}
        rows = (await self.db.execute(
            select(Lead.id, Lead.full_name, Lead.serial_no).where(Lead.id.in_(ids))
        )).all()
        return {r[0]: {"full_name": r[1], "serial_no": r[2]} for r in rows}
