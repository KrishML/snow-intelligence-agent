from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.chat.api import router as chat_router
from app.chat.store import create_redis_client
from app.dashboard.api import router as dashboard_router
from app.db.session import engine
from app.delivery.telegram_commands import build_application as build_telegram_application
from app.delivery.telegram_runtime import build_commands
from app.isolation import assert_chat_environment
from app.logging_config import configure_logging
from app.plans.api import router as plans_router
from app.prefs.api import router as prefs_router
from app.readiness import database_check, run_checks
from app.scheduler.jobs import create_scheduler
from app.settings import settings


async def _redis_check() -> None:
    client = create_redis_client()
    try:
        await client.ping()
    finally:
        await client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start APScheduler on app startup; shut it down on teardown."""
    configure_logging()
    assert_chat_environment()
    scheduler = create_scheduler()
    scheduler.start()
    bot = None
    if settings.telegram_bot_token:
        bot = build_telegram_application(build_commands(), settings.telegram_bot_token)
        await bot.initialize()
        await bot.start()
        await bot.updater.start_polling()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        if bot is not None:
            await bot.updater.stop()
            await bot.stop()
            await bot.shutdown()


app = FastAPI(
    title="Snow Intelligence Agent",
    description="Snow intelligence, chat planning and dashboard API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(chat_router)
app.include_router(prefs_router)
app.include_router(dashboard_router)
app.include_router(plans_router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe — confirms the service is running."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready() -> JSONResponse:
    """Readiness probe — 503 until PostgreSQL and Redis both answer."""
    ok, details = await run_checks(
        {"database": database_check(engine), "redis": _redis_check}
    )
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": details},
        status_code=200 if ok else 503,
    )
