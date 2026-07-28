from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class WebsiteLeadIngest(BaseModel):
    """Public-ish ingest payload posted by a marketing website.

    Everything except `form_key` is optional because the forms differ
    wildly — a contact form has a message, the eligibility checker has a
    loan amount, the SOP tool has just an email. Unmapped fields belong
    in `extra_fields` and are preserved verbatim.

    Validation is deliberately loose (see `has_contact`): rejecting a
    real enquiry because a phone number looked odd is worse than storing
    a row someone marks as spam in three seconds.
    """
    form_key: str = Field(min_length=1, max_length=80, description="Stable form id, e.g. 'av_contact'")
    form_name: Optional[str] = Field(default=None, max_length=120, description="Human label for the panel")

    full_name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=200)
    # Generous cap: free-text phone fields collect things like
    # "98765 43210 (call after 6pm)". Anything that survives this but is
    # too wide for the column gets parked in payload["phone_raw"] rather
    # than rejected — losing a real enquiry over a messy phone field is
    # worse than storing it for a human to read.
    phone: Optional[str] = Field(default=None, max_length=120)
    message: Optional[str] = None

    source: Optional[str] = Field(default=None, max_length=80)
    page: Optional[str] = Field(default=None, description="Page path/URL the form was on")
    tag: Optional[str] = Field(default=None, max_length=80)

    # Website's own submission id — makes a retried POST idempotent.
    external_id: Optional[str] = Field(default=None, max_length=100)

    # Anything else the form collected (course, intake, budget, utm_*, …).
    extra_fields: dict = Field(default_factory=dict)

    # Only needed if one backend ever serves multiple companies. Normally
    # omitted — each brand has its own deployment and DB.
    company_slug: Optional[str] = Field(default=None, max_length=80)

    def has_contact(self) -> bool:
        """A submission is useless without some way to reach the person."""
        return bool((self.email or "").strip() or (self.phone or "").strip())


class WebsiteLeadIngestResponse(BaseModel):
    status: str  # "ok" | "duplicate_submission"
    submission_id: uuid.UUID
    # Populated when an active lead with this email/phone already exists,
    # so the website can be told the person is already known if it cares.
    matched_lead_id: Optional[uuid.UUID] = None


class WebsiteSubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    form_key: str
    form_name: Optional[str] = None
    source: Optional[str] = None
    page: Optional[str] = None
    tag: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None
    payload: dict = {}
    external_id: Optional[str] = None
    status: str
    lead_id: Optional[uuid.UUID] = None
    reviewed_by: Optional[uuid.UUID] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


class WebsiteSubmissionCounts(BaseModel):
    """Badge counts for the panel's status tabs."""
    new: int = 0
    converted: int = 0
    duplicate: int = 0
    spam: int = 0
    total: int = 0


class WebsiteFormOut(BaseModel):
    """One entry in the panel's form filter dropdown."""
    form_key: str
    form_name: Optional[str] = None
    total: int
    new: int


class WebsiteSubmissionConvert(BaseModel):
    """Optional overrides applied when turning a submission into a Lead.

    All optional — the default conversion just uses what the form sent.
    """
    assigned_agent_id: Optional[uuid.UUID] = Field(default=None, description="Counsellor to own the lead")
    pre_counsellor_id: Optional[uuid.UUID] = None
    lead_source_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Override the auto-created per-form source",
    )
    # Lets the reviewer fix a typo'd name/phone before creating the lead
    # instead of creating a bad lead and editing it afterwards.
    full_name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=30)
    notes: Optional[str] = None
