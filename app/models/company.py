from __future__ import annotations

import uuid
from typing import Optional
from sqlalchemy import String, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'UTC'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Relationships
    profiles = relationship("Profile", back_populates="company")
    leads = relationship("Lead", back_populates="company")
    ai_agents = relationship("AIAgent", back_populates="company")
