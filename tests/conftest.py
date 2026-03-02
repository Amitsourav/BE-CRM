import uuid
from datetime import datetime, timezone, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine, get_db
from app.dependencies import get_current_user, get_current_admin
from app.core.constants import UserRole, LeadStage, TaskStatus, NotificationType
from app.models.profile import Profile
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.lead_stage_log import LeadStageLog
from app.models.task import Task
from app.models.notification import Notification

# Real user IDs from the Supabase database
ADMIN_USER_ID = uuid.UUID("3000eae7-48e6-4bf7-ae12-6ad8758d6a83")  # deepak@admitverse.com
AGENT_USER_ID = uuid.UUID("ca52fa93-e695-48d4-8803-d7715d53e6a3")  # ankit@fundmycampus.com
AGENT2_USER_ID = uuid.UUID("f95ecae7-76fb-4e9b-9d90-6b784f270a6b")  # amitsourav0407@gmail.com


# ── DB Session with Transaction Rollback ───────────────────────────────

@pytest.fixture
async def db_session():
    """AsyncSession inside a transaction that is ALWAYS rolled back."""
    async with engine.connect() as connection:
        trans = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    # Dispose pool so next test (new event loop) gets fresh connections
    await engine.dispose()


# ── User Fixtures (query real profiles from DB) ───────────────────────

@pytest.fixture
async def admin_user(db_session: AsyncSession) -> Profile:
    result = await db_session.execute(select(Profile).where(Profile.id == ADMIN_USER_ID))
    user = result.scalar_one()
    return user


@pytest.fixture
async def agent_user(db_session: AsyncSession) -> Profile:
    result = await db_session.execute(select(Profile).where(Profile.id == AGENT_USER_ID))
    user = result.scalar_one()
    return user


@pytest.fixture
async def agent2_user(db_session: AsyncSession) -> Profile:
    result = await db_session.execute(select(Profile).where(Profile.id == AGENT2_USER_ID))
    user = result.scalar_one()
    return user


# ── App + Client Fixtures ─────────────────────────────────────────────

def _create_app_with_overrides(db_session: AsyncSession, user: Profile):
    """Override get_db and get_current_user/get_current_admin to skip JWT auth."""
    from app.main import app

    async def override_get_db():
        yield db_session

    async def override_get_current_user():
        return user

    async def override_get_current_admin():
        if user.role != UserRole.ADMIN:
            from app.core.exceptions import ForbiddenError
            raise ForbiddenError("Admin access required")
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_admin] = override_get_current_admin
    return app


@pytest.fixture
async def admin_client(db_session: AsyncSession, admin_user: Profile):
    app = _create_app_with_overrides(db_session, admin_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def agent_client(db_session: AsyncSession, agent_user: Profile):
    app = _create_app_with_overrides(db_session, agent_user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def unauth_client():
    from app.main import app
    # Clear any overrides from previous tests
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    # Restore overrides
    app.dependency_overrides.update(saved)


# ── Data Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
async def sample_lead_source(db_session: AsyncSession) -> LeadSource:
    source = LeadSource(
        name=f"Test Source {uuid.uuid4().hex[:8]}",
        source_type="manual",
        is_active=True,
    )
    db_session.add(source)
    await db_session.flush()
    return source


@pytest.fixture
async def sample_lead(db_session: AsyncSession, agent_user: Profile, sample_lead_source: LeadSource) -> Lead:
    lead = Lead(
        full_name="Test Lead",
        email=f"lead-{uuid.uuid4().hex[:6]}@example.com",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        city="Mumbai",
        state="Maharashtra",
        country="India",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=agent_user.id,
        lead_source_id=sample_lead_source.id,
        created_by=agent_user.id,
    )
    db_session.add(lead)
    await db_session.flush()

    stage_log = LeadStageLog(
        lead_id=lead.id,
        from_stage=None,
        to_stage=LeadStage.LEAD,
        changed_by=agent_user.id,
    )
    db_session.add(stage_log)
    await db_session.flush()
    return lead


@pytest.fixture
async def sample_lead_unassigned(db_session: AsyncSession, admin_user: Profile) -> Lead:
    lead = Lead(
        full_name="Unassigned Lead",
        email=f"unassigned-{uuid.uuid4().hex[:6]}@example.com",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.LEAD,
        assigned_agent_id=None,
        created_by=admin_user.id,
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def called_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    lead = Lead(
        full_name="Called Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.CALLED,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=1,
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def connected_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    lead = Lead(
        full_name="Connected Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.CONNECTED,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=2,
        connected_time=datetime.now(timezone.utc),
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def qualified_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    lead = Lead(
        full_name="Qualified Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.QUALIFIED_LEAD,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=3,
        connected_time=datetime.now(timezone.utc),
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def lost_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    lead = Lead(
        full_name="Lost Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.LOST,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        lost_time=datetime.now(timezone.utc),
        lost_reason="Not interested",
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def won_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    lead = Lead(
        full_name="Won Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.WON,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        won_time=datetime.now(timezone.utc),
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def dnp_4_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    """Lead with 4 DNP attempts — next DNP triggers warning."""
    lead = Lead(
        full_name="DNP Warning Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.CALLED,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=4,
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def dnp_5_lead(db_session: AsyncSession, agent_user: Profile) -> Lead:
    """Lead with 5 DNP attempts — next DNP triggers auto-lost."""
    lead = Lead(
        full_name="DNP Auto-Lost Lead",
        phone=f"+91{uuid.uuid4().int % 10**10:010d}",
        current_stage=LeadStage.CALLED,
        assigned_agent_id=agent_user.id,
        created_by=agent_user.id,
        call_attempt_count=5,
    )
    db_session.add(lead)
    await db_session.flush()
    return lead


@pytest.fixture
async def sample_task(db_session: AsyncSession, agent_user: Profile, sample_lead: Lead) -> Task:
    task = Task(
        lead_id=sample_lead.id,
        assigned_to=agent_user.id,
        created_by=agent_user.id,
        task_type="follow_up",
        title="Follow up with Test Lead",
        description="Call and discuss admission options",
        status=TaskStatus.PENDING,
        due_date=datetime.now(timezone.utc) + timedelta(days=2),
    )
    db_session.add(task)
    await db_session.flush()
    return task


@pytest.fixture
async def overdue_task(db_session: AsyncSession, agent_user: Profile, sample_lead: Lead) -> Task:
    task = Task(
        lead_id=sample_lead.id,
        assigned_to=agent_user.id,
        created_by=agent_user.id,
        task_type="call",
        title="Overdue Task",
        status=TaskStatus.OVERDUE,
        due_date=datetime.now(timezone.utc) - timedelta(days=2),
    )
    db_session.add(task)
    await db_session.flush()
    return task


@pytest.fixture
async def sample_notification(db_session: AsyncSession, agent_user: Profile) -> Notification:
    notif = Notification(
        user_id=agent_user.id,
        type=NotificationType.GENERAL,
        title="Test Notification",
        message="This is a test notification",
        is_read=False,
    )
    db_session.add(notif)
    await db_session.flush()
    return notif
