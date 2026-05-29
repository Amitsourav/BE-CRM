from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, Text, text, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base


class MetaWebhookEvent(Base):
    """Durable queue for incoming Meta Lead Ads webhooks. The webhook
    handler persists every payload here before returning 200, and a
    background worker (app.workers.meta_retry) walks pending rows to
    route + ingest. Survives AV outages, restart-during-processing,
    and Meta's 36-hour redelivery window.
    """
    __tablename__ = "meta_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'dropped')",
            name="meta_event_status_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    leadgen_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    form_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    page_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resulting_lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
