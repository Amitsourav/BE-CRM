import uuid
from datetime import datetime, timezone, timedelta


# ── CREATE ─────────────────────────────────────────────────────────────

async def test_create_task(agent_client, sample_lead):
    due = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    resp = await agent_client.post("/api/v1/tasks", json={
        "lead_id": str(sample_lead.id),
        "title": "Follow up call",
        "task_type": "call",
        "due_date": due,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Follow up call"
    assert data["status"] == "pending"


async def test_create_task_defaults_assigned_to_self(agent_client, sample_lead):
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    resp = await agent_client.post("/api/v1/tasks", json={
        "lead_id": str(sample_lead.id),
        "title": "Self-assigned task",
        "due_date": due,
    })
    assert resp.status_code == 201
    from tests.conftest import AGENT_USER_ID
    assert resp.json()["assigned_to"] == str(AGENT_USER_ID)


async def test_create_task_creates_notification(agent_client, sample_lead, db_session):
    due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    resp = await agent_client.post("/api/v1/tasks", json={
        "lead_id": str(sample_lead.id),
        "title": "Notification test task",
        "due_date": due,
    })
    assert resp.status_code == 201

    from sqlalchemy import select
    from app.models.notification import Notification
    result = await db_session.execute(
        select(Notification).where(Notification.type == "task_created")
    )
    notif = result.scalars().first()
    assert notif is not None


# ── GET ────────────────────────────────────────────────────────────────

async def test_get_task_by_id(agent_client, sample_task):
    resp = await agent_client.get(f"/api/v1/tasks/{sample_task.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(sample_task.id)


async def test_get_task_not_found(agent_client):
    resp = await agent_client.get(f"/api/v1/tasks/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── UPDATE ─────────────────────────────────────────────────────────────

async def test_update_task(agent_client, sample_task):
    resp = await agent_client.put(f"/api/v1/tasks/{sample_task.id}", json={
        "title": "Updated Title",
    })
    assert resp.status_code == 200
    assert resp.json()["title"] == "Updated Title"


# ── COMPLETE ───────────────────────────────────────────────────────────

async def test_complete_task(agent_client, sample_task):
    resp = await agent_client.post(f"/api/v1/tasks/{sample_task.id}/complete", json={
        "completion_notes": "Done successfully",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["completed_at"] is not None
    assert data["completion_notes"] == "Done successfully"


# ── LIST ───────────────────────────────────────────────────────────────

async def test_list_tasks_admin_sees_all(admin_client, sample_task):
    resp = await admin_client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


async def test_list_tasks_agent_sees_own(agent_client, sample_task):
    resp = await agent_client.get("/api/v1/tasks")
    assert resp.status_code == 200
    from tests.conftest import AGENT_USER_ID
    for item in resp.json()["items"]:
        assert item["assigned_to"] == str(AGENT_USER_ID)


async def test_list_tasks_filter_by_status(admin_client, sample_task):
    resp = await admin_client.get("/api/v1/tasks", params={"status": "pending"})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["status"] == "pending"


# ── TODAY / OVERDUE / COMPLETED TODAY ──────────────────────────────────

async def test_get_today_tasks(agent_client, db_session, agent_user, sample_lead):
    from app.models.task import Task
    from app.core.constants import TaskStatus

    task = Task(
        company_id=agent_user.company_id,
        lead_id=sample_lead.id,
        assigned_to=agent_user.id,
        created_by=agent_user.id,
        task_type="call",
        title="Today Task",
        status=TaskStatus.PENDING,
        due_date=datetime.now(timezone.utc).replace(hour=23, minute=0),
    )
    db_session.add(task)
    await db_session.flush()

    resp = await agent_client.get("/api/v1/tasks/today")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_overdue_tasks(agent_client, overdue_task):
    resp = await agent_client.get("/api/v1/tasks/overdue")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_get_completed_today(admin_client, db_session, admin_user):
    from app.models.task import Task
    from app.core.constants import TaskStatus

    task = Task(
        company_id=admin_user.company_id,
        assigned_to=admin_user.id,
        created_by=admin_user.id,
        task_type="other",
        title="Completed Today Task",
        status=TaskStatus.COMPLETED,
        due_date=datetime.now(timezone.utc) - timedelta(days=1),
        completed_at=datetime.now(timezone.utc),
    )
    db_session.add(task)
    await db_session.flush()

    resp = await admin_client.get("/api/v1/tasks/completed-today")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
