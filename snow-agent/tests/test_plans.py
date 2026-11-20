from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.tools import ToolException

from app.chat import tools as chat_tools
from app.chat.session import ChatSession
from app.clients.amadeus_hotels import search_hotels
from app.db.models.resort import Resort
from app.db.models.snow_score import SnowScore
from app.db.models.trip_plan import TripPlan
from app.db.models.user_prefs import UserPrefs
from app.db.models.weekly_run import WeeklyRun
from app.delivery.email_sender import SendGridDelivery
from app.delivery.telegram_bot import TelegramDelivery, format_plan_text
from app.plans import api as plans_api
from app.plans import service as plans_service
from app.plans.service import dispatch_plan, offer_total_gbp
from app.prefs.service import default_preferences
from app.settings import settings
from tests.factories import make_resort, make_run, make_score
from tests.orm_sqlite import orm_sqlite

USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER = uuid.UUID("00000000-0000-0000-0000-000000000002")
HEADERS = {"Authorization": "Bearer test-token"}
OUT = date.today() + timedelta(days=10)
BACK = OUT + timedelta(days=2)


@pytest_asyncio.fixture
async def factory():
    async with orm_sqlite(Resort, WeeklyRun, SnowScore, TripPlan, UserPrefs) as f:
        yield f


async def _seed(factory, *objects):
    async with factory() as session:
        session.add_all(objects)
        await session.commit()


# --- hotel client ----------------------------------------------------------------


class _Resp:
    def __init__(self, data):
        self.data = data


def _fake_amadeus(listing, offers):
    calls = {}

    def by_geocode(**kw):
        calls["geocode"] = kw
        return _Resp(listing)

    def hotel_offers(**kw):
        calls["offers"] = kw
        return _Resp(offers)

    client = SimpleNamespace(
        reference_data=SimpleNamespace(
            locations=SimpleNamespace(
                hotels=SimpleNamespace(by_geocode=SimpleNamespace(get=by_geocode))
            )
        ),
        shopping=SimpleNamespace(hotel_offers_search=SimpleNamespace(get=hotel_offers)),
    )
    return client, calls


def _hotel(hotel_id, name, *offers):
    return {
        "hotel": {"hotelId": hotel_id, "name": name},
        "offers": [
            {"id": f"{hotel_id}-{i}", "price": {"currency": cur, "total": total}}
            for i, (cur, total) in enumerate(offers)
        ],
    }


def test_hotel_search_keeps_only_gbp_cheapest_offer_and_sorts():
    listing = [{"hotelId": h, "address": {"countryCode": "AT"}} for h in "ABCD"]
    client, calls = _fake_amadeus(
        listing,
        [
            _hotel("A", "Alpha", ("GBP", "400.00"), ("GBP", "300.00")),
            _hotel("B", "Beta", ("EUR", "100.00")),
            _hotel("C", "Gamma", ("GBP", "250.50")),
        ],
    )
    results = search_hotels(client, 47.0, 12.0, OUT, BACK)

    assert [r["name"] for r in results] == ["Gamma", "Alpha"]
    assert results[1]["total_gbp"] == "300.00"
    assert results[1]["nightly_avg_gbp"] == "150.00"
    assert results[0]["nightly_avg_gbp"] == "125.25"
    assert all(r["currency"] == "GBP" and r["nights"] == 2 for r in results)
    assert results[0]["country_code"] == "AT"
    assert calls["offers"]["currency"] == "GBP"
    assert calls["offers"]["hotelIds"] == "A,B,C,D"


def test_hotel_search_budget_filter_and_empty_listing():
    listing = [{"hotelId": "A"}, {"hotelId": "B"}]
    client, _ = _fake_amadeus(
        listing, [_hotel("A", "Cheap", ("GBP", "200")), _hotel("B", "Dear", ("GBP", "600"))]
    )
    results = search_hotels(client, 0, 0, OUT, BACK, Decimal("150"))
    assert [r["name"] for r in results] == ["Cheap"]

    empty, _ = _fake_amadeus([], [])
    assert search_hotels(empty, 0, 0, OUT, BACK) == []
    with pytest.raises(ValueError):
        search_hotels(empty, 0, 0, OUT, OUT)


# --- offer helper ----------------------------------------------------------------


def test_offer_total_requires_explicit_gbp():
    assert offer_total_gbp({"price": {"currency": "GBP", "grandTotal": "99.90"}}) == Decimal("99.90")
    assert offer_total_gbp({"currency": "GBP", "total_gbp": "10"}) == Decimal("10")
    assert offer_total_gbp({"price": {"currency": "EUR", "grandTotal": "5"}}) is None
    assert offer_total_gbp({"currency": "GBP", "total_gbp": "-1"}) is None
    assert offer_total_gbp({"currency": "GBP", "total_gbp": "nan"}) is None
    assert offer_total_gbp(None) is None


