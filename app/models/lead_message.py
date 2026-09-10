from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class LeadMessage(Base):
    """One WhatsApp message in the conversation with a lead.

    The direct-to-lead counterpart of `lead_bank_messages`. That table
    hangs off (lead, bank) because its whole purpose is to keep the PNB
    thread about a student apart from the SBI thread about the same
    student. Here there is only ever one counterparty — the lead — so it
    hangs off the lead itself.

    Deliberately NOT `lead_remarks`. A remark is a counsellor's internal
    note: one author, who is a CRM user, no direction and no phone
    number. A chat has two sides, and the person on the other end will
    never have a CRM profile. Storing a transcript as a remark loses
    exactly the things a chat view needs — who said it, from which
    number, and which way it went.
    """

    __tablename__ = "lead_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # The WhatsApp number this message came from. Not an FK: the lead is
    # a person on WhatsApp, not a CRM user, and never will be. This is
    # also what the conversation is displayed against — the lead may have
    # been created from a CSV with one number and be chatting from
    # another, and the thread should show the number actually used.
    sender_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sender_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # True when we sent it, false when the lead did. The bot decides this
    # from the sending number; the CRM does not try to infer it.
    is_our_team: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # WhatsApp's own message id — the idempotency key.
    #
    # This is what makes a retry safe. When the bot's request times out
    # it cannot tell whether the write landed, so it retries; without a
    # key that is how the same conversation ends up on the lead twice.
    # UNIQUE per (lead_id, wa_message_id) via a partial index, so a
    # redelivery returns the existing row instead of adding a line.
    #
    # Nullable because a message typed into the CRM by hand has no
    # WhatsApp id, and the partial index ignores NULLs.
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    # When WhatsApp says it was sent, as opposed to when we stored it.
    # A backfill or a delayed retry would otherwise render the thread in
    # the wrong order.
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    lead = relationship("Lead", foreign_keys=[lead_id])
