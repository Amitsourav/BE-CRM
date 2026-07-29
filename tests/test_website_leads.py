"""Website Leads inbox — ingest, triage, conversion.

The pure-schema tests at the top need no database and run anywhere. The
integration tests below follow the existing suite convention: real
Supabase session inside a transaction that is always rolled back.
"""
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.website_submission import WebsiteLeadIngest


# ── Schema / validation (no DB) ────────────────────────────────────────

def test_ingest_requires_form_key():
    with pytest.raises(ValidationError):
        WebsiteLeadIngest(email="a@b.com")


def test_has_contact_true_with_email_only():
    assert WebsiteLeadIngest(form_key="av_contact", email="a@b.com").has_contact()


def test_has_contact_true_with_phone_only():
    assert WebsiteLeadIngest(form_key="av_contact", phone="9876543210").has_contact()


def test_has_contact_false_when_both_missing():
    assert not WebsiteLeadIngest(form_key="av_contact", full_name="Test").has_contact()


def test_has_contact_false_on_whitespace_only():
    """A form posting empty strings shouldn't count as a contactable lead."""
    assert not WebsiteLeadIngest(form_key="av_contact", email="  ", phone="").has_contact()


def test_extra_fields_default_empty_and_accept_arbitrary_keys():
    body = WebsiteLeadIngest(form_key="f")
    assert body.extra_fields == {}
    body2 = WebsiteLeadIngest(
        form_key="f", email="a@b.com",
        extra_fields={"course": "MSc CS", "utm_source": "google"},
    )
    assert body2.extra_fields["course"] == "MSc CS"


# ── Ingest endpoint auth ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_rejects_missing_secret(unauth_client):
    resp = await unauth_client.post(
        "/api/v1/internal/website/ingest",
        json={"form_key": "av_contact", "email": "x@y.com"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_ingest_rejects_wrong_secret(unauth_client, monkeypatch):
    from app.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "website_lead_secret", "the-real-secret", raising=False)
    resp = await unauth_client.post(
        "/api/v1/internal/website/ingest",
        headers={"X-Internal-Secret": "not-the-secret"},
        json={"form_key": "av_contact", "email": "x@y.com"},
    )
    assert resp.status_code == 403


# ── Ingest → panel → convert (DB) ──────────────────────────────────────

@pytest.fixture
def ingest_payload():
    unique = uuid.uuid4().hex[:8]
    return {
        "form_key": "av_contact",
        "form_name": "AV — Contact Form",
        "full_name": f"Test Person {unique}",
        "email": f"test_{unique}@example.com",
        "phone": f"98765{unique[:5]}",
        "message": "I want to study in Germany",
        "source": "website",
        "page": "/contact",
        "extra_fields": {"interested_country": "Germany"},
    }


@pytest.mark.asyncio
async def test_ingest_stores_submission_not_lead(db_session, admin_user, ingest_payload):
    """A submission must NOT create a Lead — that's the whole point of the
    waiting list."""
    from sqlalchemy import select
    from app.models.lead import Lead
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, replay = await service.ingest(WebsiteLeadIngest(**ingest_payload))

    assert replay is False
    assert submission.status == "new"
    assert submission.form_key == "av_contact"
    assert submission.payload["interested_country"] == "Germany"

    lead = (await db_session.execute(
        select(Lead).where(Lead.email == ingest_payload["email"])
    )).scalar_one_or_none()
    assert lead is None, "ingest must not create a lead directly"


@pytest.mark.asyncio
async def test_ingest_normalizes_phone_and_lowercases_email(db_session, admin_user):
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(
        form_key="f", email="MiXeD@Case.COM", phone="9876543210",
    ))
    assert submission.email == "mixed@case.com"
    assert submission.phone.startswith("+91")


@pytest.mark.asyncio
async def test_external_id_makes_retry_idempotent(db_session, admin_user, ingest_payload):
    """The website retrying a failed POST must not create two rows."""
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    payload = {**ingest_payload, "external_id": f"sub_{uuid.uuid4().hex[:10]}"}
    service = WebsiteSubmissionService(db_session, admin_user.company_id)

    first, replay1 = await service.ingest(WebsiteLeadIngest(**payload))
    second, replay2 = await service.ingest(WebsiteLeadIngest(**payload))

    assert replay1 is False and replay2 is True
    assert first.id == second.id


@pytest.mark.asyncio
async def test_convert_creates_lead_and_marks_converted(db_session, admin_user, ingest_payload):
    from app.schemas.website_submission import WebsiteLeadIngest, WebsiteSubmissionConvert
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))

    lead = await service.convert(submission.id, WebsiteSubmissionConvert(), admin_user)

    assert lead.email == ingest_payload["email"]
    assert lead.custom_fields["website_form"] == "av_contact"
    assert lead.custom_fields["interested_country"] == "Germany"
    assert "I want to study in Germany" in (lead.notes or "")
    assert lead.serial_no is not None

    refreshed = await service.get(submission.id)
    assert refreshed.status == "converted"
    assert refreshed.lead_id == lead.id
    assert refreshed.reviewed_by == admin_user.id


