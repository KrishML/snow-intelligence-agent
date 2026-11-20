from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.tools import ToolException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.chat.tools import create_chat_tools
from app.db.models.user_prefs import UserPrefs
from app.prefs import api as prefs_api
from app.prefs.registry import get_city
from app.prefs.service import PreferenceError, PrefsService, default_preferences
from app.settings import settings

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-000000000002")
HEADERS = {"Authorization": "Bearer test-token"}


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(UserPrefs.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    monkeypatch.setattr(
        prefs_api, "PrefsService", lambda: PrefsService(session_factory=factory)
    )
    app = FastAPI()
    app.include_router(prefs_api.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_new_user_defaults_to_london(factory):
    prefs = await PrefsService(factory).get(USER)
    assert prefs.residence_city == "London"
    assert prefs.preferred_airports == ["LHR", "LGW", "STN", "LTN", "LCY"]
    assert prefs.origin_airport == "LHR"


async def test_get_does_not_create_a_record(factory):
    await PrefsService(factory).get(USER)
    async with factory() as session:
        assert (await session.execute(UserPrefs.__table__.select())).first() is None


async def test_changing_city_resets_airports(factory):
    service = PrefsService(factory)
    prefs = await service.update(USER, {"residence_city": "manchester"})
    assert prefs.residence_city == "Manchester"
    assert prefs.preferred_airports == ["MAN"]
    assert (await service.get(USER)).preferred_airports == ["MAN"]


async def test_same_city_keeps_chosen_airports(factory):
    service = PrefsService(factory)
    await service.update(USER, {"preferred_airports": ["LGW", "STN"]})
    prefs = await service.update(USER, {"residence_city": "London"})
    assert prefs.preferred_airports == ["LGW", "STN"]


async def test_airport_outside_city_is_rejected(factory):
    service = PrefsService(factory)
    with pytest.raises(PreferenceError):
        await service.update(USER, {"preferred_airports": ["MAN"]})
    with pytest.raises(PreferenceError):
        await service.update(USER, {"residence_city": "Atlantis"})
    with pytest.raises(PreferenceError):
        await service.update(
            USER, {"residence_city": "Manchester", "preferred_airports": ["LHR"]}
        )


async def test_preferences_are_isolated_per_user(factory):
    service = PrefsService(factory)
    await service.update(USER, {"mode": "tourism", "budget_total_gbp": Decimal("500")})
    other = await service.get(OTHER_USER)
    assert other.mode == settings.default_mode
    assert other.budget_total_gbp is None


async def test_api_requires_auth(client):
    assert (await client.get("/api/user/prefs")).status_code == 401
    assert (await client.patch("/api/user/prefs", json={})).status_code == 401


async def test_api_get_and_patch_round_trip(client):
    got = await client.get("/api/user/prefs", headers=HEADERS)
    assert got.status_code == 200
    assert got.json()["residence_city"] == "London"

    patched = await client.patch(
        "/api/user/prefs",
        headers=HEADERS,
        json={
            "mode": "tourism",
            "budget_total_gbp": "450.50",
            "excluded_airlines": ["fr"],
            "email": "me@example.com",
            "telegram_chat_id": "12345",
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["mode"] == "tourism"
    assert body["budget_total_gbp"] == "450.50"
    assert body["excluded_airlines"] == ["FR"]

    cleared = await client.patch(
        "/api/user/prefs", headers=HEADERS, json={"budget_total_gbp": None}
    )
    assert cleared.json()["budget_total_gbp"] is None
    assert cleared.json()["mode"] == "tourism"


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "skiing"},
        {"mode": None},
        {"budget_total_gbp": -1},
        {"email": "not-an-email"},
        {"telegram_chat_id": "abc"},
        {"preferred_airports": ["MAN"]},
        {"preferred_airports": []},
        {"residence_city": "Atlantis"},
        {"unknown_field": 1},
    ],
)
async def test_api_rejects_invalid_updates(client, payload):
    response = await client.patch("/api/user/prefs", headers=HEADERS, json=payload)
    assert response.status_code == 422


async def test_api_lists_cities(client):
    response = await client.get("/api/user/cities", headers=HEADERS)
    london = next(c for c in response.json() if c["city"] == "London")
    assert london["airports"] == ["LHR", "LGW", "STN", "LTN", "LCY"]


async def test_flight_tool_rejects_origin_outside_residence(monkeypatch):
    monkeypatch.setattr(settings, "amadeus_client_id", "id")
    monkeypatch.setattr(settings, "amadeus_client_secret", "secret")
    tools = create_chat_tools(
        "s", USER, {"live_calls": 0, "plan_created": False}, default_preferences(USER)
    )
    flights = next(t for t in tools if t.name == "search_flights_readonly")
    outbound = date.today() + timedelta(days=7)
    with pytest.raises(ToolException, match="London"):
        await flights.ainvoke(
            {
                "resort_id": str(uuid.uuid4()),
                "outbound_date": outbound.isoformat(),
                "return_date": (outbound + timedelta(days=2)).isoformat(),
                "origin_airport": "MAN",
            }
        )


def test_registry_lookup_is_case_insensitive():
    assert get_city(" LONDON ").name == "London"
    assert get_city("nowhere") is None
