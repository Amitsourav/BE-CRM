"""Website Leads inbox — ingest, triage, and conversion to Leads.

Flow:

    website form → POST /internal/website/ingest → website_submissions
                                                     (status='new')
                          ↓ human reviews in the panel
              convert → Lead (+ per-form LeadSource)   status='converted'
              spam    → dismissed                      status='spam'

Kept separate from LeadService on purpose: a submission is untrusted
public input, a Lead is a record the team works. The only bridge is
`convert()`.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import LeadSourceType
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.company import Company
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.profile import Profile
from app.models.website_submission import WebsiteSubmission
from app.utils.csv_parser import normalize_phone
from app.utils.date_helpers import now_utc
from app.utils.pagination import paginate

logger = logging.getLogger(__name__)

VALID_STATUSES = ("new", "converted", "duplicate", "spam")

# Must match WebsiteSubmission.phone column width.
PHONE_MAX_LEN = 32


async def resolve_ingest_company(db: AsyncSession, company_slug: Optional[str]) -> uuid.UUID:
    """Work out which tenant an unauthenticated website POST belongs to.

    Each brand runs its own deployment against its own database, so in
    practice there is exactly one company row and the answer is obvious.
    The explicit-slug and first-admin paths exist so this doesn't silently
    pick the wrong tenant if that ever stops being true.
    """
    if company_slug:
        row = (await db.execute(
            select(Company.id).where(func.lower(Company.slug) == company_slug.strip().lower())
        )).first()
        if not row:
            raise BadRequestError(f"Unknown company_slug '{company_slug}'")
        return row[0]

    company_ids = (await db.execute(select(Company.id).limit(2))).scalars().all()
    if len(company_ids) == 1:
        return company_ids[0]
    if not company_ids:
        raise BadRequestError("No company exists on this deployment")

    # Multi-company DB and no slug given — fall back to the first admin's
    # tenant (same rule the Meta internal ingest uses) but say so loudly,
    # because this is a guess.
    admin = (await db.execute(
        select(Profile).where(Profile.role == "admin").limit(1)
    )).scalar_one_or_none()
    if not admin or not admin.company_id:
        raise BadRequestError("Cannot resolve company — pass company_slug")
    logger.warning(
        "Website ingest: %d companies on this DB and no company_slug — "
        "defaulting to admin's company %s",
        len(company_ids), admin.company_id,
    )
    return admin.company_id


class WebsiteSubmissionService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    # ── Ingest ────────────────────────────────────────────────────────

    async def ingest(self, data) -> tuple[WebsiteSubmission, bool]:
        """Store one submission. Returns (row, is_replay).

        `is_replay` is True when `external_id` matched an existing row —
        the website retried a POST it already made. We return the original
        row instead of creating a second one, so the retry looks like a
        success to the visitor without double-counting the lead.
        """
        email = (data.email or "").strip().lower() or None
        phone = normalize_phone(data.phone) if (data.phone or "").strip() else None
        external_id = (data.external_id or "").strip() or None

        # normalize_phone returns the input untouched when it isn't a clean
        # 10/12-digit Indian number, and website phone fields are free text
        # ("98765 43210 call after 6pm"). Anything too long for the column
        # would 500 the request and lose the lead, so park the raw value in
        # payload instead of truncating it into a wrong number.
        extra = dict(data.extra_fields or {})
        if phone and len(phone) > PHONE_MAX_LEN:
            extra["phone_raw"] = phone
            logger.info(
                "Website ingest: unparseable phone (%d chars) parked in payload for form=%s",
                len(phone), data.form_key,
            )
            phone = None

        if external_id:
            existing = (await self.db.execute(
                select(WebsiteSubmission).where(
                    WebsiteSubmission.company_id == self.company_id,
                    WebsiteSubmission.external_id == external_id,
                )
            )).scalar_one_or_none()
            if existing:
                logger.info(
                    "Website ingest: replay of external_id=%s → submission %s",
                    external_id, existing.id,
                )
                return existing, True

        # Flag (don't block) an existing lead with the same contact, so
        # the reviewer sees "already in CRM" before clicking Convert.
        matched_lead_id = await self._find_matching_lead(email, phone)

        submission = WebsiteSubmission(
            company_id=self.company_id,
            form_key=data.form_key.strip(),
            form_name=(data.form_name or "").strip() or None,
            source=(data.source or "").strip() or None,
            page=(data.page or "").strip() or None,
            tag=(data.tag or "").strip() or None,
            full_name=(data.full_name or "").strip() or None,
            email=email,
            phone=phone,
            message=(data.message or "").strip() or None,
            payload=extra,
            external_id=external_id,
            status="new",
            lead_id=matched_lead_id,
        )
        self.db.add(submission)
        try:
            await self.db.commit()
        except IntegrityError:
            # Concurrent retry won the race on the external_id index.
            await self.db.rollback()
            if external_id:
                existing = (await self.db.execute(
                    select(WebsiteSubmission).where(
                        WebsiteSubmission.company_id == self.company_id,
                        WebsiteSubmission.external_id == external_id,
                    )
                )).scalar_one_or_none()
                if existing:
                    return existing, True
            raise
        await self.db.refresh(submission)
        return submission, False

    async def _find_matching_lead(
        self, email: Optional[str], phone: Optional[str],
    ) -> Optional[uuid.UUID]:
        """Existing active lead with the same email or phone, if any."""
        clauses = []
        if email:
            clauses.append(func.lower(Lead.email) == email)
        if phone:
            clauses.append(Lead.phone == phone)
        if not clauses:
            return None
        row = (await self.db.execute(
            select(Lead.id).where(
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
                or_(*clauses),
            ).limit(1)
        )).first()
        return row[0] if row else None

    # ── Panel reads ───────────────────────────────────────────────────

    async def list_submissions(
        self,
        *,
        status: Optional[str] = None,
        form_key: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        query = select(WebsiteSubmission).where(
            WebsiteSubmission.company_id == self.company_id
        )
        if status:
            if status not in VALID_STATUSES:
                raise BadRequestError(f"status must be one of {', '.join(VALID_STATUSES)}")
            query = query.where(WebsiteSubmission.status == status)
        if form_key:
            query = query.where(WebsiteSubmission.form_key == form_key)
        if q:
            term = f"%{q.strip().lower()}%"
            query = query.where(or_(
                func.lower(WebsiteSubmission.full_name).like(term),
                func.lower(WebsiteSubmission.email).like(term),
                WebsiteSubmission.phone.like(term),
            ))
        query = query.order_by(WebsiteSubmission.created_at.desc())
        return await paginate(self.db, query, page, page_size)

    async def counts(self) -> dict:
        rows = (await self.db.execute(
            select(WebsiteSubmission.status, func.count())
            .where(WebsiteSubmission.company_id == self.company_id)
            .group_by(WebsiteSubmission.status)
        )).all()
        out = {s: 0 for s in VALID_STATUSES}
        for status, count in rows:
            out[status] = count
        out["total"] = sum(out[s] for s in VALID_STATUSES)
        return out

    async def forms(self) -> list[dict]:
        """Distinct forms seen, with totals — powers the filter dropdown."""
        rows = (await self.db.execute(
            select(
                WebsiteSubmission.form_key,
                func.max(WebsiteSubmission.form_name),
                func.count(),
                func.count().filter(WebsiteSubmission.status == "new"),
            )
            .where(WebsiteSubmission.company_id == self.company_id)
            .group_by(WebsiteSubmission.form_key)
            .order_by(func.count().desc())
        )).all()
        return [
            {"form_key": k, "form_name": name, "total": total, "new": new}
            for k, name, total, new in rows
        ]

    async def get(self, submission_id: uuid.UUID) -> WebsiteSubmission:
        row = (await self.db.execute(
            select(WebsiteSubmission).where(
                WebsiteSubmission.id == submission_id,
                WebsiteSubmission.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not row:
            raise NotFoundError("Submission not found")
        return row

    # ── Triage actions ────────────────────────────────────────────────

    async def convert(
        self, submission_id: uuid.UUID, overrides, user: Profile,
    ) -> Lead:
        """Turn a submission into a Lead.

        Raises ConflictError (409) if an active lead already has this
        email/phone — the submission is marked `duplicate` and linked to
        that lead so the panel can show the reviewer where the person
        already lives instead of leaving a dead row behind.
        """
        submission = await self.get(submission_id)
        if submission.status == "converted":
            raise BadRequestError("Submission is already converted")

        full_name = (overrides.full_name or submission.full_name or "").strip()
        email = ((overrides.email or submission.email) or "").strip().lower() or None
        phone_raw = overrides.phone or submission.phone
        phone = normalize_phone(phone_raw) if phone_raw else None

        if not email and not phone:
            raise BadRequestError("Cannot convert — submission has no email or phone")

        # Pre-flight duplicate check so we can link the existing lead
        # rather than letting create_lead's 400 surface with no context.
        matched = await self._find_matching_lead(email, phone)
        if matched:
            submission.status = "duplicate"
            submission.lead_id = matched
            submission.reviewed_by = user.id
            submission.reviewed_at = now_utc()
            await self.db.commit()
            raise ConflictError(
                f"A lead with this contact already exists (lead_id={matched}). "
                f"Submission marked as duplicate."
            )

        source_id = overrides.lead_source_id or await self._get_or_create_source(submission)

        # Everything the form sent, kept on the lead for the counsellor.
        custom_fields = {
            "website_form": submission.form_key,
            "website_form_name": submission.form_name,
            "website_source": submission.source,
            "website_page": submission.page,
            "website_tag": submission.tag,
            "website_submission_id": str(submission.id),
            **(submission.payload or {}),
        }
        custom_fields = {k: v for k, v in custom_fields.items() if v is not None}

        notes_parts = []
        if submission.message:
            notes_parts.append(f"Website message: {submission.message}")
        if overrides.notes:
            notes_parts.append(overrides.notes)

        data = {
            "full_name": full_name or "Website Lead",
            "email": email,
            "phone": phone,
            "lead_source_id": source_id,
            "custom_fields": custom_fields,
            "notes": ("\n\n".join(notes_parts) or None),
        }
        if overrides.assigned_agent_id:
            data["assigned_agent_id"] = overrides.assigned_agent_id
        if overrides.pre_counsellor_id:
            data["pre_counsellor_id"] = overrides.pre_counsellor_id

        # creator_role=None so LeadService's auto-own rule doesn't quietly
        # assign the lead to whoever happened to click Convert.
        from app.services.lead_service import LeadService
        lead = await LeadService(self.db, self.company_id).create_lead(
            data, user.id, creator_role=None,
        )

        submission.status = "converted"
        submission.lead_id = lead.id
        submission.reviewed_by = user.id
        submission.reviewed_at = now_utc()
        await self.db.commit()

        logger.info(
            "WEBSITE_LEAD_CONVERTED submission=%s → lead=%s (#%s) form=%s by=%s",
            submission.id, lead.id, lead.serial_no, submission.form_key, user.id,
        )
        return lead

    async def mark_spam(self, submission_id: uuid.UUID, user: Profile) -> WebsiteSubmission:
        submission = await self.get(submission_id)
        if submission.status == "converted":
            raise BadRequestError("Cannot mark a converted submission as spam")
        submission.status = "spam"
        submission.reviewed_by = user.id
        submission.reviewed_at = now_utc()
        await self.db.commit()
        await self.db.refresh(submission)
        return submission

    async def reopen(self, submission_id: uuid.UUID, user: Profile) -> WebsiteSubmission:
        """Undo a spam/duplicate marking — put the row back in the queue."""
        submission = await self.get(submission_id)
        if submission.status == "converted":
            raise BadRequestError("Cannot reopen a converted submission")
        submission.status = "new"
        submission.reviewed_by = user.id
        submission.reviewed_at = now_utc()
        await self.db.commit()
        await self.db.refresh(submission)
        return submission

    # ── Internals ─────────────────────────────────────────────────────

    async def _get_or_create_source(self, submission: WebsiteSubmission) -> Optional[uuid.UUID]:
        """One LeadSource per form, so the sources report splits by form.

        `lead_sources.name` is globally unique (not per-tenant), so the
        IntegrityError path re-selects instead of failing the conversion.
        """
        name = (submission.form_name or submission.form_key).strip()
        if not name:
            return None

        existing = (await self.db.execute(
            select(LeadSource).where(LeadSource.name == name)
        )).scalar_one_or_none()
        if existing:
            return existing.id

        source = LeadSource(
            company_id=self.company_id,
            name=name,
            source_type=LeadSourceType.WEBSITE.value,
        )
        self.db.add(source)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            existing = (await self.db.execute(
                select(LeadSource).where(LeadSource.name == name)
            )).scalar_one_or_none()
            return existing.id if existing else None
        return source.id
