"""Bank shares — "this lead's file went to this bank", plus the
per-(lead, bank) conversation and the grid that reads them.

Built on lead_banks rather than a parallel table: that table is already
exactly one row per (lead, bank) and two structures recording the same
relationship would drift.

Uses admin_client only — the agent_* fixtures are broken by the stale
UserRole.TELECALLER reference in conftest.
"""
import uuid

import pytest

from app.core.constants import FMC_BANKS


BANK = "PNB"
OTHER_BANK = "SBI"


def _phone() -> str:
    return f"+91{uuid.uuid4().int % 10**10:010d}"


async def _lead(client) -> dict:
    r = await client.post("/api/v1/leads", json={
        "full_name": "Bank Share Test", "phone": _phone(),
    })
    assert r.status_code == 201, r.text
    return r.json()


# ── Recording a share ──────────────────────────────────────────────────

async def test_record_share_creates_row(admin_client, admin_user):
    lead = await _lead(admin_client)
    r = await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={
        "bank_name": BANK, "shared_by": str(admin_user.id),
        "wa_group_id": "grp-pnb-001",
    })
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["bank_name"] == BANK
    assert b["shared_at"] is not None
    assert b["shared_by"] == str(admin_user.id)
    assert b["source"] == "whatsapp"
    assert b["wa_group_id"] == "grp-pnb-001"


async def test_record_share_is_idempotent(admin_client):
    """A lead re-shared into the same group must not create a second row."""
    lead = await _lead(admin_client)
    first = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    assert first.status_code == 201
    again = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]

    listed = await admin_client.get(f"/api/v1/leads/{lead['id']}/bank-shares")
    assert len([s for s in listed.json() if s["bank_name"] == BANK]) == 1


async def test_reshare_keeps_original_shared_at(admin_client):
    """The grid answers 'when did this file first reach this bank'."""
    lead = await _lead(admin_client)
    first = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares",
        json={"bank_name": BANK, "shared_at": "2026-01-01T10:00:00Z"})
    again = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares",
        json={"bank_name": BANK, "shared_at": "2026-06-06T10:00:00Z"})
    assert again.json()["shared_at"] == first.json()["shared_at"]


async def test_one_lead_many_banks(admin_client):
    lead = await _lead(admin_client)
    for b in (BANK, OTHER_BANK, "ICICI"):
        r = await admin_client.post(
            f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": b})
        assert r.status_code == 201
    shares = (await admin_client.get(f"/api/v1/leads/{lead['id']}/bank-shares")).json()
    assert {s["bank_name"] for s in shares} == {BANK, OTHER_BANK, "ICICI"}


async def test_bank_name_must_be_canonical(admin_client):
    lead = await _lead(admin_client)
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": "Definitely Not A Bank"})
    assert r.status_code == 400
    assert "canonical" in r.json()["detail"]


async def test_shared_by_from_another_company_rejected(admin_client):
    """Unvalidated it would fail at the FK as a 500."""
    lead = await _lead(admin_client)
    r = await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={
        "bank_name": BANK, "shared_by": str(uuid.uuid4()),
    })
    assert r.status_code == 400
    assert "not a user in this company" in r.json()["detail"]


async def test_share_does_not_touch_status_of_existing_entry(admin_client):
    """bank_status is the bank's decision — recording a share must not
    move a status someone already set."""
    lead = await _lead(admin_client)
    made = await admin_client.post(f"/api/v1/leads/{lead['id']}/banks", json={
        "bank_name": BANK, "bank_status": "sanctioned",
    })
    assert made.status_code == 201

    shared = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    assert shared.status_code == 200
    assert shared.json()["bank_status"] == "sanctioned"


