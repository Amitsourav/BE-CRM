"""AI board vs counsellor board.

Campaign leads and hand-worked leads now sit on separate boards.
Membership used to be DERIVED from having a campaign_leads row, so it
could never be undone; `leads.pipeline` makes the handover possible while
the call history stays.

The rule these tests exist to protect: **a lead must never end up on a
board that has no column for its stage.** That is what stranded 1,575
leads at the legacy stage 'lead' — invisible in the pipeline and
impossible to move from the UI.
"""
import uuid

import pytest

from app.core.constants import AI_PIPELINE_STAGE_VALUES, FMC_STAGES


def _phone():
    return f"+91{uuid.uuid4().int % 10**10:010d}"


async def _lead(client, **kw):
    r = await client.post("/api/v1/leads", json={
        "full_name": "Pipeline Test", "phone": _phone(), **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ── Defaults ───────────────────────────────────────────────────────────

async def test_new_lead_starts_on_the_normal_board(admin_client):
    lead = await _lead(admin_client)
    assert lead["pipeline"] == "normal"


async def test_ai_board_stage_set_is_the_short_one():
    assert AI_PIPELINE_STAGE_VALUES == ("created", "contacted", "dnp", "qualified", "lost")
    # Loan processing is human work and must not appear on the AI board.
    for human_only in ("processing", "logged_in", "sanctioned", "pf_paid", "disbursed"):
        assert human_only not in AI_PIPELINE_STAGE_VALUES
        assert human_only in [s.value for s in FMC_STAGES]


# ── Handover ───────────────────────────────────────────────────────────

async def test_move_to_normal_and_back(admin_client):
    lead = await _lead(admin_client)
    to_ai = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/pipeline", json={"pipeline": "ai"})
    assert to_ai.status_code == 200, to_ai.text
    assert to_ai.json()["pipeline"] == "ai"

    back = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/pipeline",
        json={"pipeline": "normal", "reason": "counsellor taking over"})
    assert back.status_code == 200
    assert back.json()["pipeline"] == "normal"


async def test_move_is_idempotent(admin_client):
    lead = await _lead(admin_client)
    for _ in range(2):
        r = await admin_client.post(
            f"/api/v1/leads/{lead['id']}/pipeline", json={"pipeline": "ai"})
        assert r.status_code == 200
        assert r.json()["pipeline"] == "ai"


async def test_move_is_logged_as_a_remark(admin_client):
    """A handover is an ownership change — it needs an audit trail."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/pipeline",
                            json={"pipeline": "ai"})
    await admin_client.post(f"/api/v1/leads/{lead['id']}/pipeline",
                            json={"pipeline": "normal", "reason": "worth a call"})
    remarks = (await admin_client.get(f"/api/v1/leads/{lead['id']}/remarks")).json()
    assert any("pipeline" in r["body"] for r in remarks)
    assert any("worth a call" in r["body"] for r in remarks)


async def test_invalid_pipeline_value_rejected(admin_client):
    lead = await _lead(admin_client)
    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/pipeline", json={"pipeline": "banana"})
    assert r.status_code == 422


# ── The invisibility trap ──────────────────────────────────────────────

async def test_cannot_move_to_ai_board_from_a_stage_it_cannot_render(admin_client, db_session):
    """Moving a lead at 'processing' onto the AI board would make it
    vanish — that board has no such column."""
    lead = await _lead(admin_client)
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE leads SET current_stage='processing' WHERE id=:i"),
        {"i": lead["id"]})
    await db_session.flush()

    r = await admin_client.post(
        f"/api/v1/leads/{lead['id']}/pipeline", json={"pipeline": "ai"})
    assert r.status_code == 400
    assert "does not show" in r.json()["detail"]


async def test_advancing_past_the_ai_board_auto_promotes(admin_client):
    """A counsellor moving an AI lead into loan processing IS taking it
    over — it must not be left on a board with no column for it."""
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/pipeline",
                            json={"pipeline": "ai"})

    moved = await admin_client.put(f"/api/v1/leads/{lead['id']}", json={
        "current_stage": "processing",
        "due_date": "2026-12-01T10:00:00Z",
    })
    assert moved.status_code == 200, moved.text
    assert moved.json()["current_stage"] == "processing"
    assert moved.json()["pipeline"] == "normal", "should have been promoted off the AI board"


# ── The boards ─────────────────────────────────────────────────────────

async def test_ai_board_returns_only_its_stages(admin_client):
    r = await admin_client.get("/api/v1/leads/by-stage",
                               params={"pipeline": "ai", "per_stage_limit": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pipeline"] == "ai"
    assert body["stages"] == list(AI_PIPELINE_STAGE_VALUES)


async def test_normal_board_returns_the_full_funnel(admin_client):
    body = (await admin_client.get("/api/v1/leads/by-stage",
                                   params={"pipeline": "normal", "per_stage_limit": 1})).json()
    assert body["stages"] == [s.value for s in FMC_STAGES]
    assert "disbursed" in body["stages"]


async def test_boards_do_not_overlap(admin_client):
    lead = await _lead(admin_client)
    await admin_client.post(f"/api/v1/leads/{lead['id']}/pipeline", json={"pipeline": "ai"})

    ai = (await admin_client.get("/api/v1/leads/by-stage",
                                 params={"pipeline": "ai", "per_stage_limit": 200})).json()
    normal = (await admin_client.get("/api/v1/leads/by-stage",
                                     params={"pipeline": "normal", "per_stage_limit": 200})).json()
    in_ai = {c["id"] for col in ai["items_by_stage"].values() for c in col}
    in_normal = {c["id"] for col in normal["items_by_stage"].values() for c in col}
    assert lead["id"] in in_ai
    assert lead["id"] not in in_normal


async def test_column_counts_match_the_filtered_board(admin_client):
    """Counts and cards must use the same filter, or a header reads
    'Qualified 23' above 4 cards."""
    body = (await admin_client.get("/api/v1/leads/by-stage",
                                   params={"pipeline": "ai", "per_stage_limit": 200})).json()
    for stage, cards in body["items_by_stage"].items():
        if len(cards) < 200:
            assert body["counts_by_stage"].get(stage, 0) == len(cards), stage


async def test_omitting_pipeline_returns_both(admin_client):
    """Pre-Aug-2026 callers must keep working."""
    body = (await admin_client.get("/api/v1/leads/by-stage",
                                   params={"per_stage_limit": 1})).json()
    assert body["pipeline"] is None


# ── Campaign provenance ────────────────────────────────────────────────

async def test_lead_detail_shows_which_campaign(admin_client):
    """'Why is this lead in front of me?' — the lead page needs to say."""
    lead = await _lead(admin_client)
    detail = (await admin_client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert "campaigns" in detail
    assert detail["campaigns"] == []   # never enrolled
