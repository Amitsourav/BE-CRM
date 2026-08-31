from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field


class StageTransitionRequest(BaseModel):
    to_stage: str
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    due_date: datetime | None = None
    lost_reason: str | None = None

    # ── Required only when to_stage == "pf_paid" (FMC) ────────────────
    # "PF paid" is a fact about ONE lender: that lender's processing fee
    # was paid, for that lender's sanctioned amount. Recording it on the
    # lead alone loses both — which is the state 8 of the 10 current
    # pf_paid leads are in, sitting at the stage with no bank row saying
    # which lender it refers to. Asking here is what keeps the lead's
    # stage and its bank row describing the same event.
    bank_name: str | None = None
    # In LAKHS, matching every other loan figure a user types in this CRM
    # (lead.loan_amount is "30 Lakh" / "64"). Stored to
    # lead_banks.loan_amount in RUPEES, which is the unit the 31 existing
    # rows already use — the conversion happens server-side so neither
    # the user nor the existing data has to change.
    bank_loan_amount_lakh: Decimal | None = Field(default=None, gt=0)

    # ── Required only when to_stage == "disbursed" (FMC) ──────────────
    # The revenue event. Commission is a percentage of what the lender
    # RELEASED (not what it sanctioned), on the date it released it, so
    # both are part of the claim. bank_name may be omitted — it defaults
    # to the lead's primary lender, which the CRM already knows by the
    # time a file disburses.
    disbursed_amount_lakh: Decimal | None = Field(default=None, gt=0)
    disbursed_on: date | None = None

    # ── Required only when to_stage == "sanctioned" (FMC) ─────────────
    # Gross theoretical revenue is the lender's rate applied to the
    # amount it APPROVED — a different number from what it later
    # releases. The date is required because that figure is reported by
    # month, and 77 of the 79 files sanctioned before this rule existed
    # carry no date at all. bank_name may be omitted; it defaults to the
    # lead's primary lender.
    sanctioned_amount_lakh: Decimal | None = Field(default=None, gt=0)
    sanction_date: date | None = None


class StageLogOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    lead_id: uuid.UUID
    from_stage: str | None = None
    to_stage: str
    changed_by: uuid.UUID
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    due_date_set: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
