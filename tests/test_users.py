import uuid


# ── ME ─────────────────────────────────────────────────────────────────

async def test_get_me(admin_client):
    resp = await admin_client.get("/api/v1/users/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "admin"
    assert "email" in data


async def test_get_me_agent(agent_client):
    resp = await agent_client.get("/api/v1/users/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "agent"


async def test_update_me(agent_client):
    resp = await agent_client.put("/api/v1/users/me", json={"full_name": "Updated Agent"})
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Updated Agent"


# ── ADMIN USER MANAGEMENT ─────────────────────────────────────────────

async def test_list_users_admin_only(admin_client, agent_user):
    resp = await admin_client.get("/api/v1/users")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


async def test_list_users_agent_forbidden(agent_client):
    resp = await agent_client.get("/api/v1/users")
    assert resp.status_code == 403


async def test_list_users_filter_by_role(admin_client, agent_user):
    resp = await admin_client.get("/api/v1/users", params={"role": "agent"})
    assert resp.status_code == 200
    for user in resp.json():
        assert user["role"] == "agent"


async def test_list_users_filter_by_active(admin_client, agent_user):
    resp = await admin_client.get("/api/v1/users", params={"is_active": True})
    assert resp.status_code == 200
    for user in resp.json():
        assert user["is_active"] is True


async def test_get_user_by_id(admin_client, agent_user):
    resp = await admin_client.get(f"/api/v1/users/{agent_user.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(agent_user.id)


async def test_get_user_not_found(admin_client):
    resp = await admin_client.get(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_update_user_admin_only(admin_client, agent_user):
    resp = await admin_client.put(f"/api/v1/users/{agent_user.id}", json={
        "full_name": "Admin Updated Agent",
    })
    assert resp.status_code == 200
    assert resp.json()["full_name"] == "Admin Updated Agent"


async def test_update_user_agent_forbidden(agent_client, admin_user):
    resp = await agent_client.put(f"/api/v1/users/{admin_user.id}", json={
        "full_name": "Hacked",
    })
    assert resp.status_code == 403


async def test_deactivate_user(admin_client, agent_user):
    resp = await admin_client.delete(f"/api/v1/users/{agent_user.id}")
    assert resp.status_code == 200


async def test_deactivate_user_not_found(admin_client):
    resp = await admin_client.delete(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── STATS ──────────────────────────────────────────────────────────────

async def test_get_user_stats(admin_client, agent_user, sample_lead):
    resp = await admin_client.get(f"/api/v1/users/{agent_user.id}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_leads" in data
    assert "total_calls" in data
    assert "total_tasks" in data


async def test_get_user_stats_agent_forbidden(agent_client, admin_user):
    resp = await agent_client.get(f"/api/v1/users/{admin_user.id}/stats")
    assert resp.status_code == 403