# --- chat tools --------------------------------------------------------------------

FLIGHT = {
    "id": "f1",
    "price": {"currency": "GBP", "grandTotal": "120.00"},
    "currency": "GBP",
    "total_gbp": "120.00",
}
HOTEL = {
    "provider": "amadeus",
    "name": "Alpha",
    "checkin": OUT.isoformat(),
    "checkout": BACK.isoformat(),
    "currency": "GBP",
    "total_gbp": "300.00",
}


class RecordingDelivery:
    def __init__(self, failures=None):
        self.plans = []
        self.failures = failures or {}

    async def __call__(self, payload, prefs):
        self.plans.append(payload)
        return self.failures


@pytest.fixture
def travel(factory, monkeypatch):
    monkeypatch.setattr(chat_tools, "AsyncSessionLocal", factory)
    monkeypatch.setattr(plans_service, "AsyncSessionLocal", factory)
    delivery = RecordingDelivery()
    monkeypatch.setattr(chat_tools, "dispatch_plan", delivery)
    return delivery


async def _gated_resort(factory, passed=True):
    resort, run = make_resort(), make_run()
    await _seed(factory, resort, run, make_score(run, resort, passed=passed))
    return resort


def _tool(state, name, prefs=None):
    tools = chat_tools.create_chat_tools("sess", USER, state, prefs or default_preferences(USER))
    return next(t for t in tools if t.name == name)


def _plan_args(resort, flight=FLIGHT, hotel=HOTEL, outbound=OUT, back=BACK):
    return {
        "resort_id": str(resort.id),
        "outbound_date": outbound.isoformat(),
        "return_date": back.isoformat(),
        "flight_option": json.dumps(flight),
        "hotel_option": json.dumps(hotel),
        "notes": "quiet",
    }


def _state(**extra):
    return {"live_calls": 0, "plan_created": False, "flight_options": [FLIGHT], "hotel_options": [HOTEL], **extra}


async def test_plan_created_with_itemised_total_owner_and_delivery(factory, travel):
    resort = await _gated_resort(factory)
    result = await _tool(_state(), "create_plan_artifact").ainvoke(_plan_args(resort))

    assert result["total_estimated_gbp"] == "420.00"
    assert result["flight_total_gbp"] == "120.00" and result["hotel_total_gbp"] == "300.00"
    assert result["delivery_failures"] == {}
    async with factory() as session:
        plan = await session.get(TripPlan, uuid.UUID(result["plan_id"]))
    assert plan.user_id == USER and plan.status == "draft"
    assert plan.total_estimated_gbp == Decimal("420.00")
    assert travel.plans[0]["resort_name"] == "Kitzbuhel"
    assert "no booking" in result["message"].lower()


async def test_plan_delivery_failure_is_recorded_not_hidden(factory, monkeypatch, travel):
    failing = RecordingDelivery({"telegram": "not configured"})
    monkeypatch.setattr(chat_tools, "dispatch_plan", failing)
    resort = await _gated_resort(factory)
    result = await _tool(_state(), "create_plan_artifact").ainvoke(_plan_args(resort))

    assert result["delivery_failures"] == {"telegram": "not configured"}
    async with factory() as session:
        plan = await session.get(TripPlan, uuid.UUID(result["plan_id"]))
    assert plan.delivery_errors == {"telegram": "not configured"}


@pytest.mark.parametrize(
    "override, message",
    [
        ({"hotel": {**HOTEL, "total_gbp": "1.00"}}, "hotel selection"),
        ({"flight": {**FLIGHT, "total_gbp": "1.00"}}, "flight selection"),
        ({"back": BACK + timedelta(days=1)}, "Hotel stay dates"),
    ],
)
async def test_plan_rejects_options_not_from_this_session(factory, travel, override, message):
    resort = await _gated_resort(factory)
    with pytest.raises(ToolException, match=message):
        await _tool(_state(), "create_plan_artifact").ainvoke(_plan_args(resort, **override))
    assert travel.plans == []


async def test_plan_rejects_unpriced_selection(factory, travel):
    unpriced = {"id": "x", "name": "NoPrice", "checkin": OUT.isoformat(), "checkout": BACK.isoformat()}
    resort = await _gated_resort(factory)
    state = _state(hotel_options=[unpriced])
    with pytest.raises(ToolException, match="explicit GBP"):
        await _tool(state, "create_plan_artifact").ainvoke(_plan_args(resort, hotel=unpriced))


async def test_plan_requires_gate_passing_resort(factory, travel):
    resort = await _gated_resort(factory, passed=False)
    with pytest.raises(ToolException, match="snow gate"):
        await _tool(_state(), "create_plan_artifact").ainvoke(_plan_args(resort))