async def test_share_fills_missing_provenance_on_ui_created_row(admin_client):
    """A row added through the UI has no provenance; the first share
    should fill it in without disturbing anything else."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/banks", json={
        "bank_name": BANK, "bank_status": "applied"})
    r = await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={
        "bank_name": BANK, "wa_group_id": "grp-x"})
    assert r.status_code == 200
    assert r.json()["wa_group_id"] == "grp-x"


# ── Messages ───────────────────────────────────────────────────────────

async def test_append_message(admin_client):
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json={
            "body": "Docs shared, awaiting login",
            "sender_phone": "+919812345678", "sender_name": "Ankit",
            "is_our_team": True, "wa_message_id": f"wamid.{uuid.uuid4().hex}",
        })
    assert r.status_code == 201, r.text
    assert r.json()["is_our_team"] is True


async def test_message_idempotent_on_wa_message_id(admin_client):
    """WhatsApp redelivery must be a no-op, not a duplicated thread line."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    wamid = f"wamid.{uuid.uuid4().hex}"
    body = {"body": "Same message", "wa_message_id": wamid}

    first = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json=body)
    again = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json=body)
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]

    detail = (await admin_client.get(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}")).json()
    assert len(detail["messages"]) == 1


async def test_messages_without_wa_id_are_not_deduped(admin_client):
    """The unique index is partial — hand-added messages have no id and
    must not collide with each other on NULL."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    for _ in range(2):
        r = await admin_client.post(
            f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages",
            json={"body": "manual note"})
        assert r.status_code == 201
    detail = (await admin_client.get(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}")).json()
    assert len(detail["messages"]) == 2


async def test_message_before_share_404s(admin_client):
    lead = await _lead(admin_client)
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json={"body": "hi"})
    assert r.status_code == 404


async def test_conversations_stay_separate_per_bank(admin_client):
    """The whole point: the PNB thread is not the SBI thread."""
    lead = await _lead(admin_client)
    for b in (BANK, OTHER_BANK):
        await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": b})
    await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json={"body": "pnb only"})

    pnb = (await admin_client.get(f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}")).json()
    sbi = (await admin_client.get(f"/api/v1/leads/{lead['id']}/bank-shares/{OTHER_BANK}")).json()
    assert [m["body"] for m in pnb["messages"]] == ["pnb only"]
    assert sbi["messages"] == []


async def test_messages_do_not_leak_into_lead_remarks(admin_client):
    """Bank chatter is deliberately not the lead's general timeline."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json={"body": "bank chatter"})
    remarks = (await admin_client.get(f"/api/v1/leads/{lead['id']}/remarks")).json()
    assert all("bank chatter" != r["body"] for r in remarks)


# ── The grid ───────────────────────────────────────────────────────────

async def test_grid_returns_bank_columns_and_cells(admin_client):
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares/{BANK}/messages", json={"body": "latest word"})

    grid = (await admin_client.get(
        "/api/v1/leads/bank-share-grid", params={"q": lead["phone"]})).json()
    # Against FMC_BANKS, not a magic number — adding a lender to the
    # canonical list must not break this test.
    assert grid["banks"] == list(FMC_BANKS)
    assert grid["banks"][0] == "Axis"

    row = next(r for r in grid["items"] if r["lead_id"] == lead["id"])
    assert BANK in row["shares"]
    assert OTHER_BANK not in row["shares"]          # blank cell
    cell = row["shares"][BANK]
    assert cell["shared_at"] is not None
    assert cell["message_count"] == 1
    assert cell["last_message_preview"] == "latest word"


async def test_grid_row_carries_the_summary_columns(admin_client):
    lead = await _lead(admin_client)
    await admin_client.put(f"/api/v1/leads/{lead['id']}", json={"loan_amount": "25"})
    grid = (await admin_client.get(
        "/api/v1/leads/bank-share-grid", params={"q": lead["phone"]})).json()
    row = next(r for r in grid["items"] if r["lead_id"] == lead["id"])
    for key in ("full_name", "phone", "counsellor_name", "current_stage", "loan_amount"):
        assert key in row
    assert row["loan_amount"] == "25"


async def test_grid_bank_filter(admin_client):
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": BANK})
    hit = (await admin_client.get("/api/v1/leads/bank-share-grid",
                                  params={"bank_name": BANK, "q": lead["phone"]})).json()
    assert any(r["lead_id"] == lead["id"] for r in hit["items"])
    miss = (await admin_client.get("/api/v1/leads/bank-share-grid",
                                   params={"bank_name": OTHER_BANK, "q": lead["phone"]})).json()
    assert not any(r["lead_id"] == lead["id"] for r in miss["items"])


async def test_grid_shared_only_excludes_untouched_leads(admin_client):
    lead = await _lead(admin_client)
    out = (await admin_client.get("/api/v1/leads/bank-share-grid",
                                  params={"shared_only": True, "q": lead["phone"]})).json()
    assert not any(r["lead_id"] == lead["id"] for r in out["items"])


async def test_grid_path_is_not_swallowed_by_the_lead_id_route(admin_client):
    """Registered after /leads/{lead_id} it would 422 as a bad UUID."""
    r = await admin_client.get("/api/v1/leads/bank-share-grid")
    assert r.status_code == 200


async def test_grid_is_paginated(admin_client):
    r = await admin_client.get("/api/v1/leads/bank-share-grid", params={"page_size": 1})
    b = r.json()
    assert b["page_size"] == 1 and len(b["items"]) <= 1
    assert "total_pages" in b


# ── The bot's credential ───────────────────────────────────────────────

async def test_no_delete_route_exists_for_shares_or_messages():
    from app.main import app
    for r in app.routes:
        path = getattr(r, "path", "")
        if "bank-share" in path:
            assert "DELETE" not in (getattr(r, "methods", None) or set())


# ── FundMyCampus only ──────────────────────────────────────────────────
#
# One codebase serves both brands from two deployments, so "this is an FMC
# feature" has to be enforced in the code, not assumed. Admitverse's
# equivalent is university applications.

async def test_every_bank_share_method_is_brand_gated(db_session, admin_user, monkeypatch):
    """All five — reads included. The grid's columns are FMC_BANKS, so an
    Admitverse user hitting it would get a board of Indian lender columns
    that mean nothing for study abroad."""
    from app.services.lead_service import LeadService
    from app.core.exceptions import BadRequestError

    svc = LeadService(db_session, admin_user.company_id)

    async def _av(self=None):
        return "admitverse"
    monkeypatch.setattr(LeadService, "_get_slug", _av)

    lead_id = uuid.uuid4()
    calls = [
        ("record_bank_share", svc.record_bank_share(lead_id, {"bank_name": BANK}, admin_user)),
        ("add_bank_message", svc.add_bank_message(lead_id, BANK, {"body": "x"}, admin_user)),
        ("list_bank_shares", svc.list_bank_shares(lead_id, admin_user)),
        ("get_bank_share_detail", svc.get_bank_share_detail(lead_id, BANK, admin_user)),
        ("bank_share_grid", svc.bank_share_grid(admin_user)),
    ]
    for name, coro in calls:
        with pytest.raises(BadRequestError) as exc:
            await coro
        assert "not available for this tenant" in str(exc.value.detail), name


async def test_grid_serves_fmc_columns_on_fmc(admin_client):
    """Guard the other direction — the gate must not fire on FMC."""
    grid = (await admin_client.get("/api/v1/leads/bank-share-grid",
                                   params={"page_size": 1})).json()
    assert grid["banks"] == list(FMC_BANKS)


async def test_poonawalla_is_accepted(admin_client):
    """Added 2026-08-07 for the "Poonawalla 🤝 Admit Verse" group. TWO Ls
    — the entity is Poonawalla Fincorp Limited."""
    lead = await _lead(admin_client)
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": "Poonawalla"})
    assert r.status_code == 201, r.text
    assert r.json()["bank_name"] == "Poonawalla"


async def test_the_one_l_spelling_is_rejected(admin_client):
    """The team's old spreadsheet used 'Poonawala'. The locked list exists
    precisely so one entity can't accumulate two spellings."""
    lead = await _lead(admin_client)
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/bank-shares", json={"bank_name": "Poonawala"})
    assert r.status_code == 400
    assert "canonical" in r.json()["detail"]


async def test_banks_dropdown_exposes_poonawalla(admin_client):
    """GET /leads/banks is what the bot and the UI should read the list
    from, rather than hard-coding it."""
    banks = (await admin_client.get("/api/v1/leads/banks")).json()
    assert "Poonawalla" in banks
    assert banks == list(FMC_BANKS)
