from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel


class CSVPreviewRequest(BaseModel):
    column_mapping: dict[str, str]
    assigned_agent_id: uuid.UUID | None = None
    lead_source_id: uuid.UUID | None = None


class CSVProcessRequest(BaseModel):
    column_mapping: dict[str, str]
    assigned_agent_id: uuid.UUID | None = None
    lead_source_id: uuid.UUID | None = None


class CSVImportOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    uploaded_by: uuid.UUID
    file_name: str
    status: str
    total_rows: int
    success_count: int
    failure_count: int
    duplicate_count: int
    error_details: list = []
    column_mapping: dict = {}
    raw_headers: list[str] = []
    lead_source_id: uuid.UUID | None = None
    assigned_agent_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CSVPreviewOut(BaseModel):
    id: uuid.UUID
    file_name: str
    total_rows: int
    raw_headers: list[str]
    suggested_mapping: dict[str, str]
    preview_rows: list[dict]
