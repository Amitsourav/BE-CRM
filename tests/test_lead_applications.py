"""Brand-gating + FMC non-regression tests for the Admitverse
university-application subsystem and the brand-scoped dropdowns.

These run against the FMC test DB (the fixtures' company). They verify the
FMC side: bank endpoints still work, and the new AV-only endpoints reject
or return empty on FMC. Full AV-side CRUD (add/update/resync) is exercised
in CI once an Admitverse company + the lead_applications migration are
present in the test DB.
"""
import pytest

from app.core.constants import FMC_BANKS, FMC_DOC_KEYS


@pytest.mark.asyncio
class TestFMCNonRegression:
    async def test_banks_dropdown_still_returns_fmc_banks(self, admin_client):
        resp = await admin_client.get("/api/v1/leads/banks")
        assert resp.status_code == 200
        banks = resp.json()
        assert set(banks) == set(FMC_BANKS)

    async def test_docs_checklist_returns_fmc_list(self, admin_client):
        resp = await admin_client.get("/api/v1/leads/docs/checklist")
        assert resp.status_code == 200
        keys = {item["key"] for item in resp.json()["items"]}
        assert keys == set(FMC_DOC_KEYS)

    async def test_universities_empty_for_fmc(self, admin_client):
        resp = await admin_client.get("/api/v1/leads/universities")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.asyncio
class TestApplicationBrandGate:
    async def test_add_application_rejected_on_fmc(self, admin_client, sample_lead):
        """Application tracking is Admitverse-only — FMC must 400."""
        resp = await admin_client.post(
            f"/api/v1/leads/{sample_lead.id}/applications",
            json={"university_name": "University of Oxford"},
        )
        assert resp.status_code == 400
        assert "admitverse" in resp.json()["detail"].lower() or "application" in resp.json()["detail"].lower()
