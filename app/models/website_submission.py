from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, ForeignKey, text, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.models.base import Base, TimestampMixin


class WebsiteSubmission(Base, TimestampMixin):
    """Raw lead-form submission from a marketing website, held for review.

    Deliberately NOT a Lead. Website forms are open to the public, so
    they collect a mix of real enquiries, half-filled rows, and outright
    junk ("test test"). Dropping all of that straight into the pipeline
    would pollute the Kanban and the reports. Instead every submission
    lands here as `status='new'`, a human triages it in the Website Leads
    panel, and only then does it become a Lead (`status='converted'`).

    One row per submit — repeat submissions from the same person are kept
    as separate rows on purpose, because "filled the form three times" is
    itself a buying signal the counsellor should see. The only dedupe is
    `external_id`, which exists so a website retrying a failed POST can't
    create two rows for one human action.

    The full form body is preserved in `payload` so a form can add fields
    without a backend change, and so a bad field mapping is recoverable
    after the fact.
    """
    __tablename__ = "website_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'converted', 'duplicate', 'spam')",
            name="website_submission_status_chk",
        ),
        # Panel default view: newest-first within a status, per tenant.
        Index(
            "idx_website_subs_company_status_created",
            "company_id", "status", "created_at",
        ),
        # "Is this person already in the CRM?" lookups at ingest time.
        Index("idx_website_subs_company_email", "company_id", "email"),
        Index("idx_website_subs_company_phone", "company_id", "phone"),
        # Idempotency for website-side retries. Partial so the common
        # case (no external id supplied) never collides.
        Index(
            "uniq_website_subs_external_id",
            "company_id", "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
    )

    # ── Which form produced this ──────────────────────────────────────
    # form_key is the stable machine id the website sends ("av_contact",
    # "fmc_eligibility"). form_name is the human label shown in the panel
    # and used to name the auto-created LeadSource on conversion.
    form_key: Mapped[str] = mapped_column(String(80), nullable=False)
    form_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    page: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tag: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    # ── The person ────────────────────────────────────────────────────
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 32, not 20: website phone fields are free text and normalize_phone
    # passes anything that isn't a clean 10/12-digit Indian number through
    # untouched ("98765 43210 call after 6pm"). Anything longer than this
    # is parked in payload["phone_raw"] by the service rather than
    # truncated — see WebsiteSubmissionService.ingest.
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Everything the form sent, verbatim. Survives form changes and lets
    # a counsellor read fields we never modelled (course, intake, budget…).
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    # Website-supplied idempotency key (their own submission id).
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Triage state ──────────────────────────────────────────────────
    #   new       — waiting for a human
    #   converted — became `lead_id`
    #   duplicate — matched an existing lead (`lead_id` points at it)
    #   spam      — dismissed, never becomes a lead
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'new'"))

    # Set at ingest when an existing active lead already has this
    # email/phone, so the panel can show "already in CRM" before anyone
    # clicks Convert. Also set on convert (the created or matched lead).
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
