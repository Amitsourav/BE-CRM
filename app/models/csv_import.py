from __future__ import annotations

import uuid
from typing import Optional, List, Dict
from sqlalchemy import String, Integer, text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, ENUM
from app.models.base import Base, TimestampMixin


class CSVImport(Base, TimestampMixin):
    __tablename__ = "csv_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(ENUM('uploaded', 'previewing', 'processing', 'completed', 'failed', name='csv_import_status', create_type=False), nullable=False, server_default=text("'uploaded'"))
    total_rows: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    success_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failure_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error_details: Mapped[List] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    column_mapping: Mapped[Dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    raw_headers: Mapped[List[str]] = mapped_column(ARRAY(String), server_default=text("'{}'"))
    lead_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("lead_sources.id"), nullable=True)
    assigned_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=True)

    uploader = relationship("Profile", foreign_keys=[uploaded_by])
