"""APScheduler job definitions."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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

    return scheduler


async def run_weekly_job() -> None:
    """APScheduler entry point — delegates to WeeklyRunOrchestrator."""
    from app.runner.weekly_run import WeeklyRunOrchestrator

    orchestrator = WeeklyRunOrchestrator()
    run_id = await orchestrator.run(mode=settings.default_mode)
    logger.info("Weekly snow run completed: run_id=%s", run_id)
