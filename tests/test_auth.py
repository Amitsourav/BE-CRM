from unittest.mock import MagicMock, AsyncMock
from app.services.auth_service import get_auth_service
from app.core.exceptions import UnauthorizedError


def _mock_auth_service():
    mock = MagicMock()
    mock.login.return_value = {
        "access_token": "fake-access",
        "refresh_token": "fake-refresh",
        "token_type": "bearer",
        "expires_in": 3600,
        "user_id": "00000000-0000-4000-a000-000000000001",
    }
    mock.register.return_value = {"user_id": "new-uuid", "email": "new@test.com"}
    # create_profile_row is async — MagicMock returns a MagicMock for the
    # method call which can't be awaited. AsyncMock returns an awaitable.
    mock.create_profile_row = AsyncMock(return_value=None)
    mock.refresh_token.return_value = mock.login.return_value
    mock.reset_password.return_value = None
    mock.update_password.return_value = None
    return mock


def _apply_auth_mock(app, mock):
    app.dependency_overrides[get_auth_service] = lambda: mock


async def test_login_success(admin_client):
    mock = _mock_auth_service()
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pass"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


async def test_login_invalid_credentials(admin_client):
    mock = _mock_auth_service()
    mock.login.side_effect = UnauthorizedError("Invalid credentials")
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"})
    assert resp.status_code == 401


async def test_register_admin_only(admin_client):
    mock = _mock_auth_service()
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/register", json={
        "email": "new@test.com",
        "password": "Pass123!",
        "full_name": "New User",
    })
    assert resp.status_code == 200
    assert "message" in resp.json()


async def test_register_forbidden_for_agent(agent_client):
    resp = await agent_client.post("/api/v1/auth/register", json={
        "email": "new@test.com",
        "password": "Pass123!",
        "full_name": "New User",
    })
    assert resp.status_code == 403


async def test_refresh_token_success(admin_client):
    mock = _mock_auth_service()
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/refresh", json={"refresh_token": "old-token"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_refresh_token_invalid(admin_client):
    mock = _mock_auth_service()
    mock.refresh_token.side_effect = UnauthorizedError("Invalid refresh token")
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/refresh", json={"refresh_token": "bad"})
    assert resp.status_code == 401


async def test_logout_requires_auth(unauth_client):
    resp = await unauth_client.post("/api/v1/auth/logout")
    assert resp.status_code in (401, 403)


async def test_logout_success(admin_client):
    resp = await admin_client.post("/api/v1/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Logged out successfully"


async def test_reset_password_always_200(admin_client):
    mock = _mock_auth_service()
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.post("/api/v1/auth/reset-password", json={"email": "any@email.com"})
    assert resp.status_code == 200


async def test_update_password_success(admin_client):
    mock = _mock_auth_service()
    from app.main import app
    _apply_auth_mock(app, mock)

    resp = await admin_client.put("/api/v1/auth/update-password", json={
        "access_token": "some-token",
        "new_password": "NewPass123!",
    })
    assert resp.status_code == 200
