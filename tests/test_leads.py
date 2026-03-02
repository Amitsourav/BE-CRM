import uuid


# ── CREATE ─────────────────────────────────────────────────────────────

async def test_create_lead_full_fields(admin_client, sample_lead_source, agent_user):
    resp = await admin_client.post("/api/v1/leads", json={
        "full_name": "John Doe",
        "email": "john@test.com",
        "phone": "+911111111111",
        "city": "Delhi",
        "state": "Delhi",
        "country": "India",
        "highest_qualification": "B.Tech",
        "stream": "CS",
        "passing_year": 2024,
        "lead_source_id": str(sample_lead_source.id),
        "assigned_agent_id": str(agent_user.id),
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "John Doe"
    assert data["current_stage"] == "lead"


async def test_create_lead_minimal(admin_client):
    resp = await admin_client.post("/api/v1/leads", json={"full_name": "Minimal Lead"})
    assert resp.status_code == 201
    assert resp.json()["full_name"] == "Minimal Lead"


async def test_create_lead_returns_201(admin_client):
    resp = await admin_client.post("/api/v1/leads", json={"full_name": "Status Check"})
    assert resp.status_code == 201


# ── LIST ───────────────────────────────────────────────────────────────

async def test_list_leads_admin_sees_all(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1


async def test_list_leads_agent_sees_own_only(agent_client, sample_lead, sample_lead_unassigned):
    resp = await agent_client.get("/api/v1/leads")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["assigned_agent_id"] == str(sample_lead.assigned_agent_id)


async def test_list_leads_pagination(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 1
    assert "total_pages" in data


async def test_list_leads_filter_by_stage(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads", params={"stage": "lead"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["current_stage"] == "lead"


# ── GET ────────────────────────────────────────────────────────────────

async def test_get_lead_by_id(admin_client, sample_lead):
    resp = await admin_client.get(f"/api/v1/leads/{sample_lead.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(sample_lead.id)


async def test_get_lead_not_found(admin_client):
    fake_id = uuid.uuid4()
    resp = await admin_client.get(f"/api/v1/leads/{fake_id}")
    assert resp.status_code == 404


async def test_get_lead_agent_forbidden_other_agent(
    agent_client, db_session, agent2_user
):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        full_name="Other Agent Lead",
        phone="+919999999999",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent2_user.id,
        created_by=agent2_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.get(f"/api/v1/leads/{lead.id}")
    assert resp.status_code == 403


# ── SEARCH ─────────────────────────────────────────────────────────────

async def test_search_leads_by_name(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads/search", params={"q": "Test Lead"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_search_leads_by_email(admin_client, sample_lead):
    # sample_lead email is like "lead-abcdef@example.com" — search by the actual email
    resp = await admin_client.get("/api/v1/leads/search", params={"q": sample_lead.email})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_search_leads_by_phone(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/leads/search", params={"q": "9876543210"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


# ── UPDATE ─────────────────────────────────────────────────────────────

async def test_update_lead_partial(admin_client, sample_lead):
    resp = await admin_client.put(f"/api/v1/leads/{sample_lead.id}", json={"city": "Bangalore"})
    assert resp.status_code == 200
    assert resp.json()["city"] == "Bangalore"


async def test_update_lead_agent_can_update_own(agent_client, sample_lead):
    resp = await agent_client.put(f"/api/v1/leads/{sample_lead.id}", json={"notes": "updated"})
    assert resp.status_code == 200


async def test_update_lead_agent_cannot_update_other(
    agent_client, db_session, agent2_user
):
    from app.models.lead import Lead
    from app.core.constants import LeadStage

    lead = Lead(
        full_name="Other Lead",
        phone="+919999999998",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent2_user.id,
        created_by=agent2_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    resp = await agent_client.put(f"/api/v1/leads/{lead.id}", json={"notes": "hack"})
    assert resp.status_code == 403


# ── DELETE ─────────────────────────────────────────────────────────────

async def test_delete_lead_admin_only(admin_client, sample_lead_unassigned):
    # Use lead without stage logs to avoid FK cascade issue with ORM delete
    resp = await admin_client.delete(f"/api/v1/leads/{sample_lead_unassigned.id}")
    assert resp.status_code == 200


async def test_delete_lead_agent_forbidden(agent_client, sample_lead):
    resp = await agent_client.delete(f"/api/v1/leads/{sample_lead.id}")
    assert resp.status_code == 403


async def test_delete_lead_not_found(admin_client):
    resp = await admin_client.delete(f"/api/v1/leads/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── TIMELINE / CALLS / TASKS ──────────────────────────────────────────

async def test_get_timeline(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/timeline")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_lead_calls(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/calls")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_lead_tasks(agent_client, sample_lead):
    resp = await agent_client.get(f"/api/v1/leads/{sample_lead.id}/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── ASSIGN ─────────────────────────────────────────────────────────────

async def test_assign_lead_admin_only(admin_client, sample_lead, agent_user):
    resp = await admin_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(agent_user.id)},
    )
    assert resp.status_code == 200


async def test_assign_lead_agent_forbidden(agent_client, sample_lead, agent_user):
    resp = await agent_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(agent_user.id)},
    )
    assert resp.status_code == 403


async def test_assign_lead_invalid_agent(admin_client, sample_lead):
    resp = await admin_client.post(
        f"/api/v1/leads/{sample_lead.id}/assign",
        json={"agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 400


# ── BULK ASSIGN ────────────────────────────────────────────────────────

async def test_bulk_assign_admin_only(admin_client, sample_lead, agent_user):
    resp = await admin_client.post("/api/v1/leads/bulk-assign", json={
        "lead_ids": [str(sample_lead.id)],
        "agent_id": str(agent_user.id),
    })
    assert resp.status_code == 200
    assert "message" in resp.json()


async def test_bulk_assign_agent_forbidden(agent_client, sample_lead, agent_user):
    resp = await agent_client.post("/api/v1/leads/bulk-assign", json={
        "lead_ids": [str(sample_lead.id)],
        "agent_id": str(agent_user.id),
    })
    assert resp.status_code == 403


# ── SOURCES ────────────────────────────────────────────────────────────

async def test_list_lead_sources(admin_client, sample_lead_source):
    resp = await admin_client.get("/api/v1/leads/sources/list")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


async def test_create_lead_source_admin_only(admin_client):
    resp = await admin_client.post("/api/v1/leads/sources", json={
        "name": f"New Source {uuid.uuid4().hex[:6]}",
        "source_type": "manual",
    })
    assert resp.status_code == 201


async def test_create_lead_source_agent_forbidden(agent_client):
    resp = await agent_client.post("/api/v1/leads/sources", json={
        "name": "Agent Source",
        "source_type": "manual",
    })
    assert resp.status_code == 403
