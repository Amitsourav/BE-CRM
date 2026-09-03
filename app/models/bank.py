from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Integer, Numeric, String, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, TimestampMixin


class Bank(Base, TimestampMixin):
    """The canonical lender list — a ROUTE to money, not an institution.

    Two entries can be the same bank. `UC Axis` and `Axis Direct (UC
    Code)` are Axis reached through UniCred and reached directly, and
    they pay 1.00% and 1.35%; `Nomad Normal` and `Nomad US` are one
    lender and two products at 1.60% and 3.00%. FMC's revenue tracker has
    always modelled it this way, and commission cannot be computed
    without it — which is why `commission_rate` hangs off this row.

    The flat names that predate this (`Axis`, `Nomad`) are deliberately
    left with NO rate: each maps to more than one route at different
    rates, and guessing one would misprice every file on the other.

    Editable without a deploy.

    Was a Python tuple (`FMC_BANKS`), which meant every new lending
    relationship needed a code change and a release before shares from
    that lender's WhatsApp group could be recorded — and until then the
    relationship was invisible in the CRM. That constant is now only the
    seed and the emergency fallback; this table is the source of truth.

    Still a CONTROLLED vocabulary, not free text. It was locked in the
    first place because free-typing produced sbi/SBI, Unicred/UniCred and
    Poonawala/Poonawalla in the same database and broke reporting. Adding
    a name is admin-only and deliberate; the list just no longer needs a
    deploy to change.

    NOT company-scoped, matching the constant it replaces. Admitverse is
    excluded from bank features entirely by the brand gate
    (`_require_fmc_banks`), so scoping rows per tenant would add a seeding
    burden — including for the integration sandbox — without changing any
    behaviour.
    """

    __tablename__ = "banks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Case-sensitively unique. A case-insensitive unique index also exists
    # (see migration) so "gyandhan" can't be added alongside "GyanDhan" —
    # that is the exact drift this list prevents.
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # Deactivating removes a lender from the dropdown and stops new shares
    # being recorded against it. It does NOT hide it from the grid — a
    # lender you stop working with still had real files go to it, and
    # dropping the column would make that history invisible.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Dropdown and grid-column order. Seeded from the constant's order:
    # banks first, then NBFCs.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # What this lender pays us, as a percentage of the amount it
    # disburses. Nullable because it has to be filled in per lender and
    # nothing can guess it — a lender with no rate set simply cannot have
    # its commission computed, and the reconciliation report is expected
    # to say so out loud rather than quietly bill it at zero.
    #
    # This is the CURRENT default only. Every disbursement snapshots the
    # rate that applied to it (bank_disbursements.commission_rate), so
    # renegotiating a rate never rewrites what was already earned.
    #
    # Lives here, on a table with no company_id, which means the rate is
    # shared across tenants. Acceptable only because Admitverse is
    # brand-gated out of every bank feature; if a second FMC-like tenant
    # ever appears this must move to a tenant-scoped table.
    commission_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # True = this row is an AGGREGATOR, not a lender you can be paid by.
    # UniCred and Nomad each front several banks at different rates, so a
    # single rate on the parent is meaningless and commission on a file
    # left here can never be computed.
    #
    # Explicit rather than inferred. The frontend was detecting these by
    # checking whether a name appears as a word inside other route names
    # ("Nomad" inside "Nomad Normal"), which happens to work today and
    # breaks the moment a sub-product is renamed or a real lender's name
    # is a prefix of another. A file sitting on an aggregator earns
    # nothing, so the UI has to be able to say so with certainty.
    is_aggregator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