async def test_only_one_plan_per_session(factory, travel):
    resort = await _gated_resort(factory)
    state = _state()
    tool = _tool(state, "create_plan_artifact")
    await tool.ainvoke(_plan_args(resort))
    with pytest.raises(ToolException, match="Only one plan"):
        await tool.ainvoke(_plan_args(resort))
    assert len(travel.plans) == 1


async def test_flight_search_keeps_gbp_offers_adds_totals_and_excludes_airlines(
    factory, travel, monkeypatch
):
    monkeypatch.setattr(settings, "amadeus_client_id", "id")
    monkeypatch.setattr(settings, "amadeus_client_secret", "secret")
    seen = []

    def get(**kw):
        seen.append(kw)
        return _Resp(
            [
                {"id": "dear", "price": {"currency": "GBP", "grandTotal": "300.00"}},
                {"id": "eur", "price": {"currency": "EUR", "grandTotal": "10.00"}},
                {"id": "cheap", "price": {"currency": "GBP", "grandTotal": "99.00"}},
            ]
        )

    monkeypatch.setattr(
        chat_tools,
        "Client",
        lambda **kw: SimpleNamespace(
            shopping=SimpleNamespace(flight_offers_search=SimpleNamespace(get=get))
        ),
    )
    resort = await _gated_resort(factory)
    prefs = default_preferences(USER)
    prefs.excluded_airlines = ["FR", "U2"]
    state = {"live_calls": 0, "plan_created": False}
    result = await _tool(state, "search_flights_readonly", prefs).ainvoke(
        {
            "resort_id": str(resort.id),
            "outbound_date": OUT.isoformat(),
            "return_date": BACK.isoformat(),
            "destination_airports": ["INN"],
        }
    )

    assert [o["id"] for o in result["offers"]] == ["cheap", "dear"]
    assert result["offers"][0]["total_gbp"] == "99.00"
    assert result["origin_airport"] == "LHR"
    assert seen[0]["excludedAirlineCodes"] == "FR,U2"
    assert state["flight_options"] == result["offers"]


async def test_hotel_search_requires_gate_pass_and_stores_options(factory, travel, monkeypatch):
    monkeypatch.setattr(settings, "amadeus_client_id", "id")
    monkeypatch.setattr(settings, "amadeus_client_secret", "secret")
    client, _ = _fake_amadeus([{"hotelId": "A"}], [_hotel("A", "Alpha", ("GBP", "300"))])
    monkeypatch.setattr(chat_tools, "Client", lambda **kw: client)
    args = lambda r: {  # noqa: E731
        "resort_id": str(r.id),
        "checkin": OUT.isoformat(),
        "checkout": BACK.isoformat(),
    }

    blocked = await _gated_resort(factory, passed=False)
    with pytest.raises(ToolException, match="snow gate"):
        await _tool({"live_calls": 0, "plan_created": False}, "search_hotels_readonly").ainvoke(args(blocked))

    resort = await _gated_resort(factory)
    state = {"live_calls": 0, "plan_created": False}
    result = await _tool(state, "search_hotels_readonly").ainvoke(args(resort))
    assert result["hotels"][0]["name"] == "Alpha"
    assert state["hotel_options"] == result["hotels"]
    assert result["booking_available"] is False


