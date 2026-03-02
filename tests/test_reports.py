# ── AUTH ────────────────────────────────────────────────────────────────

async def test_dashboard_agent_forbidden(agent_client):
    resp = await agent_client.get("/api/v1/reports/dashboard")
    assert resp.status_code == 403


async def test_pipeline_agent_forbidden(agent_client):
    resp = await agent_client.get("/api/v1/reports/pipeline")
    assert resp.status_code == 403


# ── DASHBOARD ──────────────────────────────────────────────────────────

async def test_dashboard_returns_all_fields(admin_client, sample_lead, sample_task):
    resp = await admin_client.get("/api/v1/reports/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_leads" in data
    assert "new_leads_today" in data
    assert "leads_by_stage" in data
    assert "total_agents" in data
    assert "active_agents" in data
    assert "tasks_pending" in data
    assert "tasks_overdue" in data
    assert "tasks_completed_today" in data
    assert "conversion_rate" in data


async def test_dashboard_counts(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/reports/dashboard")
    assert resp.status_code == 200
    assert resp.json()["total_leads"] >= 1


# ── PIPELINE ───────────────────────────────────────────────────────────

async def test_pipeline_returns_all_stages(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/reports/pipeline")
    assert resp.status_code == 200
    data = resp.json()
    assert "stages" in data
    stage_names = [s["stage"] for s in data["stages"]]
    assert "lead" in stage_names
    assert "won" in stage_names
    assert "lost" in stage_names


# ── AGENTS ─────────────────────────────────────────────────────────────

async def test_agents_summary(admin_client, agent_user, sample_lead):
    resp = await admin_client.get("/api/v1/reports/agents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_agent_detail(admin_client, agent_user, sample_lead):
    resp = await admin_client.get(f"/api/v1/reports/agents/{agent_user.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == str(agent_user.id)
    assert "total_leads" in data


async def test_agent_detail_not_found(admin_client):
    import uuid
    resp = await admin_client.get(f"/api/v1/reports/agents/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── SOURCES ────────────────────────────────────────────────────────────

async def test_sources_report(admin_client, sample_lead_source, sample_lead):
    resp = await admin_client.get("/api/v1/reports/sources")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── TASK COMPLIANCE ────────────────────────────────────────────────────

async def test_task_compliance(admin_client, sample_task):
    resp = await admin_client.get("/api/v1/reports/tasks/compliance")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_tasks" in data
    assert "compliance_rate" in data


# ── TRENDS ─────────────────────────────────────────────────────────────

async def test_trends_default_30_days(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/reports/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 30


async def test_trends_custom_days(admin_client, sample_lead):
    resp = await admin_client.get("/api/v1/reports/trends", params={"days": 7})
    assert resp.status_code == 200
    assert len(resp.json()) == 7
