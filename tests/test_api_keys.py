"""API-key credential tests.

Deliberately free of database fixtures. The rest of the suite runs
against real Supabase inside a rolled-back transaction, which is fine
for CRM workflows but makes the security-critical paths here — revoked,
expired, cross-tenant — awkward to set up and slow to run. Everything
below exercises the real functions and the real middleware against
in-memory doubles instead, so it passes with no network.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.api_key import (
    generate_api_key,
    hash_api_key,
    looks_like_api_key,
    resolve_api_key,
)
from app.core.exceptions import DuplicateLeadError, UnauthorizedError
from app.core.exception_handlers import duplicate_lead_exception_handler
from app.core.middleware import ApiKeyDeleteGuardMiddleware
from app.models.api_key import ApiKey
from app.utils.date_helpers import now_utc


# ── Key generation / hashing ───────────────────────────────────────────

def test_generated_key_has_expected_shape():
    raw, prefix, key_hash = generate_api_key()
    assert raw.startswith("crmk_live_")
    assert raw.startswith(prefix)
    assert len(key_hash) == 64
    assert hash_api_key(raw) == key_hash
    # The stored prefix must not be enough to reconstruct the secret.
    assert len(prefix) < len(raw)


def test_generated_keys_are_unique():
    keys = {generate_api_key()[0] for _ in range(200)}
    assert len(keys) == 200


def test_hash_is_stable_and_differs_per_key():
    a, _, _ = generate_api_key()
    b, _, _ = generate_api_key()
    assert hash_api_key(a) == hash_api_key(a)
    assert hash_api_key(a) != hash_api_key(b)


@pytest.mark.parametrize(
    "value",
    ["", None, "not-a-key", "Bearer eyJhbGciOi", "sk_live_abc", "CRMK_live_abc"],
)
def test_malformed_values_rejected_before_db(value):
    assert looks_like_api_key(value) is False


# ── resolve_api_key ────────────────────────────────────────────────────

class _FakeProfile:
    def __init__(self, company_id, is_active=True, role="admin"):
        self.id = uuid.uuid4()
        self.company_id = company_id
        self.is_active = is_active
        self.role = role
        self.email = "svc@example.com"
        self.full_name = "Service Account"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns the seeded ApiKey for the first select, then profiles.

    Only `execute` / `commit` / `rollback` are used by resolve_api_key.
    """

    def __init__(self, api_key_row):
        self._row = api_key_row
        self.commits = 0

    async def execute(self, stmt):
        # resolve_api_key's only SELECT is the key lookup (the profile
        # comes off the relationship); the UPDATE for last_used_at
        # returns nothing anyone reads.
        return _FakeResult(self._row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _make_key(**overrides):
    company_id = overrides.pop("company_id", uuid.uuid4())
    profile = overrides.pop("profile", None) or _FakeProfile(company_id)
    raw, prefix, key_hash = generate_api_key()
    row = ApiKey(
        id=uuid.uuid4(),
        company_id=company_id,
        profile_id=profile.id,
        name="test key",
        key_prefix=prefix,
        key_hash=key_hash,
        is_active=overrides.pop("is_active", True),
        expires_at=overrides.pop("expires_at", None),
        revoked_at=overrides.pop("revoked_at", None),
    )
    row.profile = profile
    return raw, row, profile


async def test_valid_key_resolves_to_its_service_account():
    raw, row, profile = _make_key()
    resolved = await resolve_api_key(_FakeSession(row), raw)
    assert resolved is profile
    assert resolved.role == "admin"


async def test_unknown_key_rejected():
    _, row, _ = _make_key()
    other, _, _ = generate_api_key()
    session = _FakeSession(None)
    with pytest.raises(UnauthorizedError):
        await resolve_api_key(session, other)


async def test_revoked_key_rejected():
    raw, row, _ = _make_key(is_active=False, revoked_at=now_utc())
    with pytest.raises(UnauthorizedError):
        await resolve_api_key(_FakeSession(row), raw)


async def test_expired_key_rejected():
    raw, row, _ = _make_key(expires_at=now_utc() - timedelta(seconds=1))
    with pytest.raises(UnauthorizedError):
        await resolve_api_key(_FakeSession(row), raw)


async def test_future_expiry_still_valid():
    raw, row, profile = _make_key(expires_at=now_utc() + timedelta(days=30))
    assert await resolve_api_key(_FakeSession(row), raw) is profile


async def test_key_for_deactivated_service_account_rejected():
    company_id = uuid.uuid4()
    profile = _FakeProfile(company_id, is_active=False)
    raw, row, _ = _make_key(company_id=company_id, profile=profile)
    with pytest.raises(UnauthorizedError):
        await resolve_api_key(_FakeSession(row), raw)


async def test_key_rejected_when_profile_moved_tenant():
    """A key is minted for one company; if its account is now in another,
    honouring it would be a cross-tenant write."""
    profile = _FakeProfile(uuid.uuid4())
    raw, row, _ = _make_key(company_id=uuid.uuid4(), profile=profile)
    with pytest.raises(UnauthorizedError):
        await resolve_api_key(_FakeSession(row), raw)


async def test_failures_do_not_leak_which_check_failed():
    """Every rejection must look the same to the caller."""
    messages = set()
    for factory in (
        lambda: _make_key(is_active=False),
        lambda: _make_key(expires_at=now_utc() - timedelta(days=1)),
    ):
        raw, row, _ = factory()
        with pytest.raises(UnauthorizedError) as exc:
            await resolve_api_key(_FakeSession(row), raw)
        messages.add(str(exc.value.detail))
    assert len(messages) == 1


# ── DELETE guard middleware ────────────────────────────────────────────

def _guard_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ApiKeyDeleteGuardMiddleware)

    @app.delete("/leads/{lead_id}")
    async def delete_lead(lead_id: str):
        return {"deleted": lead_id}

    @app.post("/leads")
    async def create_lead():
        return {"created": True}

    @app.put("/leads/{lead_id}")
    async def update_lead(lead_id: str):
        return {"updated": lead_id}

    @app.get("/leads")
    async def list_leads():
        return {"items": []}

    return app


