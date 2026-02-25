from __future__ import annotations

import httpx
from jose import jwt, JWTError, jwk
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import get_settings, Settings
from app.core.exceptions import UnauthorizedError

bearer_scheme = HTTPBearer()

# Cache JWKS keys
_jwks_cache: dict | None = None


def _get_jwks(supabase_url: str) -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        resp = httpx.get(f"{supabase_url}/auth/v1/.well-known/jwks.json", timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> dict:
    token = credentials.credentials
    try:
        # Get unverified header to determine algorithm
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")

        if alg.startswith("ES") or alg.startswith("RS"):
            # Asymmetric — use JWKS public key
            jwks = _get_jwks(settings.supabase_url)
            kid = header.get("kid")
            key = None
            for k in jwks.get("keys", []):
                if k.get("kid") == kid:
                    key = k
                    break
            if not key:
                raise UnauthorizedError("Invalid token: key not found")

            payload = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience="authenticated",
            )
        else:
            # Symmetric HS256 — use JWT secret
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )

        if not payload.get("sub"):
            raise UnauthorizedError("Invalid token: missing subject")
        return payload
    except JWTError as e:
        raise UnauthorizedError(f"Invalid token: {e}")
