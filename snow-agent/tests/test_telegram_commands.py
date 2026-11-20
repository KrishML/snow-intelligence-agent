from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.models.user_prefs import UserPrefs
from app.delivery.telegram_commands import TelegramCommands, format_plan, format_report
from app.prefs import api as prefs_api
from app.prefs.service import PrefsService
from app.prefs.telegram_link import LinkCodeStore
from app.settings import settings
from tests.orm_sqlite import orm_sqlite

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
CHAT = "555"

WEEKLY = {
    "mode": "sports", "last_updated": "2026-11-20", "run": {"id": "r"}, "partial_failure": True,
    "latest_attempt": {"status": "failed"},
    "resorts": [
        {"rank": i, "name": f"R{i}", "country": "AT", "depth_cm": 80, "score": {"total": 90 - i}}
        for i in range(1, 8)
    ],
}


@pytest_asyncio.fixture
async def factory():
    async with orm_sqlite(UserPrefs) as f:
        yield f


@pytest.fixture
def redis():
    return FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def commands(factory, redis, monkeypatch):
    monkeypatch.setattr(settings, "telegram_chat_id", "")
    seen = {}

    async def load_report(prefs):
        seen["mode"] = prefs.mode
        return WEEKLY

    async def load_plan(user_id):
        return {"resort_name": "Kitzbuhel", "outbound_date": "2026-12-04", "return_date": "2026-12-06",
                "total_estimated_gbp": "420.00"}

    cmds = TelegramCommands(PrefsService(factory), LinkCodeStore(redis), load_report, load_plan, USER)
    cmds.seen = seen
    return cmds


async def link(commands, redis, chat=CHAT):
    code = await LinkCodeStore(redis).create(USER)
    return await commands.handle(chat, f"/start {code}")


async def test_unlinked_chat_cannot_use_commands(commands):
    for text in ("/mode", "/budget 100", "/report", "/plan", "/help"):
        assert "not linked" in await commands.handle(CHAT, text)


async def test_start_links_chat_with_single_use_code(commands, redis, factory):
    assert "linked" in await link(commands, redis)
    assert (await PrefsService(factory).get(USER)).telegram_chat_id == CHAT
    assert "/mode" in await commands.handle(CHAT, "/help")

    code = await LinkCodeStore(redis).create(USER)
    await commands.handle("777", f"/start {code}")
    assert "invalid or has expired" in await commands.handle("888", f"/start {code}")


@pytest.mark.parametrize("text", ["/start", "/start bogus-code"])
async def test_start_rejects_missing_or_unknown_codes(commands, text):
    reply = await commands.handle(CHAT, text)
    assert "not linked" not in reply and ("code" in reply)
    assert "linked. " not in reply


async def test_code_for_another_user_is_rejected(commands, redis):
    code = await LinkCodeStore(redis).create(OTHER)
    assert "invalid or has expired" in await commands.handle(CHAT, f"/start {code}")


async def test_mode_show_and_switch_affects_report(commands, redis):
    await link(commands, redis)
    assert await commands.handle(CHAT, "/mode") == "Current mode: sports."
    assert await commands.handle(CHAT, "/mode tourism") == "Mode set to tourism."
    await commands.handle(CHAT, "/report")
    assert commands.seen["mode"] == "tourism"
    assert "sports or tourism" in await commands.handle(CHAT, "/mode skiing")


async def test_budget_set_show_and_validation(commands, redis, factory):
    await link(commands, redis)
    assert "not set" in await commands.handle(CHAT, "/budget")
    assert await commands.handle(CHAT, "/budget £600") == "Budget set to £600."
    assert (await PrefsService(factory).get(USER)).budget_total_gbp == Decimal("600.00")
    assert "Budget: £600" in await commands.handle(CHAT, "/budget")
    for bad, expected in (
        ("abc", "Send a number"),
        ("nan", "between"),
        ("-5", "between"),
        ("99999999", "between"),
    ):
        assert expected in await commands.handle(CHAT, f"/budget {bad}")


async def test_report_and_plan_formats(commands, redis):
    await link(commands, redis)
    report = await commands.handle(CHAT, "/report")
    assert report.count("\n") == 5 + 2  # header + 5 resorts + two warnings
    assert "R5" in report and "R6" not in report
    assert "Some data sources failed" in report and "latest run failed" in report
    plan = await commands.handle(CHAT, "/plan")
    assert "Kitzbuhel" in plan and "Nothing has been booked" in plan


def test_format_helpers_handle_empty_states():
    assert "No completed weekly run" in format_report({"run": None})
    assert "No resorts passed" in format_report({**WEEKLY, "resorts": [], "partial_failure": False, "latest_attempt": None})
    assert "No draft plan" in format_plan(None)


async def test_booking_commands_are_not_handled_here(commands, redis):
    await link(commands, redis)
    for text in ("/book abc", "/confirm abc"):
        assert "separate booking bot" in await commands.handle(CHAT, text)
    assert "Unknown command" in await commands.handle(CHAT, "/dance")
    assert "/help" in await commands.handle(CHAT, "hello")


async def test_link_code_endpoint_requires_auth_and_issues_usable_code(redis, monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    monkeypatch.setattr(prefs_api, "_link_store", lambda: LinkCodeStore(redis))
    app = FastAPI()
    app.include_router(prefs_api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        assert (await http.post("/api/user/telegram-link")).status_code == 401
        response = await http.post("/api/user/telegram-link", headers={"Authorization": "Bearer test-token"})

    body = response.json()
    assert response.status_code == 200 and body["expires_in_seconds"] == 600
    assert await LinkCodeStore(redis).consume(body["code"]) == USER
    assert await LinkCodeStore(redis).consume(body["code"]) is None
