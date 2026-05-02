import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import get_settings
from app.api.v1.router import api_router
from app.core.exception_handlers import validation_exception_handler, generic_exception_handler
from app.core.middleware import TimingMiddleware
from app.core.rate_limit import limiter
from app.workers.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

# ── Sentry error tracking ──
if settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
    except ImportError:
        pass  # sentry-sdk not installed, skip

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Rate limiter is defined in app.core.rate_limit (shared instance)


# Brand-aware logs + API title — same codebase serves both FundMyCampus
# and Admitverse from separate Railway deployments. The deployment's
# APP_NAME env var (default: "FundMyCampus CRM") drives this. Set
# APP_NAME="Admitverse CRM" on the Admitverse Railway service.
import os
APP_NAME = os.environ.get("APP_NAME", "FundMyCampus CRM")


def _run_alembic(args: list[str], timeout: int = 180) -> int:
    """Run an alembic CLI command, mirror its output to our logs, return exit code."""
    import subprocess
    cmd = ["alembic"] + args
    logger.info("AUTO_MIGRATE: running %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    for line in (result.stdout + result.stderr).splitlines():
        if line.strip():
            logger.info("AUTO_MIGRATE | %s", line)
    return result.returncode


async def _run_pending_migrations() -> None:
    """Bring the DB schema up to head on startup.

    Two paths:
    - Fresh DB (no `alembic_version` table): create all tables from
      current models via `Base.metadata.create_all`, then `alembic stamp head`
      so subsequent boots are no-ops. This sidesteps the empty baseline
      migration that assumes tables already exist.
    - Existing DB: just `alembic upgrade head` as normal.

    Set AUTO_MIGRATE=false on a Railway service to opt out (e.g., if a
    DBA is managing migrations manually for that environment).
    """
    if os.environ.get("AUTO_MIGRATE", "true").lower() in ("0", "false", "no"):
        logger.info("AUTO_MIGRATE disabled — skipping migration step")
        return
    try:
        from app.db.session import engine
        from app.db.bootstrap import is_fresh_db, bootstrap_schema

        fresh = await is_fresh_db(engine)
        if fresh:
            logger.info("AUTO_MIGRATE: fresh DB detected — bootstrapping schema from models")
            await bootstrap_schema(engine)
            stamp_rc = _run_alembic(["stamp", "head"])
            if stamp_rc == 0:
                logger.info("AUTO_MIGRATE: ✅ fresh DB bootstrapped & stamped at head")
            else:
                logger.error("AUTO_MIGRATE: ❌ stamp head failed (exit %d)", stamp_rc)
            return

        rc = _run_alembic(["upgrade", "head"])
        if rc == 0:
            logger.info("AUTO_MIGRATE: ✅ migrations applied (or already at head)")
        else:
            logger.error("AUTO_MIGRATE: ❌ exit code %d — see lines above", rc)
    except Exception as e:
        # Don't crash the app if migrations fail — ops can handle via
        # Railway shell. Just log loudly so they notice.
        logger.error("AUTO_MIGRATE: ❌ unexpected error: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s Backend (%s)", APP_NAME, settings.app_env)
    await _run_pending_migrations()
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutting down %s Backend", APP_NAME)


app = FastAPI(
    title=f"{APP_NAME} API",
    description=(
        f"{APP_NAME} Backend — Lead Management, Pipeline, AI Voice Calls, "
        "Tasks & Reports"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Attach limiter to app state (required by slowapi)
app.state.limiter = limiter

# Rate limit exceeded handler — returns 429 Too Many Requests
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Response-Time"],
)

# Performance timing
app.add_middleware(TimingMiddleware)

# Exception handlers
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Routes
app.include_router(api_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "env": settings.app_env}
