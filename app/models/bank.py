from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, Integer, String, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, TimestampMixin


class Bank(Base, TimestampMixin):
    """The canonical lender list, editable without a deploy.

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

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
