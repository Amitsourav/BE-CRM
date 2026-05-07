import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger(__name__)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        errors.append({
            "field": " → ".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
        })
    return JSONResponse(status_code=422, content={"detail": "Validation error", "errors": errors})


async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    # Temporarily surface error class + message in the 500 response
    # so production failures can be diagnosed without Railway log access.
    # Revert to bare {"detail": "Internal server error"} once the
    # specific 500 we're chasing is fixed.
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:500],
        },
    )
