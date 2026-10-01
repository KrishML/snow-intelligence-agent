from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.scheduler.jobs import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start APScheduler on app startup; shut it down on teardown."""
    scheduler = create_scheduler()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="Snow Intelligence Agent",
    description="Phase 1 — Snow Intelligence Core",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe — confirms the service is running."""
    return {"status": "ok"}
