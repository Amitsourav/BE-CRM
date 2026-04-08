from __future__ import annotations

import uuid
from typing import Optional
from sqlalchemy import String, Boolean, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, ENUM
from app.models.base import Base, TimestampMixin


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(ENUM('admin', 'manager', 'telecaller', name='user_role', create_type=False), nullable=False, server_default=text("'telecaller'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    vertical: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Relationships
    company = relationship("Company", back_populates="profiles", lazy="joined")
    assigned_leads = relationship("Lead", back_populates="assigned_agent", foreign_keys="Lead.assigned_agent_id")
    tasks = relationship("Task", back_populates="assignee", foreign_keys="Task.assigned_to")
    notifications = relationship("Notification", back_populates="user")

    @property
    def company_name(self) -> str | None:
        return self.company.name if self.company else None

    @property
    def company_timezone(self) -> str | None:
        return self.company.timezone if self.company else None
