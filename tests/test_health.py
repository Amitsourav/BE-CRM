async def test_health_returns_200(unauth_client):
    resp = await unauth_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "env" in data


async def test_health_no_auth_required(unauth_client):
    resp = await unauth_client.get("/health")
    assert resp.status_code == 200
