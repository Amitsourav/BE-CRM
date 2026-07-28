"""Website lead capture — public ingest + the Website Leads review panel.

Two routers live here:

  ingest_router  POST /internal/website/ingest   ← the marketing websites
  router         /website-leads/*                ← the CRM panel (Manager+)

IMPORTANT: this module deliberately does NOT use
`from __future__ import annotations`. slowapi's @limiter.limit wrapper
hides the real signature from FastAPI, and with postponed annotations
FastAPI can no longer resolve the Pydantic body model — it silently
demotes the body to a query parameter and every request 422s. Keep
annotations concrete here (Optional[X], not X | None).
"""
import hmac
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.rate_limit import limiter
from app.core.tenant import get_current_company_id
from app.db.session import get_db
from app.dependencies import get_current_manager
from app.models.profile import Profile
from app.schemas.common import PaginatedResponse
from app.schemas.lead import LeadOut
from app.schemas.website_submission import (
    WebsiteFormOut,
    WebsiteLeadIngest,
    WebsiteLeadIngestResponse,
    WebsiteSubmissionConvert,
    WebsiteSubmissionCounts,
    WebsiteSubmissionOut,
)
from app.services.website_submission_service import (
    WebsiteSubmissionService,
    resolve_ingest_company,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/website-leads", tags=["Website Leads"])
ingest_router = APIRouter(prefix="/internal", tags=["Internal"])


# ── Public ingest ──────────────────────────────────────────────────────
#
# Every lead form on admitverse.com / fundmycampus.com POSTs here. The
# submission is stored for review — it does NOT become a Lead until a
# human converts it, because these forms are open to the public and
# collect their share of junk.
#
# Brand-agnostic by design: the same codebase is deployed as both the FMC
# and AV backends, so each site posts to its own backend URL with its own
# WEBSITE_LEAD_SECRET and lands in its own database.


@ingest_router.post(
    "/website/ingest",
    response_model=WebsiteLeadIngestResponse,
    status_code=201,
)
@limiter.limit("60/minute")
async def internal_website_ingest(
    request: Request,
    body: WebsiteLeadIngest,
    x_internal_secret: Optional[str] = Header(None, alias="X-Internal-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Accept one lead-form submission from a marketing website.

    Auth is a shared secret in `X-Internal-Secret` (`WEBSITE_LEAD_SECRET`,
    falling back to `INTERNAL_META_SECRET`). Rate-limited per IP so a
    scripted spam run can't fill the inbox faster than anyone can triage.
    """
    settings = get_settings()
    expected = settings.website_lead_secret or settings.internal_meta_secret
    # Constant-time compare — a plain != leaks secret length and prefix
    # to a patient attacker through response timing.
    if not expected or not x_internal_secret or not hmac.compare_digest(
        x_internal_secret, expected
    ):
        logger.warning("Website ingest: bad or missing secret (form=%s)", body.form_key)
        raise HTTPException(status_code=403, detail="Forbidden")

    if not body.has_contact():
        raise HTTPException(
            status_code=422,
            detail="Submission needs at least an email or a phone number",
        )

    company_id = await resolve_ingest_company(db, body.company_slug)
    service = WebsiteSubmissionService(db, company_id)
    submission, is_replay = await service.ingest(body)

    logger.info(
        "Website ingest: %s submission %s form=%s page=%s tenant=%s%s",
        "replayed" if is_replay else "stored",
        submission.id, submission.form_key, submission.page, company_id,
        " (matches existing lead)" if submission.lead_id else "",
    )
    return WebsiteLeadIngestResponse(
        status="duplicate_submission" if is_replay else "ok",
        submission_id=submission.id,
        matched_lead_id=submission.lead_id,
    )


# ── Review panel (Manager+) ────────────────────────────────────────────
#
# Pre-Counsellors work the leads they're given; deciding which raw
# submissions become leads is a supervisory call — same gate the CSV
# importer uses.


@router.get("", response_model=PaginatedResponse[WebsiteSubmissionOut])
async def list_website_leads(
    status: Optional[str] = Query(
        "new",
        description="new | converted | duplicate | spam. Pass an empty string for all.",
    ),
    form_key: Optional[str] = Query(None, description="Filter to one form"),
    q: Optional[str] = Query(None, description="Search name / email / phone"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = WebsiteSubmissionService(db, company_id)
    return await service.list_submissions(
        status=(status or None), form_key=form_key, q=q, page=page, page_size=page_size,
    )


@router.get("/count", response_model=WebsiteSubmissionCounts)
async def website_lead_counts(
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Badge counts per status — drives the panel tabs and sidebar dot."""
    service = WebsiteSubmissionService(db, company_id)
    return await service.counts()


@router.get("/forms", response_model=list[WebsiteFormOut])
async def website_lead_forms(
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Every form that has ever submitted, with totals — filter dropdown."""
    service = WebsiteSubmissionService(db, company_id)
    return await service.forms()


@router.get("/{submission_id}", response_model=WebsiteSubmissionOut)
async def get_website_lead(
    submission_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = WebsiteSubmissionService(db, company_id)
    return await service.get(submission_id)


@router.post("/{submission_id}/convert", response_model=LeadOut, status_code=201)
async def convert_website_lead(
    submission_id: uuid.UUID,
    body: Optional[WebsiteSubmissionConvert] = None,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a Lead from a submission.

    Returns 409 if an active lead already has that email/phone; the
    submission is then marked `duplicate` and linked to the existing lead.
    """
    service = WebsiteSubmissionService(db, company_id)
    return await service.convert(
        submission_id, body or WebsiteSubmissionConvert(), current_user,
    )


@router.post("/{submission_id}/spam", response_model=WebsiteSubmissionOut)
async def mark_website_lead_spam(
    submission_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = WebsiteSubmissionService(db, company_id)
    return await service.mark_spam(submission_id, current_user)


@router.post("/{submission_id}/reopen", response_model=WebsiteSubmissionOut)
async def reopen_website_lead(
    submission_id: uuid.UUID,
    current_user: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Undo a spam / duplicate marking — puts the row back in the queue."""
    service = WebsiteSubmissionService(db, company_id)
    return await service.reopen(submission_id, current_user)
