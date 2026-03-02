# ── BASIC CALL LOGGING ─────────────────────────────────────────────────

async def test_log_call_dnp(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "No answer",
        "agent_agenda": "Try again tomorrow",
    })
    assert resp.status_code == 201
    assert resp.json()["disposition"] == "dnp"
    assert resp.json()["attempt_number"] == 1


async def test_log_call_connected(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "connected",
        "conversation_notes": "Student interested",
        "agent_agenda": "Send brochure",
    })
    assert resp.status_code == 201
    assert resp.json()["disposition"] == "connected"


async def test_log_call_busy(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "busy",
        "conversation_notes": "Line busy",
        "agent_agenda": "Retry in 2 hours",
    })
    assert resp.status_code == 201
    assert resp.json()["disposition"] == "busy"


async def test_log_call_callback(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "callback",
        "conversation_notes": "Asked to call back",
        "agent_agenda": "Call at 5pm",
    })
    assert resp.status_code == 201
    assert resp.json()["disposition"] == "callback"


# ── STAGE TRANSITIONS FROM CALLS ──────────────────────────────────────

async def test_first_call_moves_lead_to_called(agent_client, sample_lead):
    await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "No answer",
        "agent_agenda": "Retry",
    })
    lead_resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}")
    assert lead_resp.json()["current_stage"] == "called"


async def test_connected_call_moves_to_connected(agent_client, sample_lead):
    await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "connected",
        "conversation_notes": "Spoke with student",
        "agent_agenda": "Follow up",
    })
    lead_resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}")
    assert lead_resp.json()["current_stage"] == "connected"
    assert lead_resp.json()["connected_time"] is not None


# ── DNP LOGIC ──────────────────────────────────────────────────────────

async def test_dnp_at_attempt_5_creates_warning(agent_client, dnp_4_lead, db_session):
    resp = await agent_client.post(f"/api/v1/leads/{dnp_4_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "No answer again",
        "agent_agenda": "Last try",
    })
    assert resp.status_code == 201
    assert resp.json()["attempt_number"] == 5

    # Check notification was created
    from sqlalchemy import select
    from app.models.notification import Notification
    result = await db_session.execute(
        select(Notification).where(
            Notification.lead_id == dnp_4_lead.id,
            Notification.type == "dnp_warning",
        )
    )
    notif = result.scalar_one_or_none()
    assert notif is not None
    assert "5 DNP" in notif.message


async def test_dnp_at_attempt_6_auto_lost(agent_client, dnp_5_lead):
    resp = await agent_client.post(f"/api/v1/leads/{dnp_5_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "Final attempt",
        "agent_agenda": "N/A",
    })
    assert resp.status_code == 201
    assert resp.json()["attempt_number"] == 6

    lead_resp = await agent_client.get(f"/api/v1/leads/{dnp_5_lead.id}")
    data = lead_resp.json()
    assert data["current_stage"] == "lost"
    assert "Auto-lost" in data["lost_reason"]
    assert data["due_date"] is None


# ── ATTEMPT COUNTING ──────────────────────────────────────────────────

async def test_call_increments_attempt_count(agent_client, sample_lead):
    resp = await agent_client.post(f"/api/v1/leads/{sample_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "No answer",
        "agent_agenda": "Retry",
    })
    assert resp.json()["attempt_number"] == 1

    lead_resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}")
    assert lead_resp.json()["call_attempt_count"] == 1


# ── AUTHORIZATION ──────────────────────────────────────────────────────

async def test_agent_cannot_call_other_agents_lead(
    agent_client, db_session, agent2_user
):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        full_name="Other Lead",
        phone="+919999999997",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent2_user.id,
        created_by=agent2_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.post(f"/api/v1/leads/{lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 403


# ── STAGE RESTRICTIONS ─────────────────────────────────────────────────

async def test_cannot_call_connected_lead(agent_client, connected_lead):
    resp = await agent_client.post(f"/api/v1/leads/{connected_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 400


async def test_cannot_call_won_lead(agent_client, won_lead):
    resp = await agent_client.post(f"/api/v1/leads/{won_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 400


async def test_cannot_call_lost_lead(agent_client, lost_lead):
    resp = await agent_client.post(f"/api/v1/leads/{lost_lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 400


# ── MAX ATTEMPTS ───────────────────────────────────────────────────────

async def test_cannot_exceed_max_attempts(agent_client, db_session, agent_user):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        full_name="Max Attempts Lead",
        phone="+919999999996",
        current_stage=LeadStage.CALLED,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=6,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.post(f"/api/v1/leads/{lead.id}/calls", json={
        "disposition": "dnp",
        "conversation_notes": "test",
        "agent_agenda": "test",
    })
    assert resp.status_code == 400
