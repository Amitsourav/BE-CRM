from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, text, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base


class MetaFormRouting(Base):
    """One row per Meta Lead Ads form. Tells the FMC webhook gateway
    which tenant a given lead should land in and what LeadSource to
    tag it with. Lives on the FMC DB; AV DB has the empty table but
    never reads from it.
    """
    __tablename__ = "meta_form_routing"
    __table_args__ = (
        CheckConstraint("target IN ('fmc', 'av')", name="meta_form_target_chk"),
    )

    form_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    target: Mapped[str] = mapped_column(String(10), nullable=False)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
