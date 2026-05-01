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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting FundMyCampus CRM Backend (%s)", settings.app_env)
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutting down FundMyCampus CRM Backend")


app = FastAPI(
    title="FundMyCampus CRM API",
    description="FundMyCampus CRM Backend — Education Loan Lead Management, Pipeline, AI Voice Calls, Tasks & Reports",
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
