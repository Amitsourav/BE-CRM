import hashlib
import hmac


def verify_meta_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    if not signature or not app_secret:
        return False
    expected = hmac.HMAC(
        app_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
