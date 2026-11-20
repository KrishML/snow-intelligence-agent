"""APScheduler job definitions."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.prefs.service import PrefsService
from app.settings import settings

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    """Return a configured AsyncIOScheduler with the weekly snow run job."""
    scheduler = AsyncIOScheduler(timezone=settings.weekly_run_timezone)

    scheduler.add_job(
        func=run_weekly_job,
        trigger=CronTrigger(
            day_of_week=settings.weekly_run_day,
            hour=settings.weekly_run_hour,
            minute=settings.weekly_run_minute,
            timezone=settings.weekly_run_timezone,
        ),
        id="weekly_snow_run",
        name="Weekly Snow Intelligence Run",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        func=run_snapshot_retention_job,
        trigger=CronTrigger(hour=3, minute=0, timezone=settings.weekly_run_timezone),
        id="snapshot_retention",
        name="Webcam Snapshot Retention",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    return scheduler


async def run_snapshot_retention_job() -> None:
    """Daily cleanup of webcam snapshots past the retention window."""
    from app.webcam.retention import prune_snapshots

    removed = await prune_snapshots()
    logger.info("Snapshot retention removed %d rows", removed)


async def run_weekly_job() -> None:
    """APScheduler entry point — delegates to WeeklyRunOrchestrator."""
    from app.runner.weekly_run import WeeklyRunOrchestrator

    prefs = await PrefsService().get(settings.default_user_id)
    orchestrator = WeeklyRunOrchestrator()
    run_id = await orchestrator.run(mode=prefs.mode)
    logger.info("Weekly snow run completed: run_id=%s", run_id)
