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
            commission_amount=compute_commission(disbursed_amount, rate),
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
        if payload.get("disbursed_amount") is not None or payload.get("commission_rate") is not None:
            row.commission_amount = compute_commission(
                row.disbursed_amount, row.commission_rate
            )
        # An explicit commission overrides the formula — lenders do
        # occasionally settle at a negotiated figure, and the report has
        # to reflect what was actually agreed rather than what the
        # percentage says.
        if payload.get("commission_amount") is not None:
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
                "commission_amount": r.commission_amount,
                "gst_amount": r.gst_amount,
                "invoice_id": r.invoice_id,
                "amount_received": r.amount_received,
                "tds_deducted": r.tds_deducted,
                "received_on": r.received_on,
                "shortfall": r.shortfall,
                "status": r.status,
                "days_outstanding": (now_utc().date() - r.disbursed_on).days,
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
        return [
            {
                "bank_name": r[0],
                "files": r[1],
                "disbursed_total": r[2],
                "commission_total": r[3],
                "received_total": r[4],
                "tds_total": r[5],
                "outstanding_total": r[3] - (r[4] + r[5]),
                "unbilled_count": r[6],
            }
            for r in rows
        ]

    async def _lead_names(self, lead_ids: list) -> dict:
        ids = [i for i in lead_ids if i]
        if not ids:
            return {}
        rows = (await self.db.execute(
            select(Lead.id, Lead.full_name, Lead.serial_no).where(Lead.id.in_(ids))
        )).all()
        return {r[0]: {"full_name": r[1], "serial_no": r[2]} for r in rows}
