"""FastAPI application: middleware, health, and route wiring."""
import logging
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.backend.routes import router as api_router
from apps.backend.vapi import pms_router, router as vapi_router
from apps.backend.voice import router as voice_router
from packages.database.session import get_sessionmaker
from packages.shared.config import get_settings
from packages.shared.logging import log, new_correlation_id, setup_logging

setup_logging()
logger = logging.getLogger("http")

app = FastAPI(title="AI Voice Receptionist", version="1.0.0",
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.include_router(api_router)
app.include_router(voice_router)
app.include_router(vapi_router)
app.include_router(pms_router)

# Dashboard is a separate origin on Render (static site); auth is a Bearer
# token, not cookies, so a wildcard origin carries no CSRF risk here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ponytail: in-memory sliding-window rate limiter; move to nginx/redis when
# running more than one backend replica.
_hits: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def observability(request: Request, call_next):
    new_correlation_id()
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= get_settings().rate_limit_per_minute:
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    window.append(now)

    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    log(logger, "request", method=request.method, path=request.url.path,
        status=response.status_code, latency_ms=int((time.monotonic() - started) * 1000))
    return response


@app.get("/api/health/live", tags=["health"])
async def liveness():
    return {"status": "ok"}


@app.get("/api/health/ready", tags=["health"])
async def readiness():
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "degraded", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "up"}
