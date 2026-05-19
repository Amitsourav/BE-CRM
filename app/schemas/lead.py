from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator


class LeadCreate(BaseModel):
    full_name: str
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = "India"
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    lead_source_id: uuid.UUID | None = None
    assigned_agent_id: uuid.UUID | None = None
    custom_fields: dict | None = None
    tags: list[str] | None = None
    notes: str | None = None


class LeadUpdate(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    custom_fields: dict | None = None
    tags: list[str] | None = None
    notes: str | None = None
    # Pipeline-related fields. Without these, the Edit Lead form's
    # "callback date" / "assign to" / "stage" inputs were silently
    # dropped by Pydantic before the service ever saw them — the
    # value reaches the frontend, the user thinks it saved, but the
    # column never updated. Stage transitions still go through the
    # dedicated /stage endpoint (with its own validation); listing
    # current_stage here lets simple inline edits work too.
    due_date: datetime | None = None
    assigned_agent_id: uuid.UUID | None = None
    pre_counsellor_id: uuid.UUID | None = None
    current_stage: str | None = None
    is_important: bool | None = None
    # FMC enhanced tile fields — editable from the lead form.
    loan_amount: str | None = None
    bank_name: str | None = None
    bank_status: str | None = None
    docs_required: int | None = None
    docs_submitted: int | None = None
    submitted_docs: list[str] | None = None
    # FMC DNP attempt counter — telecallers can manually adjust this
    # via the card's DNP-N dropdown (1..6). Auto-incremented in
    # StageMachine on transitions into 'dnp'; manual edit lets the
    # user correct or align with reality.
    dnp_count: int | None = None
    # Admitverse enhanced tile field — free-text budget figure.
    # FMC FE doesn't render it. Editable inline from the AV Kanban tile.
    budget: str | None = None
    # When current_stage is included, the service routes the change
    # through StageMachine.transition() so transition validity, notes
    # requirements, and lost_reason gating actually run. Without these
    # accompanying fields the FE can't pass a remark on stage change
    # via PUT /leads/{id} — it had to call the separate /stage endpoint.
    conversation_notes: str | None = None
    agent_agenda: str | None = None
    lost_reason: str | None = None


class LeadImportantToggle(BaseModel):
    is_important: bool


class LeadOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    alternate_phone: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    highest_qualification: str | None = None
    stream: str | None = None
    passing_year: int | None = None
    college_name: str | None = None
    university: str | None = None
    percentage: Decimal | None = None
    target_degree: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] | None = None
    preferred_universities: list[str] | None = None
    current_stage: str
    assigned_agent_id: uuid.UUID | None = None
    pre_counsellor_id: uuid.UUID | None = None
    lead_source_id: uuid.UUID | None = None
    call_attempt_count: int = 0
    due_date: datetime | None = None
    connected_time: datetime | None = None
    won_time: datetime | None = None
    lost_time: datetime | None = None
    lost_reason: str | None = None
    custom_fields: dict = {}
    tags: list[str] = []
    notes: str | None = None
    is_important: bool = False
    # FMC enhanced tile fields (free text, enum, counters)
    loan_amount: str | None = None
    bank_name: str | None = None
    bank_status: str | None = None
    docs_required: int = 6
    docs_submitted: int = 0
    submitted_docs: list[str] = []
    dnp_count: int = 0
    # Multi-bank fields. bank_count = how many entries in lead_banks;
    # top_banks = top 2 entries (by status priority) so the Kanban list
    # endpoint (which serves LeadOut) can render the same UI as /by-stage.
    bank_count: int = 0
    top_banks: list[dict] = []
    latest_note: dict | None = None
    # Admitverse tile field (free text budget). FMC leaves NULL.
    budget: str | None = None
    # Activity rollups (computed in service, not on the model)
    assigned_agent_name: str | None = None
    assigned_agent_role: str | None = None
    pre_counsellor_name: str | None = None
    # Human-readable source label (e.g. "WhatsApp Campaign", "Facebook Ads").
    # Resolved from lead_source_id via a batched lookup in the service.
    # Without this, the FE would need a separate /sources fetch per card.
    source_name: str | None = None
    task_count: int = 0
    call_count: int = 0
    notes_count: int = 0
    has_active_ai_campaign: bool = False
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadCardOut(BaseModel):
    """Slim projection for the Kanban board. The full LeadOut ships 35
    fields per row including JSONB custom_fields and notes; on a 19-column
    Admitverse board with hundreds of leads that's a lot of bytes per
    refresh. The card UI only needs identity + routing + due date.
    """
    id: uuid.UUID
    full_name: str
    phone: str | None = None
    email: str | None = None
    current_stage: str
    assigned_agent_id: uuid.UUID | None = None
    pre_counsellor_id: uuid.UUID | None = None
    lead_source_id: uuid.UUID | None = None
    due_date: datetime | None = None
    last_contacted_at: datetime | None = None
    call_attempt_count: int = 0
    tags: list[str] = []
    is_important: bool = False
    # FMC enhanced tile fields. Always returned; FE renders only on FMC.
    target_degree: str | None = None
    loan_amount: str | None = None
    bank_name: str | None = None
    bank_status: str | None = None
    docs_required: int = 6
    docs_submitted: int = 0
    submitted_docs: list[str] = []
    dnp_count: int = 0
    # FMC multi-bank counter. bank_name / bank_status above remain the
    # "primary" bank shown on the tile; bank_count tells the FE whether
    # to render a "+N more" badge to expand into the full list.
    bank_count: int = 0
    # Top 2 banks (by status priority, then oldest first as tie-break) so
    # the Kanban tile can render two chips inline without a per-card
    # round trip. Empty list when bank_count == 0.
    top_banks: list[dict] = []
    # Latest remark on the lead (newest entry from lead_remarks).
    # Drives the Kanban tile's "Latest note" row, replacing the
    # previous "Top 3 pending tasks" section. Null when no remarks.
    latest_note: dict | None = None
    # Shared tile fields (FMC + Admitverse both render). university is
    # the lead's target college (e.g. "MIT", "Oxford"); preferred_countries
    # is a list because Admitverse leads commonly target 2-3 countries.
    # FMC FE renders the first country only as a single chip.
    university: str | None = None
    target_intake: str | None = None
    preferred_countries: list[str] = []
    budget: str | None = None

    @field_validator("preferred_countries", mode="before")
    @classmethod
    def _preferred_countries_none_to_empty(cls, v):
        # leads.preferred_countries is a nullable text[]. Pre-existing FMC
        # rows have NULL since the FMC tile never used it. Coerce to []
        # so the card schema (list[str]) always validates.
        return v or []

    assigned_agent_name: str | None = None
    assigned_agent_role: str | None = None
    pre_counsellor_name: str | None = None
    # Human-readable source label resolved from lead_source_id. Lets the
    # Kanban tile render a chip like "Source: WhatsApp Campaign" without
    # a separate /sources fetch per card.
    source_name: str | None = None
    task_count: int = 0
    call_count: int = 0
    notes_count: int = 0
    has_active_ai_campaign: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadsByStageOut(BaseModel):
    """Response for GET /leads/by-stage. Kanban fetches all stages in
    one round trip; frontend slices `items_by_stage` into columns.
    """
    items_by_stage: dict[str, list[LeadCardOut]]
    counts_by_stage: dict[str, int]
    total: int


