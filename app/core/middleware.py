import time
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.api_key import API_KEY_HEADER

logger = logging.getLogger("perf")
audit_logger = logging.getLogger("audit")


class ApiKeyDeleteGuardMiddleware(BaseHTTPMiddleware):
    """Refuse every DELETE request that authenticates with an API key.

    Machine integrations create and update; they have no reason to
    destroy. Enforcing that here rather than per-route means the
    guarantee holds for routes that don't exist yet — a new DELETE
    endpoint is covered the moment it is added, with nothing to
    remember and nothing to wire up.

    Keyed on the *presence* of the header rather than on the resolved
    principal, so it runs before routing and before auth, and cannot be
    bypassed by a route that forgets a dependency. `get_current_user`
    gives the API key precedence when both credentials are sent, so a
    request carrying a key is a key request regardless of what else it
    carries — the two rules agree.

    Note this covers hard *and* soft deletes: `DELETE /leads/{id}` and
    `DELETE /users/{id}` are soft in this codebase, and both are blocked.
    Any future destructive operation exposed under a non-DELETE verb
    would need its own check.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "DELETE" and request.headers.get(API_KEY_HEADER):
            audit_logger.warning(
                "API_KEY_DELETE_BLOCKED path=%s client=%s",
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "API keys are not permitted to delete. This credential is "
                        "restricted to read and write operations."
                    )
                },
            )
        return await call_next(request)


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"

        logger.info(
            "%s %s %s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )

        if elapsed_ms > 1000:
            logger.warning(
                "SLOW %s %s took %.1fms",
                request.method,
                request.url.path,
                elapsed_ms,
            )

        return response
