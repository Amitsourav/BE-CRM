from __future__ import annotations

import uuid
from typing import Optional
from sqlalchemy import String, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base, TimestampMixin


class LeadSource(Base, TimestampMixin):
    __tablename__ = "lead_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(ENUM('csv', 'meta_ads', 'manual', 'whatsapp', name='lead_source_type', create_type=False), nullable=False, server_default=text("'manual'"))
    meta_form_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    leads = relationship("Lead", back_populates="lead_source")