# --- plans API -----------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    monkeypatch.setattr(settings, "api_bearer_token", "test-token")
    monkeypatch.setattr(plans_api, "AsyncSessionLocal", factory)
    app = FastAPI()
    app.include_router(plans_api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _plan(resort, user=USER, status="draft") -> TripPlan:
    return TripPlan(
        id=uuid.uuid4(), user_id=user, session_id="s", resort_id=resort.id,
        outbound_date=OUT, return_date=BACK, flight_option=FLIGHT, hotel_option=HOTEL,
        total_estimated_gbp=Decimal("420.00"), status=status,
    )


async def test_plans_scoped_to_owner_with_resort_name(client, factory):
    resort = make_resort()
    mine, theirs = _plan(resort), _plan(resort, user=OTHER)
    await _seed(factory, resort, mine, theirs)

    listed = (await client.get("/api/plans", headers=HEADERS)).json()
    assert [p["id"] for p in listed] == [str(mine.id)]
    assert listed[0]["resort_name"] == "Kitzbuhel"
    assert listed[0]["flight_total_gbp"] == "120.00"
    assert (await client.get(f"/api/plans/{mine.id}", headers=HEADERS)).status_code == 200
    assert (await client.get(f"/api/plans/{theirs.id}", headers=HEADERS)).status_code == 404
    assert (await client.delete(f"/api/plans/{theirs.id}", headers=HEADERS)).status_code == 404
    assert (await client.get("/api/plans")).status_code == 401


async def test_cancel_plan_and_refuse_booked(client, factory):
    resort = make_resort()
    draft, booked = _plan(resort), _plan(resort, status="booked")
    await _seed(factory, resort, draft, booked)

    cancelled = await client.delete(f"/api/plans/{draft.id}", headers=HEADERS)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (await client.delete(f"/api/plans/{booked.id}", headers=HEADERS)).status_code == 409
    async with factory() as session:
        assert (await session.get(TripPlan, booked.id)).status == "booked"


# --- delivery ------------------------------------------------------------------------

PAYLOAD = {
    "id": "p1", "resort_name": "Kitzbuhel", "outbound_date": "2026-12-04",
    "return_date": "2026-12-06", "flight_total_gbp": "120.00",
    "hotel_total_gbp": "300.00", "total_estimated_gbp": "420.00",
}


class FakeBot:
    sent = []

    def __init__(self, token):
        pass

    async def send_message(self, chat_id, text):
        FakeBot.sent.append((chat_id, text))


async def test_telegram_plan_uses_preference_chat_and_states_draft(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "tok")
    monkeypatch.setattr(settings, "telegram_chat_id", "default-chat")
    FakeBot.sent = []
    telegram = TelegramDelivery(bot_factory=FakeBot)

    assert await telegram.send_plan(PAYLOAD, "999")
    assert await telegram.send_plan(PAYLOAD)
    assert [chat for chat, _ in FakeBot.sent] == ["999", "default-chat"]
    text = FakeBot.sent[0][1]
    assert "£420.00" in text and "No booking or payment has been made" in text
    assert "Kitzbuhel" in format_plan_text(PAYLOAD)


async def test_email_plan_escapes_content_and_reports_missing_config(monkeypatch):
    sent = []

    class FakeClient:
        def __init__(self, api_key):
            pass

        def send(self, mail):
            sent.append(mail)
            return SimpleNamespace(status_code=202)

    monkeypatch.setattr(settings, "sendgrid_api_key", "key")
    monkeypatch.setattr(settings, "delivery_email_address", "")
    email = SendGridDelivery(client_factory=FakeClient)

    assert not await email.send_plan(PAYLOAD)
    assert "not configured" in email.last_error
    assert await email.send_plan({**PAYLOAD, "resort_name": "<b>X</b>"}, "me@example.com")
    assert "&lt;b&gt;X&lt;/b&gt;" in str(sent[0].get())


async def test_dispatch_plan_reports_each_failing_channel():
    class Failing:
        last_error = "down"

        async def send_plan(self, *args):
            return False

    class Working:
        last_error = None

        async def send_plan(self, *args):
            return True

    prefs = default_preferences(USER)
    assert await dispatch_plan(PAYLOAD, prefs, Failing(), Working()) == {"telegram": "down"}
    assert await dispatch_plan(PAYLOAD, prefs, Working(), Failing()) == {"email": "down"}
    assert await dispatch_plan(PAYLOAD, prefs, Working(), Working()) == {}


# --- chat stream events --------------------------------------------------------------


async def _collect(session, chunk):
    return [item async for item in session._events_for(chunk, USER)]


def _chat_session(loader):
    return ChatSession(
        memory_system=SimpleNamespace(),
        prefs_service=SimpleNamespace(),
        store=SimpleNamespace(),
        plan_loader=loader,
    )


async def test_stream_emits_plan_card_tool_calls_and_never_leaks_tool_output():
    plan_id = uuid.uuid4()

    async def loader(pid, user):
        return {"id": str(pid), "resort_name": "Kitzbuhel"} if user == USER else None

    session = _chat_session(loader)
    card = await _collect(
        session,
        ToolMessage(content=json.dumps({"plan_id": str(plan_id)}), name="create_plan_artifact", tool_call_id="1"),
    )
    assert card == [{"type": "plan_card", "plan": {"id": str(plan_id), "resort_name": "Kitzbuhel"}}]

    other_tool = await _collect(
        session, ToolMessage(content='{"secret": 1}', name="fetch_live_snow", tool_call_id="2")
    )
    errored = await _collect(
        session,
        ToolMessage(content="boom", name="create_plan_artifact", tool_call_id="3", status="error"),
    )
    assert other_tool == [] and errored == []

    call = AIMessageChunk(
        content="",
        tool_call_chunks=[{"name": "fetch_live_snow", "args": "", "id": "c", "index": 0}],
    )
    assert await _collect(session, call) == [{"type": "tool_call", "name": "fetch_live_snow"}]
    assert await _collect(session, AIMessageChunk(content="Hi")) == ["Hi"]
