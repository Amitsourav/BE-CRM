import hashlib
import hmac
from unittest.mock import patch


async def test_meta_verify_webhook_success(unauth_client):
    with patch("app.api.v1.webhooks.get_settings") as mock_settings:
        mock_settings.return_value.meta_verify_token = "test-token"
        resp = await unauth_client.get("/api/v1/webhooks/meta", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-token",
            "hub.challenge": "challenge123",
        })
    assert resp.status_code == 200
    assert resp.text == "challenge123"


async def test_meta_verify_webhook_wrong_token(unauth_client):
    with patch("app.api.v1.webhooks.get_settings") as mock_settings:
        mock_settings.return_value.meta_verify_token = "correct-token"
        resp = await unauth_client.get("/api/v1/webhooks/meta", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge123",
        })
    assert resp.status_code == 403


async def test_meta_webhook_post_valid_signature(unauth_client):
    secret = "test-secret"
    payload = b'{"entry": []}'
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    with patch("app.api.v1.webhooks.get_settings") as mock_settings:
        mock_settings.return_value.meta_app_secret = secret
        resp = await unauth_client.post(
            "/api/v1/webhooks/meta",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
    assert resp.status_code == 200


async def test_meta_webhook_post_invalid_signature(unauth_client):
    with patch("app.api.v1.webhooks.get_settings") as mock_settings:
        mock_settings.return_value.meta_app_secret = "real-secret"
        resp = await unauth_client.post(
            "/api/v1/webhooks/meta",
            content=b'{"entry": []}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )
    assert resp.status_code == 403
