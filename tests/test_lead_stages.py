# ── VALID FORWARD TRANSITIONS ──────────────────────────────────────────

async def test_transition_lead_to_called(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "called",
        "conversation_notes": "Discussed options",
        "agent_agenda": "Follow up in 3 days",
    })
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "called"


async def test_transition_called_to_connected(agent_client, called_lead):
    resp = await agent_client.post(f"/api/v1/leads/{called_lead.id}/stage", json={
        "to_stage": "connected",
        "conversation_notes": "Student is interested",
        "agent_agenda": "Send brochure",
    })
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "connected"


async def test_transition_connected_to_qualified(agent_client, connected_lead):
    resp = await agent_client.post(f"/api/v1/leads/{connected_lead.id}/stage", json={
        "to_stage": "qualified_lead",
        "conversation_notes": "Documents verified",
        "agent_agenda": "Start application",
    })
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "qualified_lead"


async def test_transition_qualified_to_won(agent_client, qualified_lead):
    resp = await agent_client.post(f"/api/v1/leads/{qualified_lead.id}/stage", json={
        "to_stage": "won",
    })
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "won"


# ── LOST TRANSITIONS ──────────────────────────────────────────────────

async def test_transition_lead_to_lost(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "lost",
        "lost_reason": "Not interested",
    })
    assert resp.status_code == 200
    assert resp.json()["current_stage"] == "lost"
    assert resp.json()["lost_reason"] == "Not interested"


async def test_transition_to_lost_requires_lost_reason(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "lost",
    })
    assert resp.status_code == 400


# ── ADMIN REOPEN ───────────────────────────────────────────────────────

async def test_reopen_lost_to_lead_admin(admin_client, lost_lead):
    resp = await admin_client.post(f"/api/v1/leads/{lost_lead.id}/stage", json={
        "to_stage": "lead",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_stage"] == "lead"
    assert data["lost_time"] is None
    assert data["lost_reason"] is None


async def test_reopen_lost_to_lead_agent_forbidden(agent_client, lost_lead):
    resp = await agent_client.post(f"/api/v1/leads/{lost_lead.id}/stage", json={
        "to_stage": "lead",
    })
    assert resp.status_code == 403


# ── INVALID TRANSITIONS ───────────────────────────────────────────────

async def test_invalid_transition_lead_to_connected(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "connected",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 400


async def test_invalid_transition_lead_to_won(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "won",
    })
    assert resp.status_code == 400


async def test_invalid_transition_won_to_anything(agent_client, won_lead):
    resp = await agent_client.post(f"/api/v1/leads/{won_lead.id}/stage", json={
        "to_stage": "lead",
    })
    assert resp.status_code == 400


# ── NOTES REQUIRED ─────────────────────────────────────────────────────

async def test_transition_to_called_requires_notes(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/stage", json={
        "to_stage": "called",
    })
    assert resp.status_code == 400


async def test_transition_to_connected_requires_notes(agent_client, called_lead):
    resp = await agent_client.post(f"/api/v1/leads/{called_lead.id}/stage", json={
        "to_stage": "connected",
    })
    assert resp.status_code == 400


async def test_transition_to_qualified_requires_notes(agent_client, connected_lead):
    resp = await agent_client.post(f"/api/v1/leads/{connected_lead.id}/stage", json={
        "to_stage": "qualified_lead",
    })
    assert resp.status_code == 400


# ── TIMESTAMPS ─────────────────────────────────────────────────────────

async def test_connected_sets_connected_time(agent_client, called_lead):
    resp = await agent_client.post(f"/api/v1/leads/{called_lead.id}/stage", json={
        "to_stage": "connected",
        "conversation_notes": "Connected",
        "agent_agenda": "Next steps",
    })
    assert resp.status_code == 200
    assert resp.json()["connected_time"] is not None


async def test_won_sets_won_time(agent_client, qualified_lead):
    resp = await agent_client.post(f"/api/v1/leads/{qualified_lead.id}/stage", json={
        "to_stage": "won",
    })
    assert resp.status_code == 200
    assert resp.json()["won_time"] is not None


async def test_lost_sets_lost_time(agent_client, called_lead):
    resp = await agent_client.post(f"/api/v1/leads/{called_lead.id}/stage", json={
        "to_stage": "lost",
        "lost_reason": "Budget issue",
    })
    assert resp.status_code == 200
    assert resp.json()["lost_time"] is not None


# ── STAGE HISTORY ──────────────────────────────────────────────────────

async def test_get_stage_history(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/stage-history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1
