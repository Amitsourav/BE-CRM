from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class LeadBankMessage(Base):
    """A message about one lead, in one lender's WhatsApp group.

    Hangs off `lead_banks` rather than the lead, because the whole point
    is to keep the PNB conversation about a student separate from the SBI
    conversation about the same student. `lead_remarks` stays what it is —
    the lead's general internal timeline.

    Both sides of the conversation land here: our counsellors and the
    bank's staff. `is_our_team` is what tells them apart, resolved by the
    bot from the sender's number, since the bank's people will never have
    CRM profiles.
    """

    __tablename__ = "lead_bank_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # The (lead, bank) pair this message belongs to. CASCADE so removing a
    # bank entry from a lead takes its conversation with it rather than
    # orphaning rows.
    lead_bank_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lead_banks.id", ondelete="CASCADE"), nullable=False
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # The WhatsApp number the message came from. Not an FK — most senders
    # are the lender's staff and will never exist in `profiles`.
    sender_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # True when the sender is one of ours. Decided by the bot, which knows
    # the team's numbers; the CRM does not try to infer it.
    is_our_team: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # WhatsApp's own message id. Unique (partial index — see migration)
    # so a redelivered message is a no-op rather than a duplicate.
    # Nullable because a message added by hand through the UI has none.
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    lead_bank = relationship("LeadBank", back_populates="messages")
