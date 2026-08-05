from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    """A long-lived, revocable credential for a machine client.

    An API key does not carry permissions of its own — it *acts as* a
    profile (`profile_id`). That indirection is deliberate:

    • Every audit column in this schema (`leads.created_by`,
      `lead_remarks.author_id`, `lead_stage_logs.changed_by`,
      `activity_logs.user_id`) is an FK to `profiles.id`. A key that
      resolved to a synthetic principal would have nothing to write
      there. Pointing at a real service-account profile means the
      integration's writes are attributable with zero changes to any
      of those tables.
    • Revoking the key and disabling the account stay independent —
      revoke the key and the service account survives; deactivate the
      account and every key bound to it stops working at once.

    The service account is a normal `profiles` row (and therefore a
    normal Supabase auth user). Create it with `POST /auth/register`,
    then mint a key against it. Its role determines the key's scope.

    Only the SHA-256 of the key is stored. The plaintext is shown once,
    at creation, and is unrecoverable afterwards.
    """

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    # The profile this key authenticates as. CASCADE: if the service
    # account is ever removed, its keys go with it rather than dangling
    # into a state where they'd resolve to nothing.
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )

    # Human label — "WhatsApp group ingest (prod)". Shown in the admin list.
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # First few characters of the plaintext, stored so the admin UI can
    # tell two keys apart ("crmk_live_8fa2…") without holding the secret.
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    # SHA-256 hex of the full plaintext. Unique so lookup is a single
    # indexed equality probe rather than a scan-and-compare over rows.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Optional hard expiry. NULL = never expires; the key lives until revoked.
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Touched at most once a minute on use — see app/core/api_key.py for
    # why this is throttled rather than written on every request.
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )

    profile = relationship("Profile", foreign_keys=[profile_id], lazy="joined")
