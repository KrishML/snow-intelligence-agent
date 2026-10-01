"""
Smoke tests for the APScheduler configuration.

Tests
-----
test_scheduler_has_exactly_one_job          — exactly one job registered with id='weekly_snow_run'
test_scheduler_job_trigger_is_cron_friday   — CronTrigger configured for Fri 17:00 UTC
test_scheduler_misfire_grace_time           — misfire_grace_time=3600
test_scheduler_job_name                     — job name is "Weekly Snow Intelligence Run"
test_health_endpoint                        — FastAPI /health returns {"status": "ok"}

Requirements: 7.1, 29.4
"""

from __future__ import annotations

import pytest
from apscheduler.triggers.cron import CronTrigger
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.scheduler.jobs import create_scheduler


# ---------------------------------------------------------------------------
# Scheduler smoke tests (synchronous — no async needed)
# ---------------------------------------------------------------------------


def test_scheduler_has_exactly_one_job() -> None:
    """
    Assert the scheduler has exactly one registered job with id='weekly_snow_run'.

    Requirements: 7.1, 29.4
    """
    scheduler = create_scheduler()
    jobs = scheduler.get_jobs()

    assert len(jobs) == 1
    assert jobs[0].id == "weekly_snow_run"


def test_scheduler_job_trigger_is_cron_friday() -> None:
    """
    Assert the job trigger is a CronTrigger configured for Friday at 17:00 UTC.

    Requirements: 7.1, 29.4
    """
    scheduler = create_scheduler()
    job = scheduler.get_jobs()[0]

    assert isinstance(job.trigger, CronTrigger)

    fields = {f.name: f for f in job.trigger.fields}
    assert str(fields["day_of_week"]) == "fri"
    assert str(fields["hour"]) == "17"
    assert str(fields["minute"]) == "0"


def test_scheduler_misfire_grace_time() -> None:
    """
    Assert the job has misfire_grace_time=3600 seconds.

    Requirements: 7.1, 29.4
    """
    scheduler = create_scheduler()
    job = scheduler.get_jobs()[0]

    assert job.misfire_grace_time == 3600


def test_scheduler_job_name() -> None:
    """
    Assert the job name is "Weekly Snow Intelligence Run".

    Requirements: 7.1, 29.4
    """
    scheduler = create_scheduler()
    job = scheduler.get_jobs()[0]

    assert job.name == "Weekly Snow Intelligence Run"


# ---------------------------------------------------------------------------
# FastAPI health endpoint smoke test
# ---------------------------------------------------------------------------


async def test_health_endpoint() -> None:
    """
    Assert the /health endpoint returns HTTP 200 with {"status": "ok"}.

    Uses httpx.AsyncClient with ASGITransport so no live server is needed.
    The FastAPI lifespan starts and stops APScheduler as part of the test.

    Requirements: 29.4
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