@pytest.mark.asyncio
async def test_convert_twice_is_rejected(db_session, admin_user, ingest_payload):
    from app.core.exceptions import BadRequestError
    from app.schemas.website_submission import WebsiteLeadIngest, WebsiteSubmissionConvert
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))
    await service.convert(submission.id, WebsiteSubmissionConvert(), admin_user)

    with pytest.raises(BadRequestError):
        await service.convert(submission.id, WebsiteSubmissionConvert(), admin_user)


@pytest.mark.asyncio
async def test_convert_duplicate_links_existing_lead(db_session, admin_user, ingest_payload):
    """Second submission from the same person → 409 + status='duplicate',
    linked to the lead that already exists."""
    from app.core.exceptions import ConflictError
    from app.schemas.website_submission import WebsiteLeadIngest, WebsiteSubmissionConvert
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    first, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))
    lead = await service.convert(first.id, WebsiteSubmissionConvert(), admin_user)

    second, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))
    # Ingest already flags the match so the panel can warn before clicking.
    assert second.lead_id == lead.id

    with pytest.raises(ConflictError):
        await service.convert(second.id, WebsiteSubmissionConvert(), admin_user)

    refreshed = await service.get(second.id)
    assert refreshed.status == "duplicate"
    assert refreshed.lead_id == lead.id


@pytest.mark.asyncio
async def test_spam_then_reopen(db_session, admin_user, ingest_payload):
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))

    spammed = await service.mark_spam(submission.id, admin_user)
    assert spammed.status == "spam"

    reopened = await service.reopen(submission.id, admin_user)
    assert reopened.status == "new"


@pytest.mark.asyncio
async def test_converted_cannot_be_marked_spam(db_session, admin_user, ingest_payload):
    from app.core.exceptions import BadRequestError
    from app.schemas.website_submission import WebsiteLeadIngest, WebsiteSubmissionConvert
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))
    await service.convert(submission.id, WebsiteSubmissionConvert(), admin_user)

    with pytest.raises(BadRequestError):
        await service.mark_spam(submission.id, admin_user)


@pytest.mark.asyncio
async def test_panel_list_and_counts(admin_client, db_session, admin_user, ingest_payload):
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    await service.ingest(WebsiteLeadIngest(**ingest_payload))

    resp = await admin_client.get("/api/v1/website-leads?status=new")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(i["form_key"] == "av_contact" for i in body["items"])

    counts = await admin_client.get("/api/v1/website-leads/count")
    assert counts.status_code == 200
    assert counts.json()["new"] >= 1

    forms = await admin_client.get("/api/v1/website-leads/forms")
    assert forms.status_code == 200
    assert any(f["form_key"] == "av_contact" for f in forms.json())


@pytest.mark.asyncio
async def test_panel_is_tenant_scoped(db_session, admin_user, ingest_payload):
    """A submission for one company must be invisible to another."""
    from app.core.exceptions import NotFoundError
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(**ingest_payload))

    other = WebsiteSubmissionService(db_session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        await other.get(submission.id)


# ── Free-text phone fields (regression: 500 on long values) ────────────

@pytest.mark.asyncio
async def test_long_unparseable_phone_is_parked_not_crashed(db_session, admin_user):
    """normalize_phone passes non-Indian-format input through untouched.
    A website visitor typing "98765 43210 call after 6pm" must not 500 the
    ingest — the raw value is parked in payload instead."""
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    messy = "98765 43210 call after 6pm please"
    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(
        form_key="av_contact", email="messyphone@example.com", phone=messy,
    ))

    assert submission.phone is None
    assert submission.payload["phone_raw"] == messy


@pytest.mark.asyncio
async def test_normal_phone_still_normalized(db_session, admin_user):
    from app.schemas.website_submission import WebsiteLeadIngest
    from app.services.website_submission_service import WebsiteSubmissionService

    service = WebsiteSubmissionService(db_session, admin_user.company_id)
    submission, _ = await service.ingest(WebsiteLeadIngest(
        form_key="av_contact", email="ok@example.com", phone="98765 43210",
    ))
    assert submission.phone == "+919876543210"
    assert "phone_raw" not in submission.payload


# ── Phone normalisation (dedupe depends on these agreeing) ─────────────

def test_normalize_phone_variants_all_agree():
    """Every way a real person writes one Indian mobile must normalise to
    the same string, or the duplicate check silently fails and the CRM
    grows two copies of the same lead."""
    from app.utils.csv_parser import normalize_phone

    expected = "+917004428198"
    for written in [
        "7004428198",          # bare
        "07004428198",         # domestic trunk prefix — the website case
        "917004428198",        # country code
        "+917004428198",       # e164
        "+91 70044 28198",     # spaced
        "0091 7004428198",     # international dialling prefix
        "070-044-28198",       # punctuated
    ]:
        assert normalize_phone(written) == expected, f"{written!r} did not normalise"


def test_normalize_phone_leaves_unparseable_untouched():
    """Junk must come back unchanged so callers can spot it, not be
    silently truncated into a wrong-but-plausible number."""
    from app.utils.csv_parser import normalize_phone

    assert normalize_phone("98765 43210 call after 6pm") == "98765 43210 call after 6pm"
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
