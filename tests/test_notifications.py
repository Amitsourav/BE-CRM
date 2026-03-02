import uuid


async def test_list_notifications(agent_client, sample_notification):
    resp = await agent_client.get("/api/v1/notifications")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


async def test_list_notifications_pagination(agent_client, sample_notification):
    resp = await agent_client.get("/api/v1/notifications", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


async def test_unread_count(agent_client, sample_notification):
    resp = await agent_client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


async def test_mark_read(agent_client, sample_notification):
    resp = await agent_client.put(f"/api/v1/notifications/{sample_notification.id}/read")
    assert resp.status_code == 200


async def test_mark_read_not_found(agent_client):
    resp = await agent_client.put(f"/api/v1/notifications/{uuid.uuid4()}/read")
    assert resp.status_code == 404


async def test_mark_all_read(agent_client, sample_notification):
    resp = await agent_client.put("/api/v1/notifications/read-all")
    assert resp.status_code == 200
    assert "message" in resp.json()


async def test_unread_count_after_mark_all(agent_client, sample_notification):
    await agent_client.put("/api/v1/notifications/read-all")
    resp = await agent_client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
