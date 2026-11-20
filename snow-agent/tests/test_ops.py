from __future__ import annotations

import json
import logging
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as main_module
from app.booking.main import create_app as create_booking_app
from app.logging_config import JsonFormatter
from app.readiness import run_checks
from app.runner.weekly_run import WeeklyRunOrchestrator


def _record(**extra):
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "run %s done", ("abc",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_message_and_structured_extras():
    entry = json.loads(JsonFormatter().format(_record(run_id="r1", duration_seconds=12.5)))
    assert entry["message"] == "run abc done"
    assert entry["level"] == "INFO" and entry["logger"] == "app.test"
    assert entry["run_id"] == "r1" and entry["duration_seconds"] == 12.5
    assert "ts" in entry


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), __import__("sys").exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]


async def test_run_checks_reports_each_dependency():
    async def ok():
        return None

    async def broken():
        raise ConnectionError("no route")

    ready, details = await run_checks({"database": ok, "redis": broken})
    assert not ready
    assert details == {"database": "ok", "redis": "unavailable: ConnectionError"}
    assert (await run_checks({"database": ok}))[0] is True


@pytest.mark.parametrize("ok, status", [(True, 200), (False, 503)])
async def test_ready_endpoint_reflects_dependencies(monkeypatch, ok, status):
    async def fake_checks(checks):
        assert set(checks) == {"database", "redis"}
        return ok, {"database": "ok", "redis": "ok" if ok else "unavailable: ConnectionError"}

    monkeypatch.setattr(main_module, "run_checks", fake_checks)
    async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://t") as http:
        response = await http.get("/ready")
    assert response.status_code == status
    assert response.json()["status"] == ("ready" if ok else "not_ready")


async def test_booking_ready_and_health(monkeypatch):
    import app.booking.main as booking_main

    async def fake_checks(checks):
        assert set(checks) == {"database"}
        return False, {"database": "unavailable: OSError"}

    monkeypatch.setattr(booking_main, "run_checks", fake_checks)
    async with AsyncClient(transport=ASGITransport(app=create_booking_app()), base_url="http://b") as http:
        assert (await http.get("/ready")).status_code == 503
        assert (await http.get("/health")).json() == {"status": "ok"}


def test_slow_weekly_run_is_logged_as_a_warning(caplog):
    run_id = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="app.runner.weekly_run"):
        WeeklyRunOrchestrator._log_duration(run_id, "sports", time.monotonic() - 31 * 60, "complete")
        WeeklyRunOrchestrator._log_duration(run_id, "sports", time.monotonic() - 60, "complete")

    slow, fast = caplog.records
    assert slow.levelno == logging.WARNING and "over the 30 minute target" in slow.getMessage()
    assert fast.levelno == logging.INFO and fast.duration_seconds == pytest.approx(60, abs=2)
    assert slow.run_id == str(run_id) and slow.status == "complete"