@pytest.fixture
async def guard_client():
    async with AsyncClient(
        transport=ASGITransport(app=_guard_app()), base_url="http://test"
    ) as client:
        yield client


async def test_api_key_cannot_delete(guard_client):
    resp = await guard_client.delete(
        "/leads/abc", headers={"X-API-Key": "crmk_live_whatever"}
    )
    assert resp.status_code == 403
    assert "not permitted to delete" in resp.json()["detail"]


async def test_delete_is_blocked_before_the_route_runs(guard_client):
    """The handler must never execute — not even to reject afterwards."""
    resp = await guard_client.delete(
        "/leads/abc", headers={"X-API-Key": "crmk_live_whatever"}
    )
    assert "deleted" not in resp.json()


async def test_jwt_delete_is_unaffected(guard_client):
    resp = await guard_client.delete(
        "/leads/abc", headers={"Authorization": "Bearer sometoken"}
    )
    assert resp.status_code == 200


@pytest.mark.parametrize("method,path", [
    ("POST", "/leads"), ("PUT", "/leads/abc"), ("GET", "/leads"),
])
async def test_api_key_may_read_and_write(guard_client, method, path):
    resp = await guard_client.request(
        method, path, headers={"X-API-Key": "crmk_live_whatever"}
    )
    assert resp.status_code == 200


async def test_guard_ignores_malformed_key_but_still_blocks_delete(guard_client):
    """The guard keys on the header's presence, not on key validity —
    an invalid key must not become a way to reach DELETE."""
    resp = await guard_client.delete("/leads/abc", headers={"X-API-Key": "junk"})
    assert resp.status_code == 403


# ── Duplicate-lead error shape ─────────────────────────────────────────

def _duplicate_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(DuplicateLeadError, duplicate_lead_exception_handler)

    @app.post("/leads")
    async def create():
        raise DuplicateLeadError(
            field="phone",
            value="+919812345678",
            existing_id=uuid.UUID("d47c2a10-9e88-4b33-8f21-a0c5b6d7e8f9"),
            existing_name="Rohit Verma",
        )

    return app


@pytest.fixture
async def duplicate_client():
    async with AsyncClient(
        transport=ASGITransport(app=_duplicate_app()), base_url="http://test"
    ) as client:
        yield client


async def test_duplicate_error_carries_existing_lead_id(duplicate_client):
    resp = await duplicate_client.post("/leads")
    assert resp.status_code == 400
    body = resp.json()
    assert body["existing_lead_id"] == "d47c2a10-9e88-4b33-8f21-a0c5b6d7e8f9"
    assert body["duplicate_field"] == "phone"
    assert body["existing_lead_name"] == "Rohit Verma"
    assert body["error_code"] == "duplicate_lead"


async def test_duplicate_error_detail_string_is_unchanged(duplicate_client):
    """The frontend renders `detail` directly — its wording and type are
    part of the contract and must not regress."""
    body = (await duplicate_client.post("/leads")).json()
    assert body["detail"] == (
        "A lead with phone +919812345678 already exists (Rohit Verma)."
    )
    assert isinstance(body["detail"], str)
