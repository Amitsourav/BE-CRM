import hashlib
import hmac
import time

from app.config import get_settings


def _secret() -> bytes:
    settings = get_settings()
    secret = (
        settings.voice_stream_secret
        or settings.secret_key
        or settings.supabase_jwt_secret
        or "dev-insecure-fallback"
    )
    return secret.encode("utf-8")


def generate_stream_token(call_id: str, ttl_seconds: int = 900) -> str:
    """HMAC-signed token bound to a call_id, valid for ttl_seconds (default 15min)."""
    exp = int(time.time()) + ttl_seconds
    msg = f"{call_id}:{exp}".encode("utf-8")
    digest = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{digest}"


def verify_stream_token(call_id: str, token: str) -> bool:
    """Constant-time validate token against call_id and check expiry."""
    if not token or "." not in token:
        return False
    try:
        exp_str, digest = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False

    if exp < int(time.time()):
        return False

    msg = f"{call_id}:{exp}".encode("utf-8")
    expected = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)