class LeadAssign(BaseModel):
    agent_id: uuid.UUID


class LeadBulkAssign(BaseModel):
    lead_ids: list[uuid.UUID]
    agent_id: uuid.UUID


class LeadDistributeRange(BaseModel):
    """One slice of the distribution: 'leads from row from_pos to row
    to_pos, inclusive, go to agent_id'. Row positions are 1-indexed and
    refer to the position in the filtered+ordered list (not the lead's
    DB id).
    """
    from_pos: int = Field(alias="from", ge=1)
    to_pos: int = Field(alias="to", ge=1)
    agent_id: uuid.UUID

    model_config = {"populate_by_name": True}


class LeadDistributeRangeRequest(BaseModel):
    ranges: list[LeadDistributeRange]
    # If true, only distribute leads that don't have an assigned_agent
    # yet. Most common case for "distribute the firehose".
    unassigned_only: bool = True
    # Optional stage filter — e.g. only Admitverse 'created' leads.
    stage: str | None = None
    # Order in which the leads are walked before slicing into ranges.
    # Default newest first so the most recent uploads get distributed.
    order_by: str = "created_at_desc"


class LeadDistributeRangeResult(BaseModel):
    from_pos: int = Field(serialization_alias="from")
    to_pos: int = Field(serialization_alias="to")
    agent_id: uuid.UUID
    agent_name: str | None = None
    assigned_count: int

    model_config = {"populate_by_name": True}


class LeadDistributeRangeResponse(BaseModel):
    total_assigned: int
    eligible_count: int  # how many leads matched the filter total
    ranges: list[LeadDistributeRangeResult]


class LeadSearchParams(BaseModel):
    q: str | None = None
    stage: str | None = None
    agent_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    tags: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    page: int = 1
    page_size: int = 25


class LeadBankCreate(BaseModel):
    bank_name: str
    bank_status: str = "applied"
    notes: str | None = None


class LeadBankUpdate(BaseModel):
    bank_status: str | None = None
    notes: str | None = None
    # Sanction details — backend rejects these if the bank entry is not
    # yet in a sanctioned-or-later state. FMC enters them via the
    # "Sanction Details" card on the lead detail page once the bank
    # actually sanctions the loan.
    application_id: str | None = None
    sanction_date: date | None = None
    loan_amount: Decimal | None = None
    roi: Decimal | None = None
    tenure_months: int | None = None
    pf_amount: Decimal | None = None
    first_tranche_amount: Decimal | None = None
    no_of_tranches: int | None = None
    pf_status: str | None = None  # 'paid' | 'pending'


class LeadBankOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    bank_name: str
    bank_status: str
    notes: str | None = None
    application_id: str | None = None
    sanction_date: date | None = None
    loan_amount: Decimal | None = None
    roi: Decimal | None = None
    tenure_months: int | None = None
    pf_amount: Decimal | None = None
    first_tranche_amount: Decimal | None = None
    no_of_tranches: int | None = None
    pf_status: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadRemarkCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class LeadRemarkOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    author_id: uuid.UUID | None
    author_name: str | None = None  # enriched in service from profiles
    author_role: str
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadSourceCreate(BaseModel):
    name: str
    source_type: str = "manual"
    meta_form_id: str | None = None


class LeadSourceOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    source_type: str
    meta_form_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
